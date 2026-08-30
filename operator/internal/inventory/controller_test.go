package inventory

import (
	"testing"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// A HAMi node: each physical GPU in the annotation becomes one device with its real UUID and
// physical VRAM, and the mode comes from the node pool label. nvidia.com/gpu=10 is a slot count, so
// it must not over-split into ten devices of a tenth of the VRAM each.
func TestDevicesFromHAMiAnnotation(t *testing.T) {
	node := &corev1.Node{
		ObjectMeta: metav1.ObjectMeta{
			Name:   "gpu2-2",
			Labels: map[string]string{labelGPUMode: "fractional"},
			Annotations: map[string]string{
				annoHAMiRegister: `[{"id":"GPU-abc","count":10,"devmem":24564,"devcore":100,"type":"NVIDIA GeForce RTX 4090","mode":"hami-core","health":true}]`,
			},
		},
		Status: corev1.NodeStatus{Capacity: corev1.ResourceList{
			resGPU: resource.MustParse("10"), // HAMi slot count, not the number of physical GPUs
		}},
	}
	devs := devicesFromCapacity(node)
	if len(devs) != 1 {
		t.Fatalf("expected 1 physical device, got %d", len(devs))
	}
	d := devs[0]
	if d.UUID != "GPU-abc" {
		t.Errorf("UUID = %q, want GPU-abc", d.UUID)
	}
	if d.TotalMemMB != 24564 {
		t.Errorf("TotalMemMB = %d, want 24564 (full physical VRAM, not split)", d.TotalMemMB)
	}
	if d.Mode != "fractional" {
		t.Errorf("Mode = %q, want fractional (from gshare.io/gpu-mode label)", d.Mode)
	}
	if d.Status != "ready" {
		t.Errorf("Status = %q, want ready", d.Status)
	}
}

// On the same HAMi node, an exclusive node pool label must make device.mode exclusive: the mode is
// decided by gshare.io/gpu-mode, not by whether gpumem is advertised.
func TestHAMiNodeModeFromPoolLabel(t *testing.T) {
	node := &corev1.Node{
		ObjectMeta: metav1.ObjectMeta{
			Name:   "gpu2-1",
			Labels: map[string]string{labelGPUMode: "exclusive"},
			Annotations: map[string]string{
				annoHAMiRegister: `[{"id":"GPU-xyz","count":10,"devmem":24564,"devcore":100,"type":"NVIDIA GeForce RTX 4090","health":true}]`,
			},
		},
	}
	devs := devicesFromCapacity(node)
	if len(devs) != 1 || devs[0].Mode != "exclusive" {
		t.Fatalf("expected 1 exclusive device, got %+v", devs)
	}
}

// Fallback: with no HAMi annotation, devices are built from device-plugin capacity, as with
// fake-gpu-operator.
func TestDevicesFromCapacityFallback(t *testing.T) {
	node := &corev1.Node{
		ObjectMeta: metav1.ObjectMeta{
			Name:   "fake-frac",
			Labels: map[string]string{labelGPUMode: "fractional"},
		},
		Status: corev1.NodeStatus{Capacity: corev1.ResourceList{
			resGPU:    resource.MustParse("4"),
			resGPUMem: resource.MustParse("40000"),
		}},
	}
	devs := devicesFromCapacity(node)
	if len(devs) != 4 {
		t.Fatalf("expected 4 devices, got %d", len(devs))
	}
	if devs[0].TotalMemMB != 10000 {
		t.Errorf("per-device mem = %d, want 10000 (40000/4)", devs[0].TotalMemMB)
	}
	if devs[0].Mode != "fractional" {
		t.Errorf("Mode = %q, want fractional", devs[0].Mode)
	}
}

// A CPU-only node — no GPU resources, no annotation — produces no devices.
func TestCPUNodeNoDevices(t *testing.T) {
	node := &corev1.Node{ObjectMeta: metav1.ObjectMeta{Name: "cpu-1"}}
	if devs := devicesFromCapacity(node); devs != nil {
		t.Fatalf("expected nil for CPU node, got %+v", devs)
	}
}

// Per-card mode: HAMi's register annotation can mark individual cards "mig" while others stay
// "hami-core" — the report must carry the distinction per DEVICE, with hami-core mapping to the
// node-label fallback (fractional vs exclusive is GShare policy, not a HAMi fact).
func TestDevicesFromHAMiPerCardMode(t *testing.T) {
	node := &corev1.Node{
		ObjectMeta: metav1.ObjectMeta{
			Name:   "gpu-mixed",
			Labels: map[string]string{labelGPUMode: "fractional"},
			Annotations: map[string]string{
				annoHAMiRegister: `[` +
					`{"id":"GPU-frac","count":10,"devmem":98304,"devcore":100,"type":"NVIDIA RTX PRO 6000 Blackwell","mode":"hami-core","health":true},` +
					`{"id":"GPU-mig","count":10,"devmem":98304,"devcore":100,"type":"NVIDIA RTX PRO 6000 Blackwell","mode":"mig","health":true}` +
					`]`,
			},
		},
	}
	devs := devicesFromHAMi(node)
	if len(devs) != 2 {
		t.Fatalf("expected 2 devices, got %d", len(devs))
	}
	byUUID := map[string]Device{}
	for _, d := range devs {
		byUUID[d.UUID] = d
	}
	if got := byUUID["GPU-frac"].Mode; got != "fractional" {
		t.Fatalf("hami-core card should fall back to the node label: got %q", got)
	}
	if got := byUUID["GPU-mig"].Mode; got != "mig" {
		t.Fatalf("mig card must report mig: got %q", got)
	}
}
