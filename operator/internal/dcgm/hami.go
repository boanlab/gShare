package dcgm

import (
	"bufio"
	"context"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// hamiUtilMetric is the HAMi vGPU-monitor per-physical-GPU utilization ratio (0–1), labelled by
// device_uuid. Already present on any HAMi cluster (the device-plugin monitor Service), so the idle
// reaper can get per-GPU util without a separate Prometheus/dcgm-exporter.
const hamiUtilMetric = "hami_host_gpu_utilization_ratio"

// HAMiMonitor reads per-GPU utilization from the HAMi vGPU-monitor /metrics endpoint directly
// (no Prometheus). It implements the reaper DCGM interface; the reaper does its own time-smoothing.
//
// Fail-safe: on query error or a missing series the GPU is reported BUSY (1.0) so a monitor outage
// never triggers a false idle-pause; the max-runtime cap still applies.
type HAMiMonitor struct {
	BaseURL string // e.g. http://hami-device-plugin-monitor.kube-system.svc:31992
	client  *http.Client
}

// NewHAMiMonitor builds a source from the monitor Service URL.
func NewHAMiMonitor(baseURL string) *HAMiMonitor {
	return &HAMiMonitor{BaseURL: baseURL, client: &http.Client{Timeout: 3 * time.Second}}
}

// GPUUtil returns the GPU's utilization as a 0–1 fraction. Empty uuid or any error/missing series → 1.0.
func (h *HAMiMonitor) GPUUtil(ctx context.Context, gpuUUID string) float64 {
	if gpuUUID == "" || h.BaseURL == "" {
		return 1.0
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, h.BaseURL+"/metrics", nil)
	if err != nil {
		return 1.0
	}
	resp, err := h.client.Do(req)
	if err != nil {
		return 1.0
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 1.0
	}
	sc := bufio.NewScanner(resp.Body)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Text()
		if !strings.HasPrefix(line, hamiUtilMetric) || !strings.Contains(line, `device_uuid="`+gpuUUID+`"`) {
			continue
		}
		// "<metric>{labels} <value>" — value is the last space-separated field.
		if i := strings.LastIndexByte(line, ' '); i >= 0 {
			if v, perr := strconv.ParseFloat(strings.TrimSpace(line[i+1:]), 64); perr == nil {
				return v // ratio already 0–1
			}
		}
		return 1.0
	}
	return 1.0 // series absent for this uuid → treat as busy, not idle
}
