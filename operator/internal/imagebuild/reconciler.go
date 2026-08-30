/*
Package imagebuild reconciles GShareImageBuild resources: one kaniko Job per requested build.

Flow: Pending -> write the Dockerfile ConfigMap + create the kaniko Job in the build namespace
(gshare-infra; the sessions namespace enforces restricted PSS, which kaniko's root user cannot
satisfy) -> Running (reported to the control plane) -> on Job completion, recover the kaniko log
tail from the pod, report succeeded/failed to POST /internal/image-builds/{id}/status, and clean
up the ConfigMap (the Job itself is TTL-collected).

The CR carries no credentials; pushes rely on the registry being open from the cluster (or a
mounted push secret in a later iteration). The operator never touches the image catalog — the
control plane mints the Image row when it receives the succeeded callback.
*/
package imagebuild

import (
	"context"
	"fmt"
	"sort"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes"
	"k8s.io/utils/ptr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	gsharev1 "github.com/gshare/operator/api/v1"
	"github.com/gshare/operator/internal/sot"
)

// +kubebuilder:rbac:groups=gshare.io,resources=gshareimagebuilds,verbs=get;list;watch;update;patch
// +kubebuilder:rbac:groups=gshare.io,resources=gshareimagebuilds/status,verbs=get;update;patch

// BuildReporter is the narrow SoT surface this controller needs (fake-able in tests).
type BuildReporter interface {
	ReportImageBuild(ctx context.Context, buildID string, ev sot.ImageBuildStatusEvent) error
}

// Reconciler executes GShareImageBuild requests through kaniko Jobs.
type Reconciler struct {
	client.Client
	// KanikoImage is the kaniko executor image; empty disables the controller (builds Fail fast).
	KanikoImage string
	// Namespace is where build Jobs run (gshare-infra — not PSS-restricted).
	Namespace string
	// ServiceAccount for the build Job (no API permissions needed; kaniko only pushes).
	ServiceAccount string
	// InsecureRegistry adds --insecure/--skip-tls-verify for plain-HTTP registries.
	InsecureRegistry bool
	// SoT reports build progress to the control plane.
	SoT BuildReporter
	// ReadPodLog returns the tail of a pod's main container log (wired from a clientset in main;
	// nil in envtest, where no kubelet serves logs).
	ReadPodLog func(ctx context.Context, namespace, pod string, tailLines int64) (string, error)
}

const pollInterval = 10 * time.Second

func (r *Reconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := ctrl.LoggerFrom(ctx)
	var ib gsharev1.GShareImageBuild
	if err := r.Get(ctx, req.NamespacedName, &ib); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	if ib.Status.Phase == "Succeeded" || ib.Status.Phase == "Failed" {
		if !ib.Status.Reported {
			return ctrl.Result{}, r.report(ctx, &ib, "")
		}
		return ctrl.Result{}, nil
	}
	if r.KanikoImage == "" {
		// Not configured: fail explicitly — a Pending build would sit "queued" in the console forever.
		ib.Status.Phase = "Failed"
		ib.Status.Message = "kaniko image not configured (operator --kaniko-image / operator.kanikoImage)"
		if err := r.Status().Update(ctx, &ib); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, r.report(ctx, &ib, "")
	}

	jobName := "gshare-build-" + ib.Name
	if ib.Status.JobRef == "" {
		if ib.Spec.Source == "dockerfile" {
			if err := r.ensureDockerfileConfigMap(ctx, jobName, &ib); err != nil {
				return ctrl.Result{}, err
			}
		}
		job := r.buildJob(jobName, &ib)
		if err := r.Create(ctx, job); err != nil && !apierrors.IsAlreadyExists(err) {
			return ctrl.Result{}, err
		}
		ib.Status.Phase = "Running"
		ib.Status.JobRef = r.Namespace + "/" + jobName
		if err := r.Status().Update(ctx, &ib); err != nil {
			return ctrl.Result{}, err
		}
		_ = r.SoT.ReportImageBuild(ctx, ib.Spec.BuildId,
			sot.ImageBuildStatusEvent{Phase: "running"})
		logger.Info("kaniko job created", "build", ib.Spec.BuildId, "target", ib.Spec.TargetRef)
		return ctrl.Result{RequeueAfter: pollInterval}, nil
	}

	var job batchv1.Job
	if err := r.Get(ctx, types.NamespacedName{Namespace: r.Namespace, Name: jobName}, &job); err != nil {
		if apierrors.IsNotFound(err) {
			ib.Status.Phase = "Failed"
			ib.Status.Message = "build job disappeared before completion was observed"
			if err := r.Status().Update(ctx, &ib); err != nil {
				return ctrl.Result{}, err
			}
			return ctrl.Result{}, r.report(ctx, &ib, "")
		}
		return ctrl.Result{}, err
	}
	switch {
	case job.Status.Succeeded > 0:
		logTail := r.podLogTail(ctx, jobName)
		ib.Status.Phase = "Succeeded"
		ib.Status.Message = ""
		if err := r.Status().Update(ctx, &ib); err != nil {
			return ctrl.Result{}, err
		}
		r.cleanup(ctx, jobName)
		return ctrl.Result{}, r.report(ctx, &ib, logTail)
	case job.Status.Failed > 0:
		logTail := r.podLogTail(ctx, jobName)
		ib.Status.Phase = "Failed"
		ib.Status.Message = fmt.Sprintf("kaniko job failed (%d attempts)", job.Status.Failed)
		if err := r.Status().Update(ctx, &ib); err != nil {
			return ctrl.Result{}, err
		}
		r.cleanup(ctx, jobName)
		return ctrl.Result{}, r.report(ctx, &ib, logTail)
	}
	return ctrl.Result{RequeueAfter: pollInterval}, nil // running; the Job is in another namespace, Owns() cannot watch it
}

// report delivers the terminal (or config-failure) callback once and marks Reported.
func (r *Reconciler) report(ctx context.Context, ib *gsharev1.GShareImageBuild, logTail string) error {
	ev := sot.ImageBuildStatusEvent{LogTail: logTail}
	switch ib.Status.Phase {
	case "Succeeded":
		ev.Phase = "succeeded"
		ev.ImageRef = ib.Spec.TargetRef
	case "Failed":
		ev.Phase = "failed"
		ev.Error = ib.Status.Message
	default:
		return nil
	}
	if err := r.SoT.ReportImageBuild(ctx, ib.Spec.BuildId, ev); err != nil {
		return err // retry via the normal requeue-on-error path
	}
	ib.Status.Reported = true
	return r.Status().Update(ctx, ib)
}

// podLogTail best-effort recovers the kaniko log tail for the console's log drawer.
func (r *Reconciler) podLogTail(ctx context.Context, jobName string) string {
	if r.ReadPodLog == nil {
		return ""
	}
	var pods corev1.PodList
	if err := r.List(ctx, &pods, client.InNamespace(r.Namespace),
		client.MatchingLabels{"job-name": jobName}); err != nil || len(pods.Items) == 0 {
		return ""
	}
	sort.Slice(pods.Items, func(i, j int) bool { // newest attempt last
		return pods.Items[i].CreationTimestamp.Before(&pods.Items[j].CreationTimestamp)
	})
	tail, err := r.ReadPodLog(ctx, r.Namespace, pods.Items[len(pods.Items)-1].Name, 200)
	if err != nil {
		return ""
	}
	return tail
}

func (r *Reconciler) cleanup(ctx context.Context, jobName string) {
	// The Job TTL-collects itself; the Dockerfile ConfigMap is ours to remove.
	cm := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Namespace: r.Namespace, Name: jobName}}
	_ = r.Delete(ctx, cm)
}

func (r *Reconciler) ensureDockerfileConfigMap(ctx context.Context, name string, ib *gsharev1.GShareImageBuild) error {
	cm := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{Namespace: r.Namespace, Name: name},
		Data:       map[string]string{"Dockerfile": ib.Spec.Dockerfile},
	}
	if err := r.Create(ctx, cm); err != nil && !apierrors.IsAlreadyExists(err) {
		return err
	}
	return nil
}

func (r *Reconciler) buildJob(name string, ib *gsharev1.GShareImageBuild) *batchv1.Job {
	args := []string{
		"--destination=" + ib.Spec.TargetRef,
		"--snapshot-mode=redo",
		"--log-format=text",
	}
	if r.InsecureRegistry {
		args = append(args, "--insecure", "--insecure-pull", "--skip-tls-verify")
	}
	switch ib.Spec.Source {
	case "git":
		ref := ib.Spec.GitRef
		if ref == "" {
			ref = "main"
		}
		args = append(args, "--context=git://"+trimScheme(ib.Spec.GitUrl)+"#refs/heads/"+ref)
		if sub := ib.Spec.ContextSubPath; sub != "" && sub != "." {
			args = append(args, "--context-sub-path="+sub)
		}
	default: // dockerfile
		args = append(args, "--context=dir:///workspace", "--dockerfile=/workspace/Dockerfile")
	}
	// deterministic order for tests
	keys := make([]string, 0, len(ib.Spec.BuildArgs))
	for k := range ib.Spec.BuildArgs {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		args = append(args, fmt.Sprintf("--build-arg=%s=%s", k, ib.Spec.BuildArgs[k]))
	}

	pod := corev1.PodSpec{
		RestartPolicy:      corev1.RestartPolicyNever,
		ServiceAccountName: r.ServiceAccount,
		// kaniko is unprivileged but runs as root inside its container — which is exactly why the
		// build namespace is gshare-infra, not the restricted sessions namespace.
		Containers: []corev1.Container{{
			Name:  "kaniko",
			Image: r.KanikoImage,
			Args:  args,
			Resources: corev1.ResourceRequirements{
				Requests: corev1.ResourceList{
					corev1.ResourceCPU:    resource.MustParse("500m"),
					corev1.ResourceMemory: resource.MustParse("1Gi"),
				},
				Limits: corev1.ResourceList{
					corev1.ResourceCPU:              resource.MustParse("2"),
					corev1.ResourceMemory:           resource.MustParse("4Gi"),
					corev1.ResourceEphemeralStorage: resource.MustParse("20Gi"),
				},
			},
		}},
	}
	if ib.Spec.Source == "dockerfile" {
		pod.Containers[0].VolumeMounts = []corev1.VolumeMount{{Name: "workspace", MountPath: "/workspace"}}
		pod.Volumes = []corev1.Volume{{
			Name: "workspace",
			VolumeSource: corev1.VolumeSource{
				ConfigMap: &corev1.ConfigMapVolumeSource{
					LocalObjectReference: corev1.LocalObjectReference{Name: name},
				},
			},
		}}
	}
	return &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{Namespace: r.Namespace, Name: name,
			Labels: map[string]string{"gshare.io/image-build": ib.Name}},
		Spec: batchv1.JobSpec{
			BackoffLimit:            ptr.To[int32](0), // user feedback beats blind retries
			ActiveDeadlineSeconds:   ptr.To[int64](1800),
			TTLSecondsAfterFinished: ptr.To[int32](3600),
			Template:                corev1.PodTemplateSpec{Spec: pod},
		},
	}
}

func trimScheme(u string) string {
	for _, p := range []string{"https://", "http://"} {
		if len(u) > len(p) && u[:len(p)] == p {
			return u[len(p):]
		}
	}
	return u
}

// SetupWithManager registers the controller.
func (r *Reconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&gsharev1.GShareImageBuild{}).
		Complete(r)
}

// ClientsetLogReader adapts a kubernetes clientset into the ReadPodLog hook.
func ClientsetLogReader(cs kubernetes.Interface) func(ctx context.Context, namespace, pod string, tailLines int64) (string, error) {
	return func(ctx context.Context, namespace, pod string, tailLines int64) (string, error) {
		raw, err := cs.CoreV1().Pods(namespace).GetLogs(pod, &corev1.PodLogOptions{
			Container: "kaniko", TailLines: ptr.To(tailLines),
		}).Do(ctx).Raw()
		if err != nil {
			return "", err
		}
		return string(raw), nil
	}
}
