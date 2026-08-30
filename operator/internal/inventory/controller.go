/*
Package inventory implements the node/GpuDevice inventory controller.

It collects GPU total/used from DCGM/NVML + device-plugin capacity per node and
reflects it into the control plane's ledger (GpuDevice upsert), signalling drift when
Σ session.gpu_mem_mb > GpuDevice.total_mem_mb. The ledger is the single source of
truth; drift is corrected toward it.
*/
package inventory

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	"github.com/gshare/operator/internal/sot"
)

// Device-plugin / HAMi resource names + labels advertised on the Node object.
const (
	resGPU       = "nvidia.com/gpu"         // device-plugin: GPU/vGPU-slot count
	resGPUMem    = "nvidia.com/gpumem"      // fake-gpu-operator: total schedulable VRAM (MiB) as capacity
	labelProduct = "nvidia.com/gpu.product" // GPU model name, e.g. NVIDIA-RTX-4090
	labelGPUMode = "gshare.io/gpu-mode"     // node pool mode: exclusive, fractional, or mig
	// Prerequisites for lossless pause; a node is lossless_capable only when both read "ready".
	// An external detector or an administrator applies these labels.
	labelCRIU           = "gshare.io/criu"            // CRIU container checkpoint/restore is ready
	labelCudaCheckpoint = "gshare.io/cuda-checkpoint" // NVIDIA cuda-checkpoint is usable (driver and CUDA)
	// Node annotation in which HAMi publishes the measured per-GPU facts: real UUID, VRAM, and cores.
	// node.Status.Capacity carries no nvidia.com/gpumem — it advertises slot counts only.
	annoHAMiRegister = "hami.io/node-nvidia-register"
)

// hamiDevice is one entry of the hami.io/node-nvidia-register annotation, i.e. one physical GPU.
type hamiDevice struct {
	ID      string `json:"id"`      // the real GPU UUID
	Count   int    `json:"count"`   // number of vGPU slots, not the number of physical GPUs
	DevMem  int    `json:"devmem"`  // physical VRAM in MiB
	DevCore int    `json:"devcore"` // total cores, as a percentage
	Type    string `json:"type"`    // GPU model
	Health  bool   `json:"health"`
	Mode    string `json:"mode"` // HAMi's PER-CARD sharing backend: "hami-core" or "mig"
}

// nodeGPUMode reads the device mode from the gshare.io/gpu-mode node pool label, falling back to def.
func nodeGPUMode(node *corev1.Node, def string) string {
	if m := node.Labels[labelGPUMode]; m != "" {
		return m
	}
	return def
}

// Device is one collected GPU device snapshot.
type Device struct {
	UUID       string
	Model      string
	Mode       string
	TotalMemMB int
	UsedMemMB  int
	UsedCores  int
	Status     string
}

// Collector pulls device data for a node (DCGM/NVML + device-plugin capacity).
type Collector interface {
	Devices(ctx context.Context, node *corev1.Node) []Device
}

// InventoryReconciler reconciles Node -> GpuDevice inventory.
type InventoryReconciler struct {
	client.Client
	Scheme    *runtime.Scheme
	SoT       sot.Reporter
	Collector Collector
	ClusterID string
}

// +kubebuilder:rbac:groups="",resources=nodes,verbs=get;list;watch

// Reconcile collects devices for a node and upserts them into the control plane's ledger.
func (r *InventoryReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	var node corev1.Node
	if err := r.Get(ctx, req.NamespacedName, &node); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	// Prefer the DCGM/NVML Collector (live total/used per UUID); fall back to the
	// device-plugin advertised capacity on node.Status when no Collector is wired.
	var devs []Device
	if r.Collector != nil {
		devs = r.Collector.Devices(ctx, &node)
	}
	if len(devs) == 0 {
		devs = devicesFromCapacity(&node)
	}

	// Node capacity in CPU cores, memory GiB, and ephemeral GiB. Drives both the inventory display
	// and the availability gate at admission.
	nodeCPU := int(node.Status.Capacity.Cpu().Value())
	nodeMemGB := int(node.Status.Capacity.Memory().Value() / (1024 * 1024 * 1024))
	nodeDiskGB := int(node.Status.Capacity.StorageEphemeral().Value() / (1024 * 1024 * 1024))
	// Lossless pause prerequisite: a node is capable only when both the CRIU and cuda-checkpoint
	// labels read "ready".
	losslessCapable := node.Labels[labelCRIU] == "ready" && node.Labels[labelCudaCheckpoint] == "ready"
	// Role for the console's node list: control-plane and storage from their labels, GPU when the
	// node carries devices (or is pool-labelled for them), plain CPU worker otherwise.
	role := "cpu"
	if _, cp := node.Labels["node-role.kubernetes.io/control-plane"]; cp {
		role = "master"
	} else if _, m := node.Labels["node-role.kubernetes.io/master"]; m {
		role = "master"
	} else if node.Labels["gshare.io/role"] == "storage" {
		role = "storage"
	} else if len(devs) > 0 || node.Labels[labelGPUMode] != "" {
		role = "gpu"
	}
	// Report capacity for every node, GPU-less ones included, independently of the device loop.
	_ = r.SoT.UpsertNode(ctx, sot.Node{
		NodeID: node.Name, NodeCPU: nodeCPU, NodeMemGB: nodeMemGB, NodeDiskGB: nodeDiskGB,
		LosslessCapable: losslessCapable, Role: role,
	})
	for _, d := range devs {
		_ = r.SoT.UpsertGpuDevice(ctx, sot.GpuDevice{
			NodeID:     node.Name,
			UUID:       d.UUID,
			Model:      d.Model,
			Mode:       d.Mode,
			TotalMemMB: d.TotalMemMB,
			UsedMemMB:  d.UsedMemMB,
			TotalCores: 100,
			UsedCores:  d.UsedCores,
			Status:     d.Status,
			NodeCPU:    nodeCPU,
			NodeMemGB:  nodeMemGB,
			NodeDiskGB: nodeDiskGB,
		})
		// Σ used > total -> drift signal (ledger is the truth).
		if d.UsedMemMB > d.TotalMemMB {
			_ = r.SoT.ReportDrift(ctx, d.UUID, d.UsedMemMB, d.TotalMemMB)
		}
	}

	return ctrl.Result{RequeueAfter: 30 * time.Second}, nil
}

// devicesFromCapacity derives a best-effort device snapshot from the Node when no
// DCGM/NVML Collector is wired. Used VRAM/cores stay 0; the ledger reconciles real
// occupancy from the Σ session view.
//
// Precedence:
//  1. HAMi's hami.io/node-nvidia-register annotation: one physical GPU becomes one device, with its
//     real UUID and VRAM. HAMi advertises nvidia.com/gpu as a slot count, so reading capacity alone
//     would over-split the node.
//  2. Fallback to device-plugin capacity: one device per nvidia.com/gpu, splitting nvidia.com/gpumem
//     evenly between them.
//
// device.mode comes from the gshare.io/gpu-mode node pool label. CPU-only nodes — no GPU resources
// and no annotation — produce no devices.
func devicesFromCapacity(node *corev1.Node) []Device {
	if devs := devicesFromHAMi(node); len(devs) > 0 {
		return devs
	}

	gpuQty, hasGPU := node.Status.Capacity[resGPU]
	if !hasGPU {
		return nil
	}
	count := int(gpuQty.Value())
	if count <= 0 {
		return nil
	}

	totalMemMB := 0
	if memQty, ok := node.Status.Capacity[resGPUMem]; ok {
		totalMemMB = int(memQty.Value())
	}
	perDeviceMemMB := 0
	if totalMemMB > 0 {
		perDeviceMemMB = totalMemMB / count
	}

	// Advertising nvidia.com/gpumem defaults the mode to fractional; a node pool label overrides it.
	def := "exclusive"
	if totalMemMB > 0 {
		def = "fractional"
	}
	mode := nodeGPUMode(node, def)

	devs := make([]Device, 0, count)
	for i := 0; i < count; i++ {
		// No NVML/DCGM UUID from capacity alone; derive a stable per-node device key
		// so the ledger upsert is idempotent across reconciles.
		devs = append(devs, Device{
			UUID:       fmt.Sprintf("%s-gpu-%d", node.Name, i),
			Model:      node.Labels[labelProduct],
			Mode:       mode,
			TotalMemMB: perDeviceMemMB,
			UsedMemMB:  0,
			UsedCores:  0,
			Status:     "ready",
		})
	}
	return devs
}

// devicesFromHAMi turns the node-nvidia-register annotation into one device per physical GPU,
// carrying its real UUID and physical VRAM. A missing or unparseable annotation returns nil, which
// sends the caller down the capacity fallback. device.mode comes from the gshare.io/gpu-mode label.
func devicesFromHAMi(node *corev1.Node) []Device {
	raw := node.Annotations[annoHAMiRegister]
	if raw == "" {
		return nil
	}
	var entries []hamiDevice
	if err := json.Unmarshal([]byte(raw), &entries); err != nil {
		return nil
	}
	fallback := nodeGPUMode(node, "fractional") // node label, for entries with no per-card mode
	devs := make([]Device, 0, len(entries))
	for _, e := range entries {
		if e.ID == "" || e.DevMem <= 0 {
			continue
		}
		status := "ready"
		if !e.Health {
			status = "notready"
		}
		model := e.Type
		if model == "" {
			model = node.Labels[labelProduct]
		}
		// Per-CARD mode from HAMi's own register annotation: "mig" is a card-level fact; a
		// "hami-core" card cannot distinguish fractional from exclusive (that is GShare policy,
		// carried by the ledger's desired_mode), so it maps to the node-label fallback.
		mode := fallback
		if e.Mode == "mig" {
			mode = "mig"
		}
		devs = append(devs, Device{
			UUID:       e.ID,
			Model:      model,
			Mode:       mode,
			TotalMemMB: e.DevMem,
			UsedMemMB:  0,
			UsedCores:  0,
			Status:     status,
		})
	}
	return devs
}

// SetupWithManager registers the controller.
func (r *InventoryReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&corev1.Node{}).
		Complete(r)
}
