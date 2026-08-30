/*
Package migagent reconciles GpuModeChange resources: one privileged node Job per requested
card-mode transition (hami-core <-> mig), following the checkpointer's Job pattern.

Flow: Pending -> create the agent Job (nvidia-smi -mig on the target index after asserting the
card is process-free) -> Running -> on Job success, delete the HAMi device-plugin pod on the node
so the register annotation refreshes with the card's new per-card mode -> Succeeded. The control
plane confirms through its normal inventory sync and only then returns the card to service.
*/
package migagent

import (
	"context"
	"fmt"
	"strconv"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	gsharev1 "github.com/gshare/operator/api/v1"
)

// DevicePluginLabelSelector identifies the HAMi device-plugin pods (restarted after a change so
// the hami.io/node-nvidia-register annotation reflects the card's new mode).
const DevicePluginLabelSelector = "app.kubernetes.io/component=hami-device-plugin"

// +kubebuilder:rbac:groups=gshare.io,resources=gpumodechanges,verbs=get;list;watch;update;patch
// +kubebuilder:rbac:groups=gshare.io,resources=gpumodechanges/status,verbs=get;update;patch

// Reconciler executes GpuModeChange requests through privileged node Jobs.
type Reconciler struct {
	client.Client
	// AgentImage is the mig-agent image; empty disables the controller (mode changes stay Pending).
	AgentImage string
	// Namespace is the privileged namespace the agent Jobs run in (gshare-infra).
	Namespace string
	// ServiceAccount for the agent Job.
	ServiceAccount string
	// DevicePluginNamespace is where the HAMi device plugin lives (kube-system).
	DevicePluginNamespace string
}

func (r *Reconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := ctrl.LoggerFrom(ctx)
	var mc gsharev1.GpuModeChange
	if err := r.Get(ctx, req.NamespacedName, &mc); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	if mc.Status.Phase == "Succeeded" || mc.Status.Phase == "Failed" {
		return ctrl.Result{}, nil
	}
	if r.AgentImage == "" {
		// Feature not configured: fail the change explicitly. Leaving it Pending would hold the
		// card in mode_state=applying forever on the control-plane side — the rebalancer only
		// recovers on a terminal phase.
		mc.Status.Phase = "Failed"
		mc.Status.Message = "mig agent image not configured (operator --mig-agent-image / operator.migAgentImage)"
		if err := r.Status().Update(ctx, &mc); err != nil {
			return ctrl.Result{}, err
		}
		logger.Info("gpumodechange failed: no mig agent image configured", "name", mc.Name)
		return ctrl.Result{}, nil
	}

	jobName := "gshare-migagent-" + mc.Name
	if mc.Status.JobRef == "" {
		job := r.buildJob(jobName, &mc)
		if err := r.Create(ctx, job); err != nil && !apierrors.IsAlreadyExists(err) {
			return ctrl.Result{}, err
		}
		mc.Status.Phase = "Running"
		mc.Status.JobRef = r.Namespace + "/" + jobName
		if err := r.Status().Update(ctx, &mc); err != nil {
			return ctrl.Result{}, err
		}
		logger.Info("mig-agent job created", "change", mc.Name, "node", mc.Spec.NodeName,
			"gpu", mc.Spec.GpuUuid, "target", mc.Spec.TargetMode)
		return ctrl.Result{RequeueAfter: pollInterval}, nil
	}

	var job batchv1.Job
	if err := r.Get(ctx, types.NamespacedName{Namespace: r.Namespace, Name: jobName}, &job); err != nil {
		if apierrors.IsNotFound(err) {
			// TTL collected a finished Job before we observed it; report failure so the control
			// plane retries explicitly rather than waiting forever.
			mc.Status.Phase = "Failed"
			mc.Status.Message = "agent job disappeared before completion was observed"
			return ctrl.Result{}, r.Status().Update(ctx, &mc)
		}
		return ctrl.Result{}, err
	}
	switch {
	case job.Status.Succeeded > 0:
		// Refresh HAMi's view of the node: delete its device-plugin pod (the DaemonSet recreates
		// it) so the register annotation reports the card's new mode.
		if err := r.restartDevicePlugin(ctx, mc.Spec.NodeName); err != nil {
			logger.Error(err, "device-plugin restart failed; inventory will refresh on its own cadence")
		}
		mc.Status.Phase = "Succeeded"
		mc.Status.Message = ""
		return ctrl.Result{}, r.Status().Update(ctx, &mc)
	case job.Status.Failed > 0:
		mc.Status.Phase = "Failed"
		mc.Status.Message = fmt.Sprintf("agent job failed (%d attempts)", job.Status.Failed)
		return ctrl.Result{}, r.Status().Update(ctx, &mc)
	}
	return ctrl.Result{RequeueAfter: pollInterval}, nil // still running; poll (the Job lives in another namespace, so Owns() cannot watch it)
}

func (r *Reconciler) restartDevicePlugin(ctx context.Context, nodeName string) error {
	var pods corev1.PodList
	if err := r.List(ctx, &pods,
		client.InNamespace(r.DevicePluginNamespace),
		client.MatchingFields{"spec.nodeName": nodeName},
	); err != nil {
		return err
	}
	for i := range pods.Items {
		p := &pods.Items[i]
		if p.Labels["app.kubernetes.io/component"] != "hami-device-plugin" {
			continue
		}
		if err := r.Delete(ctx, p); err != nil && !apierrors.IsNotFound(err) {
			return err
		}
	}
	return nil
}

func (r *Reconciler) buildJob(name string, mc *gsharev1.GpuModeChange) *batchv1.Job {
	return &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{Namespace: r.Namespace, Name: name},
		Spec: batchv1.JobSpec{
			BackoffLimit:            ptr.To[int32](0), // a geometry change must not blindly retry
			TTLSecondsAfterFinished: ptr.To[int32](3600),
			Template: corev1.PodTemplateSpec{
				Spec: corev1.PodSpec{
					RestartPolicy: corev1.RestartPolicyNever,
					NodeName:      mc.Spec.NodeName,
					HostPID:       true,
					// nvidia runtime injects the driver + nvidia-smi; no GPU slot is requested.
					RuntimeClassName:   ptr.To("nvidia"),
					ServiceAccountName: r.ServiceAccount,
					Containers: []corev1.Container{{
						Name:            "agent",
						Image:           r.AgentImage,
						SecurityContext: &corev1.SecurityContext{Privileged: ptr.To(true)},
						Env: []corev1.EnvVar{
							{Name: "NVIDIA_VISIBLE_DEVICES", Value: "all"},
							{Name: "NVIDIA_DRIVER_CAPABILITIES", Value: "all"},
							{Name: "GPU_INDEX", Value: strconv.Itoa(mc.Spec.GpuIndex)},
							{Name: "GPU_UUID", Value: mc.Spec.GpuUuid},
							{Name: "TARGET_MODE", Value: mc.Spec.TargetMode},
						},
						VolumeMounts: []corev1.VolumeMount{
							{Name: "dev", MountPath: "/dev"},
						},
					}},
					Volumes: []corev1.Volume{
						{Name: "dev", VolumeSource: corev1.VolumeSource{
							HostPath: &corev1.HostPathVolumeSource{Path: "/dev"},
						}},
					},
				},
			},
		},
	}
}

// pollInterval paces Job-completion checks (the Job lives in the privileged namespace while the
// GpuModeChange lives in the system namespace, so an Owns() watch cannot connect them).
const pollInterval = 10 * time.Second

// SetupWithManager registers the controller.
func (r *Reconciler) SetupWithManager(mgr ctrl.Manager) error {
	// Index pods by nodeName so restartDevicePlugin can list per node.
	if err := mgr.GetFieldIndexer().IndexField(
		context.Background(), &corev1.Pod{}, "spec.nodeName",
		func(o client.Object) []string { return []string{o.(*corev1.Pod).Spec.NodeName} },
	); err != nil {
		return err
	}
	return ctrl.NewControllerManagedBy(mgr).
		For(&gsharev1.GpuModeChange{}).
		Complete(r)
}
