/*
Package dcgm sources per-GPU utilization for the idle reaper.

Prometheus implements the reaper's DCGM interface by querying the DCGM exporter's
GPU-util gauge over a trailing window (max_over_time), so sub-tick bursts between
reaper samples are not misread as idle. Values are returned as a 0–1 fraction.

Fail-safe: on query error or a missing series the GPU is reported BUSY (1.0) so a
metrics outage never triggers a false idle-pause; the max-runtime cap still applies.
*/
package dcgm

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

// utilMetric is the DCGM exporter gauge (percent, 0–100); utilLabel is the GPU-UUID label.
// utilMetricMIG covers MIG-enabled cards, where DCGM_FI_DEV_GPU_UTIL is not exported: the
// graphics-engine-active ratio (0–1) per instance, max-ed over the card's instances.
const (
	utilMetric    = "DCGM_FI_DEV_GPU_UTIL"
	utilMetricMIG = "DCGM_FI_PROF_GR_ENGINE_ACTIVE"
	utilLabel     = "UUID"
)

// Prometheus queries a DCGM exporter via the Prometheus HTTP API.
type Prometheus struct {
	BaseURL string        // e.g. http://prometheus.monitoring.svc:9090
	Window  time.Duration // max_over_time look-back (slightly larger than the reaper tick)
	client  *http.Client
}

// NewPrometheus builds a source. window defaults to 90s when non-positive.
func NewPrometheus(baseURL string, window time.Duration) *Prometheus {
	if window <= 0 {
		window = 90 * time.Second
	}
	return &Prometheus{
		BaseURL: baseURL,
		Window:  window,
		client:  &http.Client{Timeout: 3 * time.Second},
	}
}

// promResponse is the subset of the Prometheus instant-query result we read.
type promResponse struct {
	Data struct {
		Result []struct {
			Value [2]any `json:"value"` // [ <ts float>, "<sample value>" ]
		} `json:"result"`
	} `json:"data"`
}

// GPUUtil returns the GPU's peak utilization over the window as a 0–1 fraction.
// Empty uuid or any error/missing series → 1.0 (busy, fail-safe).
func (p *Prometheus) GPUUtil(ctx context.Context, gpuUUID string) float64 {
	if gpuUUID == "" || p.BaseURL == "" {
		return 1.0
	}
	// On MIG-enabled cards DCGM_FI_DEV_GPU_UTIL is absent; fall back to the per-instance
	// engine-active ratio (already 0–1), max-ed across the card's instances, in ONE query so a
	// metrics round trip stays a single request either way.
	q := fmt.Sprintf(
		`max_over_time(%s{%s="%s"}[%ds]) or (100 * max(max_over_time(%s{%s="%s"}[%ds])))`,
		utilMetric, utilLabel, gpuUUID, int(p.Window.Seconds()),
		utilMetricMIG, utilLabel, gpuUUID, int(p.Window.Seconds()),
	)
	endpoint := p.BaseURL + "/api/v1/query?query=" + url.QueryEscape(q)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return 1.0
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return 1.0
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 1.0
	}

	var pr promResponse
	if err := json.NewDecoder(resp.Body).Decode(&pr); err != nil {
		return 1.0
	}
	if len(pr.Data.Result) == 0 {
		return 1.0 // series absent (UUID not scraped) → treat as busy, not idle
	}
	raw, ok := pr.Data.Result[0].Value[1].(string)
	if !ok {
		return 1.0
	}
	pct, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return 1.0
	}
	return pct / 100.0 // DCGM util is a percent; reaper compares against a fraction
}
