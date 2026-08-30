/*
Package reaper implements the idle GPU reaper.

When DCGM GPU-util stays ~0 for the idle window (or the max-runtime cap is exceeded —
policy values carried on the CR by the SoT control plane), the reaper pauses idle GPU
sessions (returns the GPU, keeps the session) or terminates CPU/max-runtime sessions by
deleting the CR; the SessionReconciler finalizer then cleans the Pod. The reaper sends
only the signal + a mandatory operator audit. Credit settle is the control plane's job.

Workload-aware deferral: a session that has shown recurring GPU bursts within the
look-back window (e.g. a training loop between epochs / a periodic job) is "reuse
imminent", so its effective idle window is extended before pausing — a single short
trough does not evict a periodic workload. Non-recurring (one-shot then idle) sessions
pause at the base window. Deferral self-limits: once the idle streak spans the whole
look-back window, no bursts remain in view and the session pauses.
*/
package reaper

import (
	"strings"
	"fmt"
	"context"
	"strconv"
	"sync"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"sigs.k8s.io/controller-runtime/pkg/client"

	gsharev1 "github.com/gshare/operator/api/v1"
	"github.com/gshare/operator/internal/sot"
)

// idleThreshold is the GPU-util fraction below which a session counts as idle.
const idleThreshold = 0.01

// idleTimeoutAnnotation carries the per-session idle policy (seconds), stamped on the CR
// by the SoT control plane. Absent or unparsable -> the resource-class default applies.
const idleTimeoutAnnotation = "gshare.io/idle-timeout-sec"

// maxRuntimeAnnotation carries an optional absolute lifetime cap (seconds); a session
// past this is reaped regardless of GPU utilization.
const maxRuntimeAnnotation = "gshare.io/max-runtime-sec"

// defaultCPUIdleTimeout is the idle-window default for CPU (free) sessions.
const defaultCPUIdleTimeout = 30 * time.Minute

// defaultGPUIdleTimeout is the fallback idle window for billed GPU sessions when
// the CR carries no explicit policy.
const defaultGPUIdleTimeout = time.Hour

// Workload-aware deferral tunables.
const (
	// pauseReasonAnnotation carries WHY the reaper paused a session; the session
	// controller forwards it as the Paused callback's message.
	pauseReasonAnnotation = "gshare.io/pause-reason"

	// recurrenceWindow is the look-back over which recurring GPU bursts are detected.
	recurrenceWindow = 2 * time.Hour
	// minBurstEpisodes is the number of distinct busy episodes in the window that
	// classifies a session as a recurring/periodic workload.
	minBurstEpisodes = 2
	// recurringIdleMultiplier scales the base idle window for recurring workloads.
	recurringIdleMultiplier = 2
	// maxSamplesPerSession bounds the per-session util history ring.
	maxSamplesPerSession = 160
	// ewmaAlpha is the smoothing factor for the per-session util EWMA (trend signal).
	ewmaAlpha = 0.4
	// earlyIdleFactor shortens the idle window for a clearly-decayed (low-EWMA),
	// non-recurring workload — reclaim sooner than the full window.
	earlyIdleFactor = 0.5
	// minPauseInterval rate-limits pauses per session (anti-thrash: avoids a pause→resume→
	// pause loop, especially with early reclaim). A session is not re-paused within this of its
	// last pause.
	minPauseInterval = 10 * time.Minute
)

// idleWarnLead is how long before an idle pause the owner gets a heads-up notification.
const idleWarnLead = 5 * time.Minute

// sample is one util observation: whether the GPU was busy at time t.
type sample struct {
	t    time.Time
	busy bool
}

// DCGM exposes GPU utilization by UUID.
type DCGM interface {
	GPUUtil(ctx context.Context, gpuUUID string) float64
}

// IdleReaper periodically terminates idle sessions.
type IdleReaper struct {
	client.Client
	DCGM      DCGM
	SoT       sot.Reporter
	ClusterID string
	// Interval between reaper ticks.
	Interval time.Duration
	// WorkloadAware enables recurring-burst idle deferral (no effect when DCGM is nil,
	// since without a util signal no bursts are ever recorded).
	WorkloadAware bool

	// idleSince tracks, per session UID, when GPU util was first seen below idleThreshold.
	// Set on the first idle observation, cleared once the session is busy again, so
	// idleFor measures a continuous idle streak. history is the per-session util ring used
	// to detect recurring workloads. ewma is the smoothed util (trend signal for early
	// reclaim). lastPausedAt rate-limits pauses (anti-thrash) and survives the paused gap.
	mu           sync.Mutex
	idleSince    map[string]time.Time
	history      map[string][]sample
	ewma         map[string]float64
	lastPausedAt map[string]time.Time
	warned       map[string]bool
}

// Start runs the reaper loop until ctx is cancelled (manager Runnable).
func (r *IdleReaper) Start(ctx context.Context) error {
	interval := r.Interval
	if interval == 0 {
		interval = time.Minute
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
			r.tick(ctx)
		}
	}
}

// tick evaluates running sessions and reaps idle ones.
func (r *IdleReaper) tick(ctx context.Context) {
	now := time.Now()
	sessions := r.runningSessions(ctx)

	// Track live session UIDs so prune() can drop idle-history for sessions that have
	// left Running, keeping the map bounded.
	live := make(map[string]struct{}, len(sessions))

	for i := range sessions {
		s := sessions[i]
		uid := string(s.UID)
		live[uid] = struct{}{}

		util := 0.0
		if r.DCGM != nil && s.Status.BoundGpuUuid != "" {
			util = r.DCGM.GPUUtil(ctx, s.Status.BoundGpuUuid)
		}

		r.observe(uid, util, now)

		reason := ""
		pause := false
		// A GPU session's idle is judged by util — with no util source it is unknowable, so do NOT
		// idle-reap GPU sessions (only max-runtime applies); else util=0 would pause every active
		// session. Unknowable means DCGM is absent OR the session has no bound GPU UUID: a card
		// outside HAMi's annotations (a MIG instance, a non-HAMi cluster) never reports a UUID, and
		// its util would read as a permanent 0 here. CPU (free) sessions have no GPU util and
		// idle-terminate on the wall-clock window.
		gpuIdleUnknowable := s.Spec.ResourceClass == "gpu" && (r.DCGM == nil || s.Status.BoundGpuUuid == "")
		switch {
		case maxRuntimeExceeded(&s, now):
			reason = "max-runtime-exceeded" // hard lifetime cap -> terminate
		case !gpuIdleUnknowable && r.shouldReapIdle(&s, util, now):
			reason = "idle-reaped"
			// Idle GPU session -> PAUSE (return the GPU, keep the session) instead of terminate.
			// CPU (free) sessions hold no GPU -> terminate. Already-paused excluded (runningSessions).
			pause = s.Spec.ResourceClass == "gpu" && !s.Spec.Paused
		}
		if reason == "" {
			// Heads-up: idle and inside the warning window before the pause would fire.
			// Only once per idle streak; any busy sample clears the streak (forgetWarn).
			if !gpuIdleUnknowable && s.Spec.ResourceClass == "gpu" && !s.Spec.Paused {
				window := r.effectiveIdleTimeout(&s, now)
				if window > 2*idleWarnLead {
					idle := r.idleFor(&s)
					if idle > 0 && idle >= window-idleWarnLead && !r.hasWarned(uid) {
						left := window - idle
						_ = r.SoT.Report(ctx, s.Name, sot.StatusEvent{
							Phase:   "IdleWarning",
							PodRef:  s.Status.PodRef,
							Message: fmt.Sprintf("%d", int(left.Seconds())),
						})
						r.markWarned(uid)
					}
				}
			}
			continue
		}

		// Anti-thrash: do not re-pause a GPU session within minPauseInterval of its last pause.
		if pause && r.inPauseCooldown(uid, now) {
			continue
		}

		if pause {
			// spec.paused=true -> reconcile deletes the Pod (frees the GPU), keeps the CR, reports
			// Paused; the control plane releases the GPU reservation + pauses billing. Resumable.
			// The pause-reason annotation rides along so the Paused callback can say WHY — the
			// control plane surfaces it to the owner ("idle GPU reclaimed").
			s.Spec.Paused = true
			if s.Annotations == nil {
				s.Annotations = map[string]string{}
			}
			s.Annotations[pauseReasonAnnotation] = reason
			if err := r.Update(ctx, &s); err != nil && !apierrors.IsNotFound(err) {
				continue // retry next tick (idle streak intact)
			}
			r.recordPause(uid, now) // anti-thrash: rate-limit the next pause
			_ = r.SoT.AuditOperator(ctx, sot.AuditEvent{
				Actor:  "operator:" + r.ClusterID,
				Action: "session.pause",
				Target: s.Name,
				Result: "ok",
				Detail: map[string]any{"reason": reason, "session": s.Name},
			})
			delete(live, uid)
			r.forget(uid)
			continue
		}

		// Terminate path (max-runtime cap, or idle CPU session): delete CR -> finalizer cleans the
		// Pod and reports Terminated (-> settle in the control plane).
		if err := r.Delete(ctx, &s); err != nil && !apierrors.IsNotFound(err) {
			continue
		}
		_ = r.SoT.Report(ctx, s.Name, sot.StatusEvent{
			Phase:   "Terminating",
			PodRef:  s.Status.PodRef,
			Message: reason,
		})
		_ = r.SoT.AuditOperator(ctx, sot.AuditEvent{
			Actor:  "operator:" + r.ClusterID,
			Action: "pod.delete",
			Target: s.Status.PodRef,
			Result: "ok",
			Detail: map[string]any{"reason": reason, "session": s.Name},
		})
		delete(live, uid)
		r.forget(uid)
	}

	r.prune(live)
}

// observe records a DCGM util sample: it starts the idle streak on the first
// sub-threshold sample and clears it once the session is busy.
func (r *IdleReaper) observe(uid string, util float64, now time.Time) {
	if uid == "" {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.idleSince == nil {
		r.idleSince = make(map[string]time.Time)
	}
	if r.history == nil {
		r.history = make(map[string][]sample)
	}
	if r.ewma == nil {
		r.ewma = make(map[string]float64)
	}

	if prev, ok := r.ewma[uid]; ok {
		r.ewma[uid] = ewmaAlpha*util + (1-ewmaAlpha)*prev
	} else {
		r.ewma[uid] = util
	}

	busy := util >= idleThreshold
	r.history[uid] = appendSample(r.history[uid], sample{t: now, busy: busy}, now)

	if !busy {
		if _, ok := r.idleSince[uid]; !ok {
			r.idleSince[uid] = now
		}
		return
	}
	// Busy: reset the streak.
	delete(r.idleSince, uid)
}

// appendSample adds s and drops samples older than the recurrence window, capping length.
func appendSample(buf []sample, s sample, now time.Time) []sample {
	cutoff := now.Add(-recurrenceWindow)
	buf = append(buf, s)
	i := 0
	for i < len(buf) && buf[i].t.Before(cutoff) {
		i++
	}
	buf = buf[i:]
	if len(buf) > maxSamplesPerSession {
		buf = buf[len(buf)-maxSamplesPerSession:]
	}
	return buf
}

// effectiveIdleTimeout is the session's idle window, extended for recurring workloads.
func (r *IdleReaper) effectiveIdleTimeout(s *gsharev1.GShareSession, now time.Time) time.Duration {
	base := idleTimeout(s)
	if r.WorkloadAware && r.recurringWorkload(string(s.UID), now) {
		return base * recurringIdleMultiplier
	}
	return base
}

// recurringWorkload reports whether the session showed >= minBurstEpisodes distinct
// GPU-busy episodes within the recurrence window (a periodic workload, reuse-imminent).
func (r *IdleReaper) recurringWorkload(uid string, now time.Time) bool {
	if uid == "" {
		return false
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	cutoff := now.Add(-recurrenceWindow)
	episodes := 0
	prevBusy := false
	for _, sm := range r.history[uid] {
		if sm.t.Before(cutoff) {
			continue
		}
		if sm.busy && !prevBusy { // rising edge = a new busy episode
			episodes++
		}
		prevBusy = sm.busy
	}
	return episodes >= minBurstEpisodes
}

// shouldReapIdle reports whether an idle GPU session should be reaped now. True when the
// continuous idle streak exceeds the (workload-aware) idle window, or — for a clearly
// decayed (low-EWMA), non-recurring workload — at a shortened window (early reclaim).
func (r *IdleReaper) shouldReapIdle(s *gsharev1.GShareSession, util float64, now time.Time) bool {
	if util >= idleThreshold {
		return false // a current activity sample → not idle
	}
	idle := r.idleFor(s)
	base := r.effectiveIdleTimeout(s, now)
	if base <= 0 { // explicit 0 = idle reaping disabled for this session
		return false
	}
	if idle > base {
		return true
	}
	if _, explicit := s.Annotations[idleTimeoutAnnotation]; !explicit &&
		r.WorkloadAware && !r.recurringWorkload(string(s.UID), now) &&
		r.ewmaUtil(string(s.UID)) < idleThreshold &&
		idle > time.Duration(float64(base)*earlyIdleFactor) {
		return true // EWMA confirms decay → reclaim sooner (fallback windows only)
	}
	return false
}

// ewmaUtil returns the smoothed util for a session (0 if unseen).
func (r *IdleReaper) ewmaUtil(uid string) float64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.ewma[uid]
}

// inPauseCooldown reports whether the session was paused within minPauseInterval (anti-thrash).
func (r *IdleReaper) inPauseCooldown(uid string, now time.Time) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	last, ok := r.lastPausedAt[uid]
	return ok && now.Sub(last) < minPauseInterval
}

// recordPause stamps the session's last pause time (survives the paused gap for cooldown).
func (r *IdleReaper) recordPause(uid string, now time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.lastPausedAt == nil {
		r.lastPausedAt = make(map[string]time.Time)
	}
	r.lastPausedAt[uid] = now
}

// forget drops the idle streak + util history/EWMA for a session UID. lastPausedAt is kept
// (the session resumes with the same UID; the cooldown must outlive the paused gap).
func (r *IdleReaper) forget(uid string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	delete(r.idleSince, uid)
	delete(r.history, uid)
	delete(r.ewma, uid)
	delete(r.warned, uid)
}

// hasWarned reports whether the current idle streak already produced a heads-up.
func (r *IdleReaper) hasWarned(uid string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.warned[uid]
}

func (r *IdleReaper) markWarned(uid string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.warned == nil {
		r.warned = make(map[string]bool)
	}
	r.warned[uid] = true
}

// prune drops per-session state for sessions no longer Running, keeping the maps bounded.
// lastPausedAt is aged out (not keyed on the live set) so a resumed session keeps its cooldown.
func (r *IdleReaper) prune(live map[string]struct{}) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for uid := range r.idleSince {
		if _, ok := live[uid]; !ok {
			delete(r.idleSince, uid)
		}
	}
	for uid := range r.history {
		if _, ok := live[uid]; !ok {
			delete(r.history, uid)
		}
	}
	for uid := range r.ewma {
		if _, ok := live[uid]; !ok {
			delete(r.ewma, uid)
		}
	}
	now := time.Now()
	for uid, t := range r.lastPausedAt {
		if now.Sub(t) > 2*minPauseInterval {
			delete(r.lastPausedAt, uid)
		}
	}
}

// runningSessions lists sessions currently in Running phase (excluding those already
// being deleted). A transient list error returns nil; the next tick retries.
func (r *IdleReaper) runningSessions(ctx context.Context) []gsharev1.GShareSession {
	var list gsharev1.GShareSessionList
	if err := r.List(ctx, &list); err != nil {
		return nil
	}
	out := make([]gsharev1.GShareSession, 0, len(list.Items))
	for i := range list.Items {
		s := list.Items[i]
		if s.Status.Phase == "Running" && s.DeletionTimestamp.IsZero() {
			out = append(out, s)
		}
	}
	return out
}

// idleFor returns how long the session has been continuously idle (0 = not idle).
func (r *IdleReaper) idleFor(s *gsharev1.GShareSession) time.Duration {
	uid := string(s.UID)
	if uid == "" {
		return 0
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	since, ok := r.idleSince[uid]
	if !ok {
		return 0
	}
	return time.Since(since)
}

// maxRuntimeExceeded reports whether the session has exceeded the absolute lifetime cap
// carried on the CR, measured from CR creation time. Absent/invalid -> no cap.
func maxRuntimeExceeded(s *gsharev1.GShareSession, now time.Time) bool {
	secs := annotationSeconds(s, maxRuntimeAnnotation)
	if secs <= 0 {
		return false
	}
	start := s.CreationTimestamp.Time
	if start.IsZero() {
		return false
	}
	return now.Sub(start) > time.Duration(secs)*time.Second
}

// idleTimeout returns the session's idle timeout from the CR annotation, falling back
// to the resource-class default (shorter for CPU sessions).
func idleTimeout(s *gsharev1.GShareSession) time.Duration {
	// An EXPLICIT "0" disables idle reaping for this session (policy idle_timeout=0 = 무제한);
	// a missing/invalid annotation still falls back to the class default.
	if raw, ok := s.Annotations[idleTimeoutAnnotation]; ok && strings.TrimSpace(raw) == "0" {
		return 0
	}
	if secs := annotationSeconds(s, idleTimeoutAnnotation); secs > 0 {
		return time.Duration(secs) * time.Second
	}
	if s.Spec.ResourceClass == "cpu" {
		return defaultCPUIdleTimeout
	}
	return defaultGPUIdleTimeout
}

// annotationSeconds parses a non-negative integer-seconds policy from a CR annotation.
// Returns 0 (meaning "unset/invalid") when the annotation is missing or unparsable.
func annotationSeconds(s *gsharev1.GShareSession, key string) int {
	if s.Annotations == nil {
		return 0
	}
	v, ok := s.Annotations[key]
	if !ok {
		return 0
	}
	n, err := strconv.Atoi(v)
	if err != nil || n < 0 {
		return 0
	}
	return n
}
