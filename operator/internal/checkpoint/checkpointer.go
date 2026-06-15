/*
Package checkpoint implements lossless-pause via a privileged node agent Job.

It runs the gshare-lossless-agent (cuda-checkpoint + criu) as a privileged, hostPID Job pinned to the
session Pod's node. The checkpoint is containerd-native (`ctr -n k8s.io c checkpoint --task --rw`):
the host containerd (via host runc+criu) handles container netns/rootfs/mounts, and the agent only
runs cuda-checkpoint (GPU VRAM, hostPID) and drives `ctr` over the mounted containerd socket. criu
therefore lives on the NODE (label gshare.io/criu=ready), not in the agent image. The Job runs in a
privileged namespace (default gshare-infra) — session pods stay PSA restricted.

Restore is unsupported: it requires kubelet/CRI checkpoint-restore (the restore must land in a
kubelet-created sandbox netns); see docs/paper/lossless-pause.md. The type satisfies
controller.Checkpointer structurally (no import cycle).
*/
package checkpoint

import (
	"context"
	"fmt"
	"strings"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/wait"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"

	gsharev1 "github.com/gshare/operator/api/v1"
)

// JobCheckpointer drives the node agent via privileged Jobs.
type JobCheckpointer struct {
	client.Client
	AgentImage     string // gshare-lossless-agent image (criu + cuda-checkpoint)
	Namespace      string // privileged ns for the agent Job (e.g. gshare-infra)
	ServiceAccount string // privileged SA in Namespace
	CheckpointPVC  string // RWX PVC holding checkpoints/<session>/
	Timeout        time.Duration
}

func (c *JobCheckpointer) dir(s *gsharev1.GShareSession) string { return c.dirByName(s.Name) }
func (c *JobCheckpointer) dirByName(name string) string         { return "/ckpt/" + name }

// ckptVolume: shared checkpoint store mounted at /ckpt. PVC if configured (cross-node restore on RWX),
// else node-local hostPath (same-node restore; checkpoint+restore run on the session's node).
func (c *JobCheckpointer) ckptVolume() corev1.Volume {
	if c.CheckpointPVC != "" {
		return corev1.Volume{Name: "ckpt", VolumeSource: corev1.VolumeSource{
			PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: c.CheckpointPVC},
		}}
	}
	hpt := corev1.HostPathDirectoryOrCreate
	return corev1.Volume{Name: "ckpt", VolumeSource: corev1.VolumeSource{
		HostPath: &corev1.HostPathVolumeSource{Path: "/var/lib/gshare/checkpoints", Type: &hpt},
	}}
}

// Checkpoint runs `agent checkpoint` on the Pod's node. Returns the checkpoint ref (PVC-relative
// path) on success. The caller deletes the Pod afterward to free the GPU.
func (c *JobCheckpointer) Checkpoint(ctx context.Context, s *gsharev1.GShareSession, pod *corev1.Pod) (string, error) {
	if s.Status.BoundGpuUuid == "" {
		return "", fmt.Errorf("no bound GPU uuid on status; lossless checkpoint needs an exclusive bound GPU")
	}
	cid := containerID(pod, mainContainerName)
	if cid == "" {
		return "", fmt.Errorf("no running %q container id on pod %s; cannot checkpoint", mainContainerName, pod.Name)
	}
	args := []string{
		"checkpoint",
		"--gpu-uuid", s.Status.BoundGpuUuid,
		"--container-id", cid,
		"--dir", c.dir(s),
	}
	if err := c.runJob(ctx, s.Name, pod.Spec.NodeName, "ckpt", args); err != nil {
		return "", err
	}
	// Ref is the node content-store named OCI image (agent names it basename(dir)=s.Name).
	return "localhost/gshare-ckpt/" + s.Name + ":latest", nil
}

// Yield performs in-place GPU yield: the agent evicts VRAM (cuda-checkpoint) on the Pod's node while
// the Pod/process stay ALIVE, freeing the physical card. The caller does NOT delete the Pod. Resume
// toggles VRAM back into the same process (lossless). (docs/paper/manuscript, §Design)
func (c *JobCheckpointer) Yield(ctx context.Context, s *gsharev1.GShareSession, pod *corev1.Pod) error {
	if s.Status.BoundGpuUuid == "" {
		return fmt.Errorf("no bound GPU uuid on status; yield needs an exclusive bound GPU")
	}
	args := []string{"yield", "--gpu-uuid", s.Status.BoundGpuUuid, "--dir", c.dir(s)}
	return c.runJob(ctx, s.Name, pod.Spec.NodeName, "yield", args)
}

// Resume restores a yielded session's VRAM into its still-running process (cuda-checkpoint toggle
// back) on the node holding it. No Pod recreation.
func (c *JobCheckpointer) Resume(ctx context.Context, s *gsharev1.GShareSession, nodeName string) error {
	args := []string{"resume", "--dir", c.dir(s)}
	return c.runJob(ctx, s.Name, nodeName, "resume", args)
}

// Cleanup reclaims the node-local checkpoint (content-store named image + shared dir) on the node
// that holds it (status.CheckpointNode). Fire-and-forget: it creates a privileged agent Job and
// does NOT wait — the Job self-removes via TTLSecondsAfterFinished. Idempotent (the agent's
// `cleanup` no-ops when the image/dir are already gone). No-op when nodeName is empty.
func (c *JobCheckpointer) Cleanup(ctx context.Context, sessionName, nodeName string) error {
	if nodeName == "" {
		return nil
	}
	name := "cleanup-" + sessionName
	job := c.buildJob(name, nodeName, []string{"cleanup", "--dir", c.dirByName(sessionName)})
	_ = c.Delete(ctx, job, client.PropagationPolicy(metav1.DeletePropagationBackground)) // clear stale
	if err := c.Create(ctx, job); err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("create cleanup job: %w", err)
	}
	return nil
}

// mainContainerName is the session pod's workload container (podbuilder.go).
const mainContainerName = "session"

// containerID returns the containerd container id (scheme stripped) of the named container,
// e.g. "containerd://<64hex>" -> "<64hex>". Empty if the container has not reported one yet.
func containerID(pod *corev1.Pod, name string) string {
	for _, cs := range pod.Status.ContainerStatuses {
		if cs.Name == name && cs.ContainerID != "" {
			if i := strings.Index(cs.ContainerID, "://"); i >= 0 {
				return cs.ContainerID[i+3:]
			}
			return cs.ContainerID
		}
	}
	return ""
}

// Restore is intentionally unimplemented: criu restore must run inside a kubelet-owned sandbox netns,
// so operator/CLI-level restore is structurally impossible — only kubelet/CRI checkpoint-restore can
// realize it (docs/paper/lossless-pause.md). Until then resume is cold; progress is preserved by
// app-level checkpointing.

func (c *JobCheckpointer) runJob(ctx context.Context, sessionName, nodeName, kind string, args []string) error {
	if nodeName == "" {
		return fmt.Errorf("%s: empty node name", kind)
	}
	name := kind + "-" + sessionName
	job := c.buildJob(name, nodeName, args)
	_ = c.Delete(ctx, job) // idempotent: clear a stale Job from a prior attempt
	if err := c.Create(ctx, job); err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("create %s job: %w", kind, err)
	}

	timeout := c.Timeout
	if timeout == 0 {
		timeout = 10 * time.Minute
	}
	err := wait.PollUntilContextTimeout(ctx, 3*time.Second, timeout, true, func(ctx context.Context) (bool, error) {
		var j batchv1.Job
		if err := c.Get(ctx, types.NamespacedName{Namespace: c.Namespace, Name: name}, &j); err != nil {
			return false, err
		}
		if j.Status.Succeeded > 0 {
			return true, nil
		}
		if j.Status.Failed > 0 {
			return false, fmt.Errorf("%s job failed", kind)
		}
		return false, nil
	})
	// Clean up the Job (and its Pod via propagation) regardless of outcome.
	_ = c.Delete(ctx, job, client.PropagationPolicy(metav1.DeletePropagationBackground))
	return err
}

func (c *JobCheckpointer) buildJob(name, nodeName string, args []string) *batchv1.Job {
	return &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{Namespace: c.Namespace, Name: name},
		Spec: batchv1.JobSpec{
			BackoffLimit:            ptr.To[int32](1),
			TTLSecondsAfterFinished: ptr.To[int32](300),
			Template: corev1.PodTemplateSpec{
				Spec: corev1.PodSpec{
					RestartPolicy: corev1.RestartPolicyNever,
					NodeName:      nodeName,
					HostPID:       true,
					// nvidia runtime injects driver + nvidia-smi so the agent can run cuda-checkpoint
					// against existing GPU processes. No GPU slot requested (targets running procs).
					RuntimeClassName:   ptr.To("nvidia"),
					ServiceAccountName: c.ServiceAccount,
					Containers: []corev1.Container{{
						Name:            "agent",
						Image:           c.AgentImage,
						Args:            args,
						SecurityContext: &corev1.SecurityContext{Privileged: ptr.To(true)},
						Env: []corev1.EnvVar{
							{Name: "NVIDIA_VISIBLE_DEVICES", Value: "all"},
							{Name: "NVIDIA_DRIVER_CAPABILITIES", Value: "all"},
						},
						VolumeMounts: []corev1.VolumeMount{
							{Name: "ckpt", MountPath: "/ckpt"},
							{Name: "dev", MountPath: "/dev"},
							// host containerd socket — agent checkpoints the container via ctr.
							{Name: "containerd", MountPath: "/run/containerd"},
						},
					}},
					Volumes: []corev1.Volume{
						c.ckptVolume(),
						{Name: "dev", VolumeSource: corev1.VolumeSource{
							HostPath: &corev1.HostPathVolumeSource{Path: "/dev"},
						}},
						{Name: "containerd", VolumeSource: corev1.VolumeSource{
							HostPath: &corev1.HostPathVolumeSource{Path: "/run/containerd"},
						}},
					},
				},
			},
		},
	}
}
