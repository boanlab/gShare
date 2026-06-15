/*
Pure builder tests (no envtest) covering the mode-rule table.
Plain `testing` assertions to keep deps minimal.
*/
package podbuilder

import (
	"testing"

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
