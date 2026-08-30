package controller

import (
	"context"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/scheme"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestEvictionMessageFindsTheKubeletEvent(t *testing.T) {
	pod := &corev1.Pod{ObjectMeta: metav1.ObjectMeta{Namespace: "gshare-sessions", Name: "ses-ses-x"}}
	ev := &corev1.Event{
		ObjectMeta:     metav1.ObjectMeta{Namespace: "gshare-sessions", Name: "e1"},
		Reason:         "Evicted",
		Message:        "Pod ephemeral local storage usage exceeds the total limit of containers 2Gi.",
		InvolvedObject: corev1.ObjectReference{Kind: "Pod", Name: "ses-ses-x", Namespace: "gshare-sessions"},
	}
	other := &corev1.Event{
		ObjectMeta:     metav1.ObjectMeta{Namespace: "gshare-sessions", Name: "e2"},
		Reason:         "Pulled",
		InvolvedObject: corev1.ObjectReference{Kind: "Pod", Name: "ses-ses-x"},
	}
	c := fake.NewClientBuilder().WithScheme(scheme.Scheme).WithObjects(pod, ev, other).Build()
	got := evictionMessage(context.Background(), c, pod)
	want := "Evicted: Pod ephemeral local storage usage exceeds the total limit of containers 2Gi."
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
	if evictionMessage(context.Background(), c, &corev1.Pod{ObjectMeta: metav1.ObjectMeta{Namespace: "gshare-sessions", Name: "other"}}) != "" {
		t.Fatal("unrelated pod must yield empty")
	}
	if evictionMessage(context.Background(), nil, pod) != "" {
		t.Fatal("nil reader must be safe")
	}
}
