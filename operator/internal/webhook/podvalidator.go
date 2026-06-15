package webhook

import (
	"context"
	"net/http"
	"strings"

	corev1 "k8s.io/api/core/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/webhook/admission"

	gsharev1 "github.com/gshare/operator/api/v1"
)

// gpuResource is the device-plugin extended resource. A borrow/bypass Pod requests none.
const gpuResource = "nvidia.com/gpu"

// PodLendGuard validates that a Pod pinning a GPU via the device-plugin bypass
// (NVIDIA_VISIBLE_DEVICES=<uuid>, no nvidia.com/gpu) targets a card that is held by a
// yielded/lent resident session and not already lent (one bypass Pod per card).
type PodLendGuard struct {
	Client           client.Client
	Decoder          admission.Decoder
	SessionNamespace string // namespace where GShareSessions live (lend ledger source)
}

// Handle admits non-bypass Pods unchanged; bypass Pods are checked against the lend invariant.
func (g *PodLendGuard) Handle(ctx context.Context, req admission.Request) admission.Response {
	pod := &corev1.Pod{}
	if err := g.Decoder.Decode(req, pod); err != nil {
		return admission.Errored(http.StatusBadRequest, err)
	}

	uuid := bypassGPUUUID(pod)
	if uuid == "" {
		return admission.Allowed("not a GPU device-plugin-bypass Pod")
	}

	var list gsharev1.GShareSessionList
	if err := g.Client.List(ctx, &list, client.InNamespace(g.SessionNamespace)); err != nil {
		// Fail-open on a transient list error: the backend ledger is the primary enforcement.
		return admission.Allowed("lend ledger unavailable; deferring to backend ledger")
	}

	owningSession := controllerSessionName(pod)
	residentYielded, otherSpots := false, 0
	for i := range list.Items {
		s := &list.Items[i]
		if s.Status.BoundGpuUuid == uuid &&
			(s.Status.YieldState == "Yielded" || s.Status.YieldState == "Lent") {
			residentYielded = true
		}
		if s.Spec.BorrowedGpuUuid == uuid && s.Name != owningSession && s.DeletionTimestamp.IsZero() {
			otherSpots++
		}
	}

	if !residentYielded {
		return admission.Denied("GPU " + uuid + " is not a yielded/lendable card (no yielded resident holds it)")
	}
	if otherSpots > 0 {
		return admission.Denied("GPU " + uuid + " is already lent to another spot session (one bypass Pod per card)")
	}
	return admission.Allowed("lend invariant satisfied")
}

// bypassGPUUUID returns the pinned GPU UUID when the Pod is a device-plugin-bypass GPU Pod
// (NVIDIA_VISIBLE_DEVICES set to a GPU-UUID and no nvidia.com/gpu request), else "".
func bypassGPUUUID(pod *corev1.Pod) string {
	for i := range pod.Spec.Containers {
		c := &pod.Spec.Containers[i]
		if _, ok := c.Resources.Limits[gpuResource]; ok {
			return "" // device-plugin allocation, not a bypass Pod
		}
		if _, ok := c.Resources.Requests[gpuResource]; ok {
			return ""
		}
	}
	for i := range pod.Spec.Containers {
		for _, e := range pod.Spec.Containers[i].Env {
			if e.Name != "NVIDIA_VISIBLE_DEVICES" {
				continue
			}
			v := strings.TrimSpace(e.Value)
			// Only a concrete GPU-UUID pin is a bypass card claim ("all"/"none"/index are not).
			if strings.HasPrefix(v, "GPU-") {
				if i := strings.IndexByte(v, ','); i >= 0 {
					v = v[:i] // first of a comma list
				}
				return v
			}
		}
	}
	return ""
}

// controllerSessionName returns the owning GShareSession name from the Pod's controller ref.
func controllerSessionName(pod *corev1.Pod) string {
	for _, o := range pod.OwnerReferences {
		if o.Kind == "GShareSession" {
			return o.Name
		}
	}
	return ""
}
