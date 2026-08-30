/*
Pure builder tests (no envtest) covering the mode-rule table.
Plain `testing` assertions to keep deps minimal.
*/
package podbuilder

import (
	"testing"

	"k8s.io/apimachinery/pkg/api/resource"

	gsharev1 "github.com/gshare/operator/api/v1"
)

func TestExclusiveBypassesHAMi(t *testing.T) {
	s := &gsharev1.GShareSession{Spec: gsharev1.GShareSessionSpec{
		ResourceClass: "gpu", Mode: "exclusive", Image: "img",
	}}
	pod := (&Builder{}).BuildPod(s)
	lim := pod.Spec.Containers[0].Resources.Limits

	if got := lim["nvidia.com/gpu"]; got.String() != "1" {
		t.Fatalf("expected nvidia.com/gpu=1, got %q", got.String())
	}
	if pod.Spec.SchedulerName != "" {
		t.Fatalf("exclusive must NOT set hami-scheduler, got %q", pod.Spec.SchedulerName)
	}
	if _, hasMem := lim["nvidia.com/gpumem"]; hasMem {
		t.Fatalf("exclusive must not request nvidia.com/gpumem")
	}
}

func TestFractionalUsesHAMi(t *testing.T) {
	s := &gsharev1.GShareSession{Spec: gsharev1.GShareSessionSpec{
		ResourceClass: "gpu", Mode: "fractional", GpuMemMb: 4000, GpuCores: 30, Image: "img",
	}}
	pod := (&Builder{}).BuildPod(s)

	if pod.Spec.SchedulerName != "hami-scheduler" {
		t.Fatalf("fractional must set hami-scheduler, got %q", pod.Spec.SchedulerName)
	}
	lim := pod.Spec.Containers[0].Resources.Limits
	if got := lim["nvidia.com/gpumem"]; got.Value() != 4000 {
		t.Fatalf("expected gpumem=4000, got %q", got.String())
	}
	if got := lim["nvidia.com/gpucores"]; got.Value() != 30 {
		t.Fatalf("expected gpucores=30, got %q", got.String())
	}
}

func spotSession() *gsharev1.GShareSession {
	return &gsharev1.GShareSession{Spec: gsharev1.GShareSessionSpec{
		ResourceClass: "gpu", Mode: "exclusive", Image: "img",
		BorrowedGpuUuid: "GPU-xyz", BorrowedNode: "gpu2-1",
	}}
}

func TestBorrowViaHAMiExtender(t *testing.T) {
	pod := (&Builder{HAMiYieldExtender: true}).BuildPod(spotSession())

	if pod.Spec.SchedulerName != "hami-scheduler" {
		t.Fatalf("HAMi borrow must route through hami-scheduler, got %q", pod.Spec.SchedulerName)
	}
	if pod.Spec.NodeName != "" {
		t.Fatalf("HAMi borrow must NOT be node-pinned, got %q", pod.Spec.NodeName)
	}
	lim := pod.Spec.Containers[0].Resources.Limits
	if got := lim["nvidia.com/gpu"]; got.Value() != 1 {
		t.Fatalf("HAMi borrow must request nvidia.com/gpu=1, got %q", got.String())
	}
	if got := lim["nvidia.com/gpumem-percentage"]; got.Value() != 100 {
		t.Fatalf("full-card HAMi borrow must request gpumem-percentage=100, got %q", got.String())
	}
	if pod.Annotations["gshare.io/preemptible"] != "true" || pod.Annotations["nvidia.com/use-gpuuuid"] != "GPU-xyz" {
		t.Fatalf("HAMi borrow must be annotated preemptible + use-gpuuuid, got %v", pod.Annotations)
	}
	for _, e := range pod.Spec.Containers[0].Env {
		if e.Name == "NVIDIA_VISIBLE_DEVICES" {
			t.Fatalf("HAMi borrow must not use the bypass env")
		}
	}
}

func TestBorrowBypassFallback(t *testing.T) {
	pod := (&Builder{}).BuildPod(spotSession()) // HAMiYieldExtender false (default)

	if pod.Spec.SchedulerName != "" {
		t.Fatalf("bypass borrow must use the default scheduler, got %q", pod.Spec.SchedulerName)
	}
	if pod.Spec.NodeName != "gpu2-1" {
		t.Fatalf("bypass borrow must be node-pinned to the owner node, got %q", pod.Spec.NodeName)
	}
	if _, ok := pod.Spec.Containers[0].Resources.Limits["nvidia.com/gpu"]; ok {
		t.Fatalf("bypass borrow must NOT request nvidia.com/gpu (device-plugin bypass)")
	}
	var hasVisible bool
	for _, e := range pod.Spec.Containers[0].Env {
		if e.Name == "NVIDIA_VISIBLE_DEVICES" && e.Value == "GPU-xyz" {
			hasVisible = true
		}
	}
	if !hasVisible {
		t.Fatalf("bypass borrow must inject NVIDIA_VISIBLE_DEVICES=GPU-xyz")
	}
	if pod.Annotations["nvidia.com/use-gpuuuid"] != "" {
		t.Fatalf("bypass borrow must not set the HAMi use-gpuuuid annotation")
	}
}

func TestCPUSessionRequestsNoGPU(t *testing.T) {
	s := &gsharev1.GShareSession{Spec: gsharev1.GShareSessionSpec{
		ResourceClass: "cpu", Image: "img",
	}}
	pod := (&Builder{}).BuildPod(s)

	if got := pod.Spec.NodeSelector["gshare.io/node-type"]; got != "cpu" {
		t.Fatalf("cpu session must select node-type=cpu, got %q", got)
	}
	if _, hasGPU := pod.Spec.Containers[0].Resources.Limits["nvidia.com/gpu"]; hasGPU {
		t.Fatalf("cpu session must not request a GPU")
	}
	if pod.Spec.RuntimeClassName != nil {
		t.Fatalf("cpu session must not set runtimeClassName")
	}
}

// Per-card mode: fractional drops the node-pool selector and pins the ledger-reserved card;
// exclusive flows through hami-scheduler as a 100% slice.
func TestPerCardModeFractionalPinsCard(t *testing.T) {
	b := &Builder{Namespace: "gshare-sessions", PerCardMode: true}
	s := &gsharev1.GShareSession{Spec: gsharev1.GShareSessionSpec{
		ResourceClass: "gpu", Mode: "fractional", Image: "img",
	}}
	s.Spec.GpuMemMb = 12288
	s.Spec.GpuCores = 13
	s.Spec.PinnedGpuUuid = "GPU-pinned"
	pod := b.BuildPod(s)
	if pod.Spec.NodeSelector["gshare.io/gpu-mode"] != "" {
		t.Fatalf("per-card mode must not use the node-pool selector")
	}
	if pod.Spec.SchedulerName != "hami-scheduler" {
		t.Fatalf("fractional must keep hami-scheduler")
	}
	if got := pod.Annotations["nvidia.com/use-gpuuuid"]; got != "GPU-pinned" {
		t.Fatalf("expected the ledger pin annotation, got %q", got)
	}
}

func TestPerCardModeExclusiveIsFullCardSlice(t *testing.T) {
	b := &Builder{Namespace: "gshare-sessions", PerCardMode: true}
	s := &gsharev1.GShareSession{Spec: gsharev1.GShareSessionSpec{
		ResourceClass: "gpu", Mode: "exclusive", Image: "img",
	}}
	s.Spec.PinnedGpuUuid = "GPU-pinned"
	pod := b.BuildPod(s)
	if pod.Spec.SchedulerName != "hami-scheduler" {
		t.Fatalf("per-card exclusive must flow through hami-scheduler")
	}
	limits := pod.Spec.Containers[0].Resources.Limits
	if limits["nvidia.com/gpumem-percentage"] != resource.MustParse("100") {
		t.Fatalf("exclusive must be a 100%% slice, got %v", limits)
	}
	if got := pod.Annotations["nvidia.com/use-gpuuuid"]; got != "GPU-pinned" {
		t.Fatalf("expected the ledger pin annotation, got %q", got)
	}
	if pod.Spec.NodeSelector["gshare.io/gpu-mode"] != "" {
		t.Fatalf("per-card mode must not use the node-pool selector")
	}
}

func TestLegacyExclusiveUnchangedWithoutFlag(t *testing.T) {
	b := &Builder{Namespace: "gshare-sessions"}
	pod := b.BuildPod(&gsharev1.GShareSession{Spec: gsharev1.GShareSessionSpec{
		ResourceClass: "gpu", Mode: "exclusive", Image: "img",
	}})
	if pod.Spec.SchedulerName != "" {
		t.Fatalf("legacy exclusive must bypass hami-scheduler")
	}
	if pod.Spec.NodeSelector["gshare.io/gpu-mode"] != "exclusive" {
		t.Fatalf("legacy exclusive keeps the node-pool selector")
	}
}

// Volume mounting: the control-plane volume id (vol_ULID, uppercase + underscore) must be
// sanitized into an RFC 1123 name for the pod volume and PVC reference — the raw id used to
// fail pod creation outright. readOnly is the per-session mount intent, independent of the PVC
// access mode.
func TestVolumeNamesAreSanitizedAndReadOnlyHonoured(t *testing.T) {
	s := &gsharev1.GShareSession{Spec: gsharev1.GShareSessionSpec{
		ResourceClass: "gpu", Mode: "fractional", Image: "img",
		Volumes: []gsharev1.VolumeSpec{
			{Name: "vol_01ABC_XYZ", MountPath: "/data", Mode: "ReadWriteOnce"},
			{Name: "vol_01DEF", MountPath: "/shared", Mode: "ReadWriteMany", ReadOnly: true},
		},
	}}
	pod := (&Builder{}).BuildPod(s)
	if got := pod.Spec.Volumes[0].Name; got != "vol-01abc-xyz" {
		t.Fatalf("volume name not sanitized: %q", got)
	}
	if got := pod.Spec.Volumes[0].PersistentVolumeClaim.ClaimName; got != "vol-01abc-xyz" {
		t.Fatalf("claim name not sanitized: %q", got)
	}
	mounts := pod.Spec.Containers[0].VolumeMounts
	if mounts[0].ReadOnly {
		t.Fatalf("rw mount must not be read-only")
	}
	if !mounts[1].ReadOnly {
		t.Fatalf("readOnly mount intent must be honoured on a writable volume")
	}
	if pod.Spec.Volumes[1].PersistentVolumeClaim.ReadOnly != true {
		t.Fatalf("pvc source of a ro mount should be read-only")
	}
}
