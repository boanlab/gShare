/*
GShareSession CRD types.

.spec is the desired state filled by the SoT control plane on admission;
.status is the result filled by this operator while reconciling.

  - Interactive sessions only.
  - resourceClass: gpu (billed) | cpu (free, no GPU requested).
  - mode: fractional (HAMi) | exclusive (standard device-plugin) | mig.
  - The operator never touches credits — money is owned by the SoT control plane.
*/
package v1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// VolumeSpec is one persistent volume to mount into the session Pod.
type VolumeSpec struct {
	// Name is the logical volume name — the control plane's volume id (vol_...). The operator
	// sanitizes it to an RFC 1123 name for the PVC and the pod volume (lowercase, '_' -> '-').
	Name string `json:"name"`
	// MountPath is the in-container mount path, e.g. /workspace.
	MountPath string `json:"mountPath"`
	// Mode is the PVC access mode, from the volume's own access class (RWO/RWX/ROX):
	// ReadWriteOnce | ReadWriteMany | ReadOnlyMany.
	// +kubebuilder:validation:Enum=ReadWriteOnce;ReadWriteMany;ReadOnlyMany
	Mode string `json:"mode"`
	// ReadOnly mounts the volume read-only in THIS session, independent of the PVC access mode
	// (a writable RWX volume can still be attached ro).
	// +optional
	ReadOnly bool `json:"readOnly,omitempty"`
	// SizeGb is the provisioned quota backing the PVC the operator ensures. 0 falls back to a
	// 10Gi default (older control planes that do not send it).
	// +kubebuilder:validation:Minimum=0
	// +optional
	SizeGb int `json:"sizeGb,omitempty"`
}

// ConnectSpec is the source of per-session connection credentials, projected
// by the operator into the per-session Secret ses-<id>-secret.
type ConnectSpec struct {
	// ProxyTokenSecretRef optionally references an existing Secret holding the
	// code-server / web-app proxy token, instead of inlining it in the CR.
	ProxyTokenSecretRef string `json:"proxyTokenSecretRef,omitempty"`
}

// GShareSessionSpec is the desired state filled by the SoT control plane on admission.
type GShareSessionSpec struct {
	// ClusterID is the Cluster this session is bound to (no federation).
	ClusterID string `json:"clusterId"`

	// ResourceClass: gpu (billed) | cpu (free, no GPU requested).
	// +kubebuilder:validation:Enum=gpu;cpu
	ResourceClass string `json:"resourceClass"`

	// Mode: fractional | exclusive | mig. Empty for cpu sessions.
	// +kubebuilder:validation:Enum=fractional;exclusive;mig
	// +optional
	Mode string `json:"mode,omitempty"`

	// Paused: when true the operator deletes the session Pod to release the GPU but keeps
	// the CR (phase=Paused, not Terminated). Resuming (false) recreates the Pod and re-binds a GPU.
	// +optional
	Paused bool `json:"paused,omitempty"`

	// LosslessPause: when paused, attempt a state-preserving checkpoint (cuda-checkpoint + CRIU)
	// rather than a plain Pod delete, so resume can restore VRAM/process state. Set at session
	// creation from the offering+node capability gate. Falls back to cold pause if a checkpointer
	// is unavailable or the checkpoint fails.
	// +optional
	LosslessPause bool `json:"losslessPause,omitempty"`

	// PauseMode: how a pause releases the GPU. "cold" (default) deletes the Pod; "yield" keeps the
	// Pod alive and evicts VRAM via cuda-checkpoint (in-place GPU yield) so resume is lossless and
	// the physical card can be lent to a preemptible spot session. (docs/paper/manuscript, §Design)
	// +kubebuilder:validation:Enum=cold;yield
	// +optional
	PauseMode string `json:"pauseMode,omitempty"`

	// Preemptible: this session may be admitted onto a yielded (lent) card and reclaimed (SIGTERM
	// + grace) when the resident returns. Spot sessions run at spot semantics. (GPU yield and lending; see docs/paper/)
	// +optional
	Preemptible bool `json:"preemptible,omitempty"`

	// BorrowedGpuUuid: when set, this is a spot session placed on a resident's yielded card. The Pod
	// is built device-plugin-BYPASS — the card is injected by UUID (NVIDIA_VISIBLE_DEVICES), with NO
	// nvidia.com/gpu request, pinned to BorrowedNode. (docs/paper/manuscript, §Design)
	// +optional
	BorrowedGpuUuid string `json:"borrowedGpuUuid,omitempty"`

	// BorrowedNode: the node holding the borrowed (yielded) card; the borrow Pod is pinned here.
	// +optional
	BorrowedNode string `json:"borrowedNode,omitempty"`

	// GracefulDemote: on a yield→cold demotion (reservation-TTL expiry), toggle VRAM back into the
	// live process and delete the Pod with its grace period so the job saves a FRESH checkpoint on
	// SIGTERM, instead of cold-deleting the suspended process. Set by the control plane only when the
	// card is NOT lent (physically free, so restore is safe). (docs/paper/manuscript, §Design)
	// +optional
	GracefulDemote bool `json:"gracefulDemote,omitempty"`

	// GpuMemMb is the fractional VRAM hard limit (nvidia.com/gpumem).
	// +optional
	GpuMemMb int `json:"gpuMemMb,omitempty"`

	// GpuCores is the fractional core percentage 0..100 (nvidia.com/gpucores).
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=100
	// +optional
	GpuCores int `json:"gpuCores,omitempty"`

	// MigProfile is the MIG profile for mode=mig, e.g. "1g.5gb".
	// +optional
	MigProfile string `json:"migProfile,omitempty"`

	// PinnedGpuUuid pins the session to the physical card the control plane's ledger reserved,
	// via HAMi's nvidia.com/use-gpuuuid annotation. This makes the ledger the real placer: the
	// card a session runs on is exactly the card that was reserved, and per-card pool membership
	// stays a control-plane concern HAMi never needs to know about. Distinct from
	// BorrowedGpuUuid (spot borrow: preemptible annotation + lend interlock).
	// +optional
	PinnedGpuUuid string `json:"pinnedGpuUuid,omitempty"`

	// FullCard requests the whole physical card THROUGH hami-scheduler
	// (gpumem-percentage=100 + gpucores=100) instead of the plain device plugin, so exclusive
	// sessions flow through the same scheduler and UUID pinning as fractional ones and node-level
	// exclusive pools are unnecessary. Set by the control plane for mode=exclusive when per-card
	// pools are enabled.
	// +optional
	FullCard bool `json:"fullCard,omitempty"`

	// Cpu is the vCPU (cores) allotment from the offering flavor — Pod request=limit (Guaranteed).
	// +kubebuilder:validation:Minimum=0
	// +optional
	Cpu int `json:"cpu,omitempty"`

	// MemGb is the RAM (GiB) allotment — Pod request=limit (Guaranteed, OOMKill on exceed).
	// +kubebuilder:validation:Minimum=0
	// +optional
	MemGb int `json:"memGb,omitempty"`

	// DiskGb is the ephemeral scratch disk (GiB) — Pod ephemeral-storage request=limit (evict on exceed).
	// +kubebuilder:validation:Minimum=0
	// +optional
	DiskGb int `json:"diskGb,omitempty"`

	// Image is the session container image.
	Image string `json:"image"`

	// OfferingID is the resolved offering this session was admitted against.
	OfferingID string `json:"offeringId"`

	// ClusterMode: single | multi.
	// +kubebuilder:validation:Enum=single;multi
	ClusterMode string `json:"clusterMode"`

	// Volumes to mount into the session Pod.
	// +optional
	Volumes []VolumeSpec `json:"volumes,omitempty"`

	// Connect is the source of per-session connection credentials.
	// +optional
	Connect ConnectSpec `json:"connect,omitempty"`

	// BillingWalletID is carried for reporting/audit only. Optional: CPU (free) sessions have no wallet.
	// +optional
	BillingWalletID string `json:"billingWalletId,omitempty"`

	// Owner is the owning user identifier (informational).
	Owner string `json:"owner"`

	// ProjectID is the owning project (tenant) identifier (informational). Optional: a session
	// may have no project (personal/CPU).
	// +optional
	ProjectID string `json:"projectId,omitempty"`
}

// SessionCondition mirrors metav1.Condition semantics for richer status.
type SessionCondition struct {
	// Type of the condition, e.g. "Ready" | "PodScheduled".
	Type string `json:"type"`
	// Status: "True" | "False" | "Unknown".
	Status metav1.ConditionStatus `json:"status"`
	// Reason is a machine-readable reason for the condition's last transition.
	Reason string `json:"reason,omitempty"`
	// Message is a human-readable detail.
	Message string `json:"message,omitempty"`
	// LastTransitionTime is when the condition last changed.
	LastTransitionTime metav1.Time `json:"lastTransitionTime,omitempty"`
}

// GShareSessionStatus is the observed state filled by this operator. The SoT control
// plane reads it to drive the ledger (running->consume, terminated->settle).
type GShareSessionStatus struct {
	// Phase: Pending | Preparing | Running | Terminating | Terminated | Error.
	// +optional
	Phase string `json:"phase,omitempty"`

	// BoundGpuUuid is the GPU bound by hami-scheduler / device-plugin.
	// +optional
	BoundGpuUuid string `json:"boundGpuUuid,omitempty"`

	// PodRef is namespace/name of the session Pod.
	// +optional
	PodRef string `json:"podRef,omitempty"`

	// CheckpointRef is the storage reference of the last lossless-pause checkpoint
	// (empty when paused cold or never checkpointed).
	// +optional
	CheckpointRef string `json:"checkpointRef,omitempty"`

	// CheckpointNode is the node holding the checkpoint (content-store image + hostPath dir
	// are node-local). Recorded at checkpoint time so cleanup can target that node on
	// cold-resume / terminate. Empty when no checkpoint exists.
	// +optional
	CheckpointNode string `json:"checkpointNode,omitempty"`

	// YieldState: "" (not yielded) | "Yielded" (VRAM evicted, Pod alive, card lendable) |
	// "Lent" (a spot session is using the card). Distinguishes in-place yield from cold pause and
	// tells resume to toggle VRAM back rather than recreate the Pod. (GPU yield and lending; see docs/paper/)
	// +optional
	YieldState string `json:"yieldState,omitempty"`

	// EvictedVramMb is the VRAM moved to host RAM while yielded (host-RAM cost accounting).
	// +optional
	EvictedVramMb int `json:"evictedVramMb,omitempty"`

	// LentTo is the spot session id currently using this yielded card (empty if free/lent-none).
	// +optional
	LentTo string `json:"lentTo,omitempty"`

	// UsedMemMb is the measured VRAM occupancy (inventory reconciliation).
	// +optional
	UsedMemMb int `json:"usedMemMb,omitempty"`

	// Message is a human-readable status detail.
	// +optional
	Message string `json:"message,omitempty"`

	// TraceID is the W3C traceparent trace-id restored from the CR annotation
	// gshare.io/traceparent, echoed on the status callback.
	// +optional
	TraceID string `json:"traceId,omitempty"`

	// LastTransitionTime is when Phase last changed.
	// +optional
	LastTransitionTime metav1.Time `json:"lastTransitionTime,omitempty"`

	// ObservedGeneration is the .metadata.generation last reconciled.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Conditions is the list of detailed status conditions.
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []SessionCondition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:shortName=gss
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="Mode",type=string,JSONPath=`.spec.mode`
// +kubebuilder:printcolumn:name="GPU",type=string,JSONPath=`.status.boundGpuUuid`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// GShareSession is the Schema for the gsharesessions API.
type GShareSession struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   GShareSessionSpec   `json:"spec,omitempty"`
	Status GShareSessionStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// GShareSessionList contains a list of GShareSession.
type GShareSessionList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []GShareSession `json:"items"`
}

func init() {
	SchemeBuilder.Register(&GShareSession{}, &GShareSessionList{})
}
