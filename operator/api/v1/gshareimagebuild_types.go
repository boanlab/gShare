/*
GShareImageBuild: a control-plane request to build a container image from a user-supplied
Dockerfile (or git context) and push it to the GShare registry.

The control plane creates it after recording the ImageBuild row; the ImageBuildReconciler runs a
non-privileged kaniko Job in the sessions namespace (restricted PSS), streams the outcome, and
reports phase + log tail back to POST /internal/image-builds/{id}/status. The CR carries no
credentials — registry auth (if any) comes from the operator's mounted push secret.
*/
package v1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// GShareImageBuildSpec describes one requested image build.
type GShareImageBuildSpec struct {
	// BuildId is the control-plane ImageBuild row id (bld_...); callbacks key on it.
	BuildId string `json:"buildId"`

	// Owner is the requesting user id (informational; labels/annotations only).
	// +optional
	Owner string `json:"owner,omitempty"`

	// GroupId is the project the build belongs to (informational).
	// +optional
	GroupId string `json:"groupId,omitempty"`

	// Source selects the build context kind.
	// +kubebuilder:validation:Enum=dockerfile;git
	Source string `json:"source"`

	// Dockerfile is the inline Dockerfile content (source=dockerfile). Max 32KB.
	// +optional
	Dockerfile string `json:"dockerfile,omitempty"`

	// GitUrl is the clone URL (source=git). Public repos only.
	// +optional
	GitUrl string `json:"gitUrl,omitempty"`

	// GitRef is the branch/tag/commit for source=git.
	// +optional
	GitRef string `json:"gitRef,omitempty"`

	// ContextSubPath is the build context inside the repo (source=git).
	// +optional
	ContextSubPath string `json:"contextSubPath,omitempty"`

	// BuildArgs are --build-arg key/values.
	// +optional
	BuildArgs map[string]string `json:"buildArgs,omitempty"`

	// TargetRef is the full image reference kaniko pushes on success.
	TargetRef string `json:"targetRef"`
}

// GShareImageBuildStatus reports build progress.
type GShareImageBuildStatus struct {
	// Phase: Pending -> Running -> Succeeded | Failed.
	// +optional
	Phase string `json:"phase,omitempty"`

	// Message carries the failure detail (kaniko log tail) on Failed.
	// +optional
	Message string `json:"message,omitempty"`

	// JobRef is the namespace/name of the kaniko Job.
	// +optional
	JobRef string `json:"jobRef,omitempty"`

	// Reported marks that the terminal phase was delivered to the control plane.
	// +optional
	Reported bool `json:"reported,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Build",type=string,JSONPath=`.spec.buildId`
// +kubebuilder:printcolumn:name="Target",type=string,JSONPath=`.spec.targetRef`
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// GShareImageBuild is the Schema for the gshareimagebuilds API.
type GShareImageBuild struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   GShareImageBuildSpec   `json:"spec,omitempty"`
	Status GShareImageBuildStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// GShareImageBuildList contains a list of GShareImageBuild.
type GShareImageBuildList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []GShareImageBuild `json:"items"`
}

func init() {
	SchemeBuilder.Register(&GShareImageBuild{}, &GShareImageBuildList{})
}
