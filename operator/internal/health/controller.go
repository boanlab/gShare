/*
Package health implements the node health controller.

A node agent (DaemonSet) watches DCGM + NVIDIA xid/ECC/temp/power; this controller
evaluates threshold rules and on a critical breach cordons the node, creates a
NodeHealthEvent in the SoT control plane's ledger, and posts a privileged-action
audit record via SoT.AuditOperator.
*/
package health

import (
	"context"
	"strconv"
	"strings"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	"github.com/gshare/operator/internal/sot"
)

// Metrics is the DCGM-derived health snapshot for one node, sourced from a
// MetricsSource or from node-agent annotations.
type Metrics struct {
	fatalXid       bool
	recoverableXid bool
	dbeECC         int
	tempC          int
	kind           string
}

func (m Metrics) FatalXid() bool       { return m.fatalXid }
func (m Metrics) RecoverableXid() bool { return m.recoverableXid }
func (m Metrics) DoubleBitECC() int    { return m.dbeECC }
func (m Metrics) TempC() int           { return m.tempC }
func (m Metrics) Kind() string         { return m.kind }

// fatalXidCodes are the NVIDIA Xid fault codes that mandate cordon:
// double-bit ECC / GPU fall-off class faults.
var fatalXidCodes = map[int]bool{48: true, 74: true, 79: true, 94: true}

// MetricsSource pulls a node's DCGM/xid health snapshot (node-agent / DCGM-exporter
// abstraction). When no Source is wired, the controller falls back to node annotations.
type MetricsSource interface {
	// Get returns the latest health metrics for a node. ok=false means no signal is
	// available now, so the controller takes no action this reconcile.
	Get(ctx context.Context, node *corev1.Node) (Metrics, bool)
}

// Node annotations the node-agent DaemonSet publishes from DCGM/xid; the fallback
// signal when no MetricsSource is wired.
const (
	annXid    = "gshare.io/dcgm-xid"     // latest Xid fault code (int), 0/absent = none
	annECCDBE = "gshare.io/dcgm-ecc-dbe" // double-bit ECC count (int)
	annTempC  = "gshare.io/dcgm-temp-c"  // GPU temperature in Celsius (int)
)

// metricsFromAnnotations derives a Metrics snapshot from the node-agent annotations,
// setting kind to the highest-severity breaching signal. ok=false when the node carries
// no health signal at all.
func metricsFromAnnotations(node *corev1.Node) (Metrics, bool) {
	ann := node.Annotations
	if ann == nil {
		return Metrics{}, false
	}
	xidRaw, hasXid := ann[annXid]
	eccRaw, hasECC := ann[annECCDBE]
	tempRaw, hasTemp := ann[annTempC]
	if !hasXid && !hasECC && !hasTemp {
		return Metrics{}, false
	}

	m := Metrics{}
	if xid := atoiSafe(xidRaw); xid > 0 {
		if fatalXidCodes[xid] {
			m.fatalXid = true
			m.kind = "xid"
		} else {
			m.recoverableXid = true
			if m.kind == "" {
				m.kind = "xid"
			}
		}
	}
	m.dbeECC = atoiSafe(eccRaw)
	m.tempC = atoiSafe(tempRaw)

	// kind reflects the dominant breaching signal, critical first.
	switch {
	case m.dbeECC >= 1:
		m.kind = "ecc"
	case m.tempC >= 95:
		m.kind = "temp"
	case m.fatalXid:
		m.kind = "xid"
	case m.tempC >= 85:
		m.kind = "temp"
	case m.recoverableXid:
		m.kind = "xid"
	}
	return m, true
}

func atoiSafe(s string) int {
	n, err := strconv.Atoi(strings.TrimSpace(s))
	if err != nil {
		return 0
	}
	return n
}

// HealthReconciler reconciles Node health.
type HealthReconciler struct {
	client.Client
	Scheme    *runtime.Scheme
	SoT       sot.Reporter
	ClusterID string
	// Source is the DCGM/node-agent metrics abstraction. When nil the controller falls
	// back to node-agent published annotations.
	Source MetricsSource
}

// +kubebuilder:rbac:groups="",resources=nodes,verbs=get;list;watch;patch

// Reconcile evaluates a node's health metrics and acts on threshold breaches.
func (r *HealthReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	var node corev1.Node
	if err := r.Get(ctx, req.NamespacedName, &node); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	// Source metrics from the DCGM/node-agent Source, or node annotations when unset.
	// ok=false => no signal this cycle, take no action.
	var (
		m  Metrics
		ok bool
	)
	if r.Source != nil {
		m, ok = r.Source.Get(ctx, &node)
	} else {
		m, ok = metricsFromAnnotations(&node)
	}
	if ok {
		r.evaluate(ctx, &node, m)
	}
	return ctrl.Result{}, nil
}

// evaluate applies the threshold rules for one node.
func (r *HealthReconciler) evaluate(ctx context.Context, n *corev1.Node, m Metrics) {
	switch {
	case m.FatalXid() || m.DoubleBitECC() >= 1 || m.TempC() >= 95:
		// Critical -> cordon + ledger event + mandatory operator audit.
		_ = r.cordon(ctx, n)
		created, _ := r.SoT.CreateNodeHealthEvent(ctx, sot.NodeHealthEvent{
			NodeID: n.Name, Kind: m.Kind(), Severity: "critical", Action: "cordon",
		})
		_ = r.SoT.AuditOperator(ctx, sot.AuditEvent{
			Actor:  "operator:" + r.ClusterID,
			Action: "node.cordon",
			Target: n.Name,
			Result: "ok",
			Detail: map[string]any{"reason": "health:" + m.Kind(), "health_event_id": created.ID},
		})

	case m.RecoverableXid() || m.TempC() >= 85:
		// Warn -> ledger event only (no privileged action, so no audit).
		_, _ = r.SoT.CreateNodeHealthEvent(ctx, sot.NodeHealthEvent{
			NodeID: n.Name, Kind: m.Kind(), Severity: "warn", Action: "alert",
		})
	}
}

// cordon sets node.Spec.Unschedulable=true.
func (r *HealthReconciler) cordon(ctx context.Context, n *corev1.Node) error {
	patch := client.MergeFrom(n.DeepCopy())
	n.Spec.Unschedulable = true
	return r.Patch(ctx, n, patch)
}

// SetupWithManager registers the controller.
func (r *HealthReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&corev1.Node{}).
		Complete(r)
}
