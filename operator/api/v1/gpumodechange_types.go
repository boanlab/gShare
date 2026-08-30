/*
GpuModeChange: a control-plane request to change one physical card's HAMi sharing backend
(hami-core <-> mig) on a node.

The control plane creates it after draining the card in its ledger (mode_state=applying); the
operator's GpuModeChangeReconciler runs a privileged mig-agent Job on the node (assert no
processes -> nvidia-smi -mig <0|1> -> reset), restarts the HAMi device plugin so the register
annotation refreshes, and reports the outcome in .status. The control plane confirms the change
through its normal inventory sync (the per-card mode in hami.io/node-nvidia-register) and only
then returns the card to service.
*/
package v1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// GpuModeChangeSpec describes the requested card-mode transition.
type GpuModeChangeSpec struct {
	// NodeName is the Kubernetes node holding the card.
	NodeName string `json:"nodeName"`

	// GpuUuid is the physical card (GPU-... UUID, as reported by HAMi's register annotation).
	GpuUuid string `json:"gpuUuid"`

	// GpuIndex is the card's index on the node (nvidia-smi -i target). The agent cross-checks it
	// against GpuUuid before acting.
	// +kubebuilder:validation:Minimum=0
	GpuIndex int `json:"gpuIndex"`

	// TargetMode is the HAMi backend to switch to.
	// +kubebuilder:validation:Enum=mig;hami-core
	TargetMode string `json:"targetMode"`
}

// GpuModeChangeStatus reports the transition's progress.
type GpuModeChangeStatus struct {
	// Phase: Pending -> Running -> Succeeded | Failed.
	// +optional
	Phase string `json:"phase,omitempty"`

	// Message carries the failure detail (agent log tail) on Failed.
	// +optional
	Message string `json:"message,omitempty"`

	// JobRef is the namespace/name of the agent Job executing the change.
	// +optional
	JobRef string `json:"jobRef,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Node",type=string,JSONPath=`.spec.nodeName`
// +kubebuilder:printcolumn:name="GPU",type=string,JSONPath=`.spec.gpuUuid`
// +kubebuilder:printcolumn:name="Target",type=string,JSONPath=`.spec.targetMode`
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`

// GpuModeChange is the Schema for the gpumodechanges API.
type GpuModeChange struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   GpuModeChangeSpec   `json:"spec,omitempty"`
	Status GpuModeChangeStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// GpuModeChangeList contains a list of GpuModeChange.
type GpuModeChangeList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []GpuModeChange `json:"items"`
}

func init() {
	SchemeBuilder.Register(&GpuModeChange{}, &GpuModeChangeList{})
}
