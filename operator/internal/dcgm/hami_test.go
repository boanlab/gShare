package dcgm

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

const hamiMetrics = `# HELP hami_host_gpu_utilization_ratio util
hami_host_gpu_memory_used_bytes{device_uuid="GPU-aaa",zone="vGPU"} 5.05e+08
hami_host_gpu_utilization_ratio{device_index="0",device_type="NVIDIA",device_uuid="GPU-aaa",zone="vGPU"} 0.42
hami_host_gpu_utilization_ratio{device_index="1",device_type="NVIDIA",device_uuid="GPU-bbb",zone="vGPU"} 0
`

func TestHAMiGPUUtilParses(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(hamiMetrics))
	}))
	defer srv.Close()
	h := NewHAMiMonitor(srv.URL)

	if got := h.GPUUtil(context.Background(), "GPU-aaa"); got != 0.42 {
		t.Fatalf("GPU-aaa: want 0.42, got %v", got)
	}
	if got := h.GPUUtil(context.Background(), "GPU-bbb"); got != 0 {
		t.Fatalf("GPU-bbb (idle): want 0, got %v", got)
	}
	// uuid absent from the series → busy(1.0) fail-safe (no false idle-pause).
	if got := h.GPUUtil(context.Background(), "GPU-missing"); got != 1.0 {
		t.Fatalf("absent uuid: want 1.0, got %v", got)
	}
	if got := h.GPUUtil(context.Background(), ""); got != 1.0 {
		t.Fatalf("empty uuid: want 1.0, got %v", got)
	}
}

func TestHAMiGPUUtilHTTPErrorFailSafe(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	if got := NewHAMiMonitor(srv.URL).GPUUtil(context.Background(), "GPU-aaa"); got != 1.0 {
		t.Fatalf("HTTP error must be busy(1.0), got %v", got)
	}
}
