/*
Package sot is the operator's client to the Source-of-Truth control plane.

All callbacks are authed by the same short-lived internal JWT (RS256,
aud=gshare-internal), a plane-to-plane token. Key callbacks:

  - Report(StatusEvent)       -> POST /internal/sessions/{id}/status: drives
    running->consume / terminated->settle. Idempotent.
  - AuditOperator(AuditEvent) -> POST /internal/audit/operator: privileged actions
    (cordon/drain/pod-delete) into the control-plane audit_log.

The operator is only the token holder (the control plane issues+rotates it; the private
key never leaves the control plane). The operator verifies nothing and never touches
credits. trace_id (restored from CR annotation gshare.io/traceparent) rides every callback.
*/
package sot

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// Internal callback routes on the control plane. StatusPath and AuditPath are
// configurable (flags/env); these inventory/health routes are fixed, relative to BaseURL.
const (
	inventoryUpsertPath = "/internal/inventory/gpu-devices"
	inventoryNodePath   = "/internal/inventory/nodes"
	inventoryDriftPath  = "/internal/inventory/drift"
	nodeHealthPath      = "/internal/nodes/health-events"
	volumeSyncPath      = "/internal/volumes/sync"
)

// maxAttempts bounds the retry loop on 5xx / transport errors.
const maxAttempts = 4

// baseBackoff is the initial delay for the bounded exponential backoff.
const baseBackoff = 200 * time.Millisecond

// Config configures the SoT client (wired from flags/env in cmd/main.go).
type Config struct {
	// BaseURL is the control-plane base, e.g. https://api.gshare.internal (SOT_ENDPOINT).
	BaseURL string
	// StatusPath is the status callback route template (SOT_STATUS_PATH),
	// e.g. /internal/sessions/{id}/status.
	StatusPath string
	// AuditPath is the operator audit route (SOT_AUDIT_PATH), e.g. /internal/audit/operator.
	AuditPath string
	// TokenFile holds the short-lived internal JWT issued by the control plane (INTERNAL_JWT_TOKEN_FILE).
	TokenFile string
	// ClusterID identifies this operator instance (carried on every event).
	ClusterID string
	// HTTPTimeout bounds each callback request.
	HTTPTimeout time.Duration
}

// StatusEvent is the payload of POST /internal/sessions/{id}/status.
type StatusEvent struct {
	Phase        string `json:"phase"` // Pending|Preparing|Running|Terminating|Terminated|Error
	BoundGpuUUID string `json:"bound_gpu_uuid,omitempty"`
	// NodeName is the k8s node the session pod landed on (pod.spec.nodeName), reported so the
	// control plane can show WHERE a session runs — the only source for CPU sessions, which have
	// no bound GPU to derive the node from.
	NodeName string `json:"node_name,omitempty"`
	// YieldState="Yielded" on a successful in-place yield (Pod alive, VRAM evicted); empty on cold
	// pause/fallback. The control plane keys yield-vs-cold accounting on the operator's actual action.
	YieldState string    `json:"yield_state,omitempty"`
	PodRef     string    `json:"pod_ref,omitempty"`
	UsedMemMB  int       `json:"used_mem_mb,omitempty"`
	Message    string    `json:"message,omitempty"`
	TraceID    string    `json:"trace_id,omitempty"` // W3C traceparent trace-id
	ClusterID  string    `json:"cluster_id,omitempty"`
	TS         time.Time `json:"ts"`
}

// AuditEvent is the payload of POST /internal/audit/operator.
type AuditEvent struct {
	Actor   string         `json:"actor"`  // e.g. "operator:clu_..."
	Action  string         `json:"action"` // node.cordon | node.drain | pod.delete | ...
	Target  string         `json:"target"` // node name | namespace/pod | ...
	Result  string         `json:"result"` // ok | error
	Detail  map[string]any `json:"detail,omitempty"`
	TraceID string         `json:"trace_id,omitempty"`
	TS      time.Time      `json:"ts"`
}

// VolumeObserved is one session-volume PVC as the operator sees it (POST /internal/volumes/sync).
type VolumeObserved struct {
	Name       string `json:"name"`                 // PVC name (sanitized volume id)
	VolumeID   string `json:"volume_id,omitempty"`  // ledger id from the gshare.io/volume-id annotation
	CapacityGb int    `json:"capacity_gb"`          // the claim's current request
	UsedBytes  *int64 `json:"used_bytes,omitempty"` // kubelet volume stats; nil when not mounted anywhere
	Mounted    bool   `json:"mounted"`
}

// SessionDisk is one session pod's ephemeral-storage (scratch disk) reading, riding along on
// POST /internal/volumes/sync so the control plane can warn before the kubelet evicts on overuse.
type SessionDisk struct {
	Name                string `json:"name"` // CR name, i.e. the sanitized session id
	EphemeralUsedBytes  int64  `json:"ephemeral_used_bytes"`
	EphemeralLimitBytes int64  `json:"ephemeral_limit_bytes"`
}

// VolumeDirective is the control plane's answer for one observed PVC.
type VolumeDirective struct {
	Name     string `json:"name"`
	VolumeID string `json:"volume_id,omitempty"`
	QuotaGb  *int   `json:"quota_gb,omitempty"` // desired size; grow the claim up to it
	Reclaim  bool   `json:"reclaim"`            // deleted past grace: drop the PVC and its data
}

// VolumeSyncResult is the body of the sync response.
type VolumeSyncResult struct {
	Volumes []VolumeDirective `json:"volumes"`
	Orphans int               `json:"orphans"`
}

// Reporter is the interface the controllers depend on (allows a fake in tests).
type Reporter interface {
	Report(ctx context.Context, sessionID string, ev StatusEvent) error
	AuditOperator(ctx context.Context, ev AuditEvent) error
	UpsertGpuDevice(ctx context.Context, d GpuDevice) error
	UpsertNode(ctx context.Context, n Node) error
	ReportDrift(ctx context.Context, uuid string, used, total int) error
	CreateNodeHealthEvent(ctx context.Context, ev NodeHealthEvent) (NodeHealthEvent, error)
	// SyncVolumes reports every session-volume PVC (plus each session pod's scratch-disk
	// reading) and returns, per claim, the quota to grow to and whether it may be reclaimed.
	// The operator never decides either on its own.
	SyncVolumes(ctx context.Context, vols []VolumeObserved, sessions []SessionDisk) (VolumeSyncResult, error)
}

// Client is the HTTP implementation of Reporter.
type Client struct {
	cfg  Config
	http *http.Client
}

// New builds a SoT client from config.
func New(cfg Config) *Client {
	if cfg.HTTPTimeout == 0 {
		cfg.HTTPTimeout = 10 * time.Second
	}
	return &Client{
		cfg:  cfg,
		http: &http.Client{Timeout: cfg.HTTPTimeout},
	}
}

// doJSON marshals body and POSTs it to BaseURL+path with the internal-JWT bearer and
// (when set) a W3C traceparent header, retrying on 5xx / transport errors with bounded
// exponential backoff. 2xx and 409 are success (callbacks are idempotent). Returns the
// response body.
func (c *Client) doJSON(ctx context.Context, path string, body any, traceID string) ([]byte, error) {
	payload, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("sot: marshal payload for %s: %w", path, err)
	}

	url := strings.TrimRight(c.cfg.BaseURL, "/") + path

	var lastErr error
	for attempt := 0; attempt < maxAttempts; attempt++ {
		if attempt > 0 {
			// Bounded exponential backoff: 200ms, 400ms, 800ms ...
			backoff := baseBackoff << uint(attempt-1)
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-time.After(backoff):
			}
		}

		// Read the token on every attempt; the control plane rotates it on disk.
		token, terr := c.bearerToken()
		if terr != nil {
			lastErr = fmt.Errorf("sot: read bearer token: %w", terr)
			continue
		}

		req, rerr := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(payload))
		if rerr != nil {
			return nil, fmt.Errorf("sot: build request for %s: %w", path, rerr)
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+token)
		if tp := traceparentHeader(traceID); tp != "" {
			req.Header.Set("traceparent", tp)
		}

		resp, derr := c.http.Do(req)
		if derr != nil {
			// Transport-level failure: retry (bounded).
			lastErr = fmt.Errorf("sot: POST %s: %w", path, derr)
			continue
		}

		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		_ = resp.Body.Close()

		switch {
		case resp.StatusCode >= 200 && resp.StatusCode < 300:
			return respBody, nil
		case resp.StatusCode == http.StatusConflict:
			// 409 == already applied; idempotent success.
			return respBody, nil
		case resp.StatusCode >= 500:
			// Server-side / transient: retry.
			lastErr = fmt.Errorf("sot: POST %s: status %d: %s", path, resp.StatusCode, truncate(respBody))
			continue
		default:
			// 4xx (other than 409): caller/payload error — do not retry.
			return nil, fmt.Errorf("sot: POST %s: status %d: %s", path, resp.StatusCode, truncate(respBody))
		}
	}

	if lastErr == nil {
		lastErr = fmt.Errorf("sot: POST %s: exhausted %d attempts", path, maxAttempts)
	}
	return nil, lastErr
}

// truncate keeps error messages bounded when echoing response bodies.
func truncate(b []byte) string {
	const max = 256
	if len(b) > max {
		return string(b[:max]) + "..."
	}
	return string(b)
}

// traceparentHeader reconstructs a W3C traceparent header from a 32-hex trace-id,
// synthesizing a parent-id so the header is well-formed. "" when not a valid 32-hex value.
func traceparentHeader(traceID string) string {
	if len(traceID) != 32 || !isHex(traceID) {
		return ""
	}
	// version "00", 16-hex parent-id, sampled flag "01".
	return "00-" + traceID + "-" + parentID(traceID) + "-01"
}

// parentID derives a deterministic 16-hex span-id from the trace-id. Not cryptographic;
// it only satisfies the traceparent grammar (the trace-id is the correlation key).
func parentID(traceID string) string {
	return traceID[len(traceID)-16:]
}

func isHex(s string) bool {
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
			return false
		}
	}
	return true
}

// Report posts a session phase transition to the control plane (idempotent).
func (c *Client) Report(ctx context.Context, sessionID string, ev StatusEvent) error {
	if ev.TS.IsZero() {
		ev.TS = time.Now().UTC()
	}
	ev.ClusterID = c.cfg.ClusterID
	// Render the status route template, e.g. {id} -> ses_123.
	path := strings.ReplaceAll(c.cfg.StatusPath, "{id}", sessionID)
	_, err := c.doJSON(ctx, path, ev, ev.TraceID)
	return err
}

// AuditOperator posts a privileged-action audit record to the control plane.
func (c *Client) AuditOperator(ctx context.Context, ev AuditEvent) error {
	if ev.TS.IsZero() {
		ev.TS = time.Now().UTC()
	}
	_, err := c.doJSON(ctx, c.cfg.AuditPath, ev, ev.TraceID)
	return err
}

// bearerToken reads the current short-lived internal JWT from disk (issued+rotated by
// the control plane; the operator never signs).
func (c *Client) bearerToken() (string, error) {
	b, err := os.ReadFile(c.cfg.TokenFile)
	if err != nil {
		return "", err
	}
	token := strings.TrimSpace(string(b))
	if token == "" {
		return "", fmt.Errorf("sot: empty internal JWT in %s", c.cfg.TokenFile)
	}
	// Best-effort exp check (no verification): an expired token is surfaced as an error
	// so the retry loop re-reads the file and picks up a rotated token.
	if exp, ok := tokenExpiry(token); ok && time.Now().After(exp) {
		return "", fmt.Errorf("sot: internal JWT expired at %s (awaiting rotation)", exp.UTC())
	}
	return token, nil
}

// tokenExpiry decodes (does not verify) a JWT's claims to read "exp".
// ok=false when exp cannot be read.
func tokenExpiry(token string) (time.Time, bool) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return time.Time{}, false
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return time.Time{}, false
	}
	var claims struct {
		Exp int64 `json:"exp"`
	}
	if err := json.Unmarshal(raw, &claims); err != nil || claims.Exp == 0 {
		return time.Time{}, false
	}
	return time.Unix(claims.Exp, 0), true
}

// --- Inventory + health reporting -------------------------------------------

// GpuDevice is one device reported to the control plane's ledger.
type GpuDevice struct {
	NodeID     string `json:"node_id"`
	UUID       string `json:"uuid"`
	Model      string `json:"model,omitempty"`
	Mode       string `json:"mode"`
	TotalMemMB int    `json:"total_mem_mb"`
	UsedMemMB  int    `json:"used_mem_mb"`
	TotalCores int    `json:"total_cores"`
	UsedCores  int    `json:"used_cores"`
	Status     string `json:"status"`
	NodeCPU    int    `json:"node_cpu,omitempty"`     // node CPU cores
	NodeMemGB  int    `json:"node_mem_gb,omitempty"`  // node memory (GiB)
	NodeDiskGB int    `json:"node_disk_gb,omitempty"` // node ephemeral-storage (GiB)
}

// NodeHealthEvent is a health transition reported to the control plane.
type NodeHealthEvent struct {
	ID       string `json:"id,omitempty"`
	NodeID   string `json:"node_id"`
	Kind     string `json:"kind"`     // xid | ecc | temp | down
	Severity string `json:"severity"` // warn | critical
	Action   string `json:"action"`   // cordon | alert
}

// UpsertGpuDevice reflects measured device inventory (total/used) into the control
// plane's ledger, which remains the single source of truth.
func (c *Client) UpsertGpuDevice(ctx context.Context, d GpuDevice) error {
	body := struct {
		GpuDevice
		ClusterID string `json:"cluster_id,omitempty"`
	}{GpuDevice: d, ClusterID: c.cfg.ClusterID}
	_, err := c.doJSON(ctx, inventoryUpsertPath, body, "")
	return err
}

// Node reports node capacity regardless of GPUs (CPU-only nodes report cpu/mem/disk too).
type Node struct {
	NodeID     string `json:"node_id"`
	NodeCPU    int    `json:"node_cpu,omitempty"`
	NodeMemGB  int    `json:"node_mem_gb,omitempty"`
	NodeDiskGB int    `json:"node_disk_gb,omitempty"`
	// Role classifies the node for the console: master | gpu | cpu | storage.
	Role string `json:"role,omitempty"`
	// LosslessCapable: true when the node labels mark lossless-pause prerequisites (cuda-checkpoint + CRIU) ready.
	LosslessCapable bool `json:"lossless_capable,omitempty"`
}

func (c *Client) UpsertNode(ctx context.Context, n Node) error {
	body := struct {
		Node
		ClusterID string `json:"cluster_id,omitempty"`
	}{Node: n, ClusterID: c.cfg.ClusterID}
	_, err := c.doJSON(ctx, inventoryNodePath, body, "")
	return err
}

// SyncVolumes posts the observed PVCs (and session scratch-disk readings) and decodes the
// directives.
func (c *Client) SyncVolumes(ctx context.Context, vols []VolumeObserved, sessions []SessionDisk) (VolumeSyncResult, error) {
	body := struct {
		Volumes   []VolumeObserved `json:"volumes"`
		Sessions  []SessionDisk    `json:"sessions"`
		ClusterID string           `json:"cluster_id,omitempty"`
	}{Volumes: vols, Sessions: sessions, ClusterID: c.cfg.ClusterID}
	if body.Volumes == nil {
		body.Volumes = []VolumeObserved{}
	}
	if body.Sessions == nil {
		body.Sessions = []SessionDisk{}
	}
	raw, err := c.doJSON(ctx, volumeSyncPath, body, "")
	if err != nil {
		return VolumeSyncResult{}, err
	}
	var out VolumeSyncResult
	if err := json.Unmarshal(raw, &out); err != nil {
		return VolumeSyncResult{}, fmt.Errorf("sot: decode volume sync response: %w", err)
	}
	return out, nil
}

// ReportDrift signals Σused > total drift so the control plane can correct toward the
// truth (the operator never mutates the ledger directly).
func (c *Client) ReportDrift(ctx context.Context, uuid string, used, total int) error {
	body := struct {
		ClusterID  string `json:"cluster_id,omitempty"`
		UUID       string `json:"uuid"`
		UsedMemMB  int    `json:"used_mem_mb"`
		TotalMemMB int    `json:"total_mem_mb"`
	}{
		ClusterID:  c.cfg.ClusterID,
		UUID:       uuid,
		UsedMemMB:  used,
		TotalMemMB: total,
	}
	_, err := c.doJSON(ctx, inventoryDriftPath, body, "")
	return err
}

// CreateNodeHealthEvent records a node health transition in the control plane's ledger
// and returns the created event (with its server-assigned ID) so the caller can attach
// it to the privileged-action audit record.
func (c *Client) CreateNodeHealthEvent(ctx context.Context, ev NodeHealthEvent) (NodeHealthEvent, error) {
	body := struct {
		NodeHealthEvent
		ClusterID string `json:"cluster_id,omitempty"`
	}{NodeHealthEvent: ev, ClusterID: c.cfg.ClusterID}

	respBody, err := c.doJSON(ctx, nodeHealthPath, body, "")
	if err != nil {
		return ev, err
	}

	// Echo back the created event; the control plane returns it with an assigned ID.
	created := ev
	if len(respBody) > 0 {
		var decoded NodeHealthEvent
		if jerr := json.Unmarshal(respBody, &decoded); jerr == nil {
			created = decoded
			// Preserve operator-side fields if the server omitted them.
			if created.NodeID == "" {
				created.NodeID = ev.NodeID
			}
			if created.Kind == "" {
				created.Kind = ev.Kind
			}
			if created.Severity == "" {
				created.Severity = ev.Severity
			}
			if created.Action == "" {
				created.Action = ev.Action
			}
		}
	}
	return created, nil
}

// ImageBuildStatusEvent is the payload of POST /internal/image-builds/{id}/status.
type ImageBuildStatusEvent struct {
	Phase    string `json:"phase"` // queued|running|succeeded|failed
	ImageRef string `json:"image_ref,omitempty"`
	Error    string `json:"error,omitempty"`
	LogTail  string `json:"log_tail,omitempty"`
}

// ReportImageBuild delivers a kaniko build's progress/outcome to the control plane.
// Same retry/idempotency semantics as Report.
func (c *Client) ReportImageBuild(ctx context.Context, buildID string, ev ImageBuildStatusEvent) error {
	if len(ev.LogTail) > 16384 {
		ev.LogTail = ev.LogTail[len(ev.LogTail)-16384:]
	}
	_, err := c.doJSON(ctx, "/internal/image-builds/"+buildID+"/status", ev, "")
	return err
}
