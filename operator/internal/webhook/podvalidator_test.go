package webhook

import (
	"context"
	"encoding/json"
	"testing"

	admissionv1 "k8s.io/api/admission/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/webhook/admission"

	gsharev1 "github.com/gshare/operator/api/v1"
)

const cardUUID = "GPU-a65dd044-2b71-a7a3-7442-ea068419428e"

func testScheme(t *testing.T) *runtime.Scheme {
	s := runtime.NewScheme()
	if err := clientgoscheme.AddToScheme(s); err != nil {
		t.Fatal(err)
	}
	if err := gsharev1.AddToScheme(s); err != nil {
		t.Fatal(err)
	}
	return s
}

func ownerSession(name, uuid, yieldState string) *gsharev1.GShareSession {
	s := &gsharev1.GShareSession{}
	s.Name = name
	s.Namespace = "gshare-sessions"
	s.Status.BoundGpuUuid = uuid
	s.Status.YieldState = yieldState
	return s
}

func spotSession(name, uuid string) *gsharev1.GShareSession {
	s := &gsharev1.GShareSession{}
	s.Name = name
	s.Namespace = "gshare-sessions"
	s.Spec.BorrowedGpuUuid = uuid
	return s
}

// bypassPod builds a borrow Pod (NVIDIA_VISIBLE_DEVICES pin, no nvidia.com/gpu) owned by ownerName.
func bypassPod(ownerName, uuid string) *corev1.Pod {
	p := &corev1.Pod{}
	if ownerName != "" {
		p.OwnerReferences = []metav1.OwnerReference{{Kind: "GShareSession", Name: ownerName}}
	}
	p.Spec.Containers = []corev1.Container{{
		Name: "session",
		Env:  []corev1.EnvVar{{Name: "NVIDIA_VISIBLE_DEVICES", Value: uuid}},
	}}
	return p
}

func handle(t *testing.T, objs []runtime.Object, pod *corev1.Pod) admission.Response {
	s := testScheme(t)
	c := fake.NewClientBuilder().WithScheme(s).WithRuntimeObjects(objs...).Build()
	g := &PodLendGuard{Client: c, Decoder: admission.NewDecoder(s), SessionNamespace: "gshare-sessions"}
	raw, _ := json.Marshal(pod)
	return g.Handle(context.Background(), admission.Request{
		AdmissionRequest: admissionv1.AdmissionRequest{Object: runtime.RawExtension{Raw: raw}},
	})
}

func TestBypassGPUUUID(t *testing.T) {
	if got := bypassGPUUUID(bypassPod("b", cardUUID)); got != cardUUID {
		t.Fatalf("bypass pin not detected, got %q", got)
	}
	// device-plugin pod (nvidia.com/gpu) is not a bypass pod.
	dp := &corev1.Pod{}
	dp.Spec.Containers = []corev1.Container{{
		Name:      "session",
		Resources: corev1.ResourceRequirements{Limits: corev1.ResourceList{"nvidia.com/gpu": resource.MustParse("1")}},
	}}
	if got := bypassGPUUUID(dp); got != "" {
		t.Fatalf("device-plugin pod misread as bypass, got %q", got)
	}
	// NVIDIA_VISIBLE_DEVICES=all is not a concrete card claim.
	allp := &corev1.Pod{}
	allp.Spec.Containers = []corev1.Container{{Env: []corev1.EnvVar{{Name: "NVIDIA_VISIBLE_DEVICES", Value: "all"}}}}
	if got := bypassGPUUUID(allp); got != "" {
		t.Fatalf("all misread as card claim, got %q", got)
	}
}

func TestHandleAllowsLegitBorrow(t *testing.T) {
	objs := []runtime.Object{
		ownerSession("owner", cardUUID, "Yielded"),
		spotSession("spot", cardUUID), // the spot session.s own session — must not count as a rival
	}
	resp := handle(t, objs, bypassPod("spot", cardUUID))
	if !resp.Allowed {
		t.Fatalf("legit borrow on yielded card should be allowed: %s", resp.Result.Message)
	}
}

func TestHandleDeniesNonYielded(t *testing.T) {
	objs := []runtime.Object{ownerSession("owner", cardUUID, "")} // owner active, not yielded
	resp := handle(t, objs, bypassPod("spot", cardUUID))
	if resp.Allowed {
		t.Fatalf("bypass onto a non-yielded card must be denied")
	}
}

func TestHandleDeniesDoubleLend(t *testing.T) {
	objs := []runtime.Object{
		ownerSession("owner", cardUUID, "Lent"),
		spotSession("spot1", cardUUID), // an existing rival spot session
	}
	resp := handle(t, objs, bypassPod("spot2", cardUUID))
	if resp.Allowed {
		t.Fatalf("second spot session on a lent card must be denied")
	}
}

func TestHandleAllowsNonBypass(t *testing.T) {
	dp := &corev1.Pod{}
	dp.Spec.Containers = []corev1.Container{{
		Name:      "session",
		Resources: corev1.ResourceRequirements{Limits: corev1.ResourceList{"nvidia.com/gpu": resource.MustParse("1")}},
	}}
	resp := handle(t, nil, dp)
	if !resp.Allowed {
		t.Fatalf("non-bypass (device-plugin) pod must be allowed unchanged")
	}
}
