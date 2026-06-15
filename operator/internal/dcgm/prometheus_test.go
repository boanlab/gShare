package dcgm

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestGPUUtilParsesPercent(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1700000000,"42.5"]}]}}`))
	}))
	defer srv.Close()

	p := NewPrometheus(srv.URL, 90*time.Second)
	if got := p.GPUUtil(context.Background(), "GPU-abc"); got != 0.425 {
		t.Fatalf("expected 0.425, got %v", got)
	}
}

func TestGPUUtilFailSafeBusy(t *testing.T) {
	// Empty series → busy (1.0), so a missing metric never triggers a false idle-pause.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"status":"success","data":{"resultType":"vector","result":[]}}`))
	}))
	defer srv.Close()

	p := NewPrometheus(srv.URL, 0)
	if got := p.GPUUtil(context.Background(), "GPU-x"); got != 1.0 {
		t.Fatalf("empty series must be busy(1.0), got %v", got)
	}
	if got := p.GPUUtil(context.Background(), ""); got != 1.0 {
		t.Fatalf("empty uuid must be busy(1.0), got %v", got)
	}
}

func TestGPUUtilHTTPErrorFailSafe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	p := NewPrometheus(srv.URL, 90*time.Second)
	if got := p.GPUUtil(context.Background(), "GPU-y"); got != 1.0 {
		t.Fatalf("HTTP error must be busy(1.0), got %v", got)
	}
}
