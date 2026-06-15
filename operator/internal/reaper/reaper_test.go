/*
Pure logic tests for workload-aware idle deferral (no envtest): drive observe() with
synthetic util samples and assert recurrence detection + effective idle window.
*/
package reaper

import (
	"testing"
	"time"

	"k8s.io/apimachinery/pkg/types"

	gsharev1 "github.com/gshare/operator/api/v1"
)

func gpuSession(uid string) *gsharev1.GShareSession {
	s := &gsharev1.GShareSession{}
	s.UID = types.UID(uid)
	s.Spec.ResourceClass = "gpu"
	return s
}

// feed replays util samples at the given minute-offsets from base (negative = past).
func feed(r *IdleReaper, uid string, base time.Time, offsetsMin []int, utils []float64) {
	for i, off := range offsetsMin {
		r.observe(uid, utils[i], base.Add(time.Duration(off)*time.Minute))
	}
}

// A periodic workload (>=2 bursts in the look-back window) currently in a trough that
// exceeds the base window but not base*multiplier must NOT pause (reuse imminent).
func TestRecurringWorkloadDefersPause(t *testing.T) {
	r := &IdleReaper{WorkloadAware: true}
	uid := "rec"
	T := time.Now()
	// bursts at -110 and -100 (2 episodes), then continuous idle from -95 → idleFor ~95m.
	offs := []int{-110, -105, -100, -95, -60, -30, 0}
	utils := []float64{0.9, 0.0, 0.8, 0.0, 0.0, 0.0, 0.0}
	feed(r, uid, T, offs, utils)
	s := gpuSession(uid)

	if !r.recurringWorkload(uid, T) {
		t.Fatalf("expected recurring workload (2 bursts in window)")
	}
	eff := r.effectiveIdleTimeout(s, T)
	if eff != defaultGPUIdleTimeout*recurringIdleMultiplier {
		t.Fatalf("expected extended window %v, got %v", defaultGPUIdleTimeout*recurringIdleMultiplier, eff)
	}
	if idle := r.idleFor(s); idle <= defaultGPUIdleTimeout || idle >= eff {
		t.Fatalf("test setup: idleFor %v should be in (base, extended)", idle)
	}
	// pause condition is idleFor > eff → here false (deferred).
	if r.idleFor(s) > eff {
		t.Fatalf("recurring workload in trough should be deferred, not paused")
	}
}

// Deferral self-limits: once the idle streak spans the whole look-back window, the past
// bursts age out and the session pauses at the base window.
func TestRecurringDeferralSelfLimits(t *testing.T) {
	r := &IdleReaper{WorkloadAware: true}
	uid := "rec2"
	T := time.Now()
	// bursts at -200/-190 are older than the 2h window relative to T → not counted.
	offs := []int{-200, -190, -180, -125, 0}
	utils := []float64{0.9, 0.0, 0.0, 0.0, 0.0}
	feed(r, uid, T, offs, utils)
	s := gpuSession(uid)

	if r.recurringWorkload(uid, T) {
		t.Fatalf("bursts older than recurrence window must not count as recurring")
	}
	if eff := r.effectiveIdleTimeout(s, T); eff != defaultGPUIdleTimeout {
		t.Fatalf("expected base window %v, got %v", defaultGPUIdleTimeout, eff)
	}
}

// A one-shot workload (single burst then idle) pauses at the base window.
func TestNonRecurringUsesBaseWindow(t *testing.T) {
	r := &IdleReaper{WorkloadAware: true}
	uid := "once"
	T := time.Now()
	offs := []int{-71, -70, -40, 0}
	utils := []float64{0.9, 0.0, 0.0, 0.0}
	feed(r, uid, T, offs, utils)
	s := gpuSession(uid)

	if r.recurringWorkload(uid, T) {
		t.Fatalf("single burst is not recurring")
	}
	eff := r.effectiveIdleTimeout(s, T)
	if eff != defaultGPUIdleTimeout {
		t.Fatalf("expected base window, got %v", eff)
	}
	if !(r.idleFor(s) > eff) {
		t.Fatalf("one-shot idle past base window should pause")
	}
}

// WorkloadAware off → never extends, even for a recurring pattern.
func TestWorkloadAwareDisabled(t *testing.T) {
	r := &IdleReaper{WorkloadAware: false}
	uid := "rec3"
	T := time.Now()
	offs := []int{-110, -105, -100, -95, 0}
	utils := []float64{0.9, 0.0, 0.8, 0.0, 0.0}
	feed(r, uid, T, offs, utils)
	s := gpuSession(uid)

	if eff := r.effectiveIdleTimeout(s, T); eff != defaultGPUIdleTimeout {
		t.Fatalf("disabled workload-awareness must use base window, got %v", eff)
	}
}

// A busy sample resets the continuous idle streak.
func TestBusyResetsStreak(t *testing.T) {
	r := &IdleReaper{WorkloadAware: true}
	uid := "reset"
	T := time.Now()
	r.observe(uid, 0.0, T.Add(-30*time.Minute)) // idle starts
	s := gpuSession(uid)
	if r.idleFor(s) < 29*time.Minute {
		t.Fatalf("expected ~30m idle streak")
	}
	r.observe(uid, 0.5, T.Add(-10*time.Minute)) // busy → reset
	if r.idleFor(s) != 0 {
		t.Fatalf("busy sample must reset idle streak, got %v", r.idleFor(s))
	}
}

// A decayed (low-EWMA), non-recurring workload reaps at the shortened (early) window.
func TestEarlyReclaimWhenDecayed(t *testing.T) {
	T := time.Now()
	// idle 40m ago → idleFor ~40m > 0.5×base(1h)=30m, ewma=0 (decayed), non-recurring.
	r := &IdleReaper{WorkloadAware: true}
	r.observe("decay", 0.0, T.Add(-40*time.Minute))
	if !r.shouldReapIdle(gpuSession("decay"), 0.0, T) {
		t.Fatalf("low-EWMA non-recurring idle past the early window should reap")
	}
	// idle only 20m (< early window) → not yet.
	r2 := &IdleReaper{WorkloadAware: true}
	r2.observe("decay", 0.0, T.Add(-20*time.Minute))
	if r2.shouldReapIdle(gpuSession("decay"), 0.0, T) {
		t.Fatalf("idle below the early window must not reap")
	}
}

// Elevated EWMA (recent activity) blocks early reclaim until the full window.
func TestNoEarlyReclaimWhenRecentlyBusy(t *testing.T) {
	T := time.Now()
	r := &IdleReaper{WorkloadAware: true}
	r.observe("b", 0.9, T.Add(-41*time.Minute)) // burst → EWMA high
	r.observe("b", 0.0, T.Add(-40*time.Minute)) // idle starts, EWMA still elevated
	if r.shouldReapIdle(gpuSession("b"), 0.0, T) {
		t.Fatalf("elevated EWMA must block early reclaim")
	}
}

// Recurring workloads never take the early-reclaim shortcut.
func TestNoEarlyReclaimForRecurring(t *testing.T) {
	T := time.Now()
	r := &IdleReaper{WorkloadAware: true}
	feed(r, "rec", T, []int{-110, -105, -100, -40}, []float64{0.9, 0.0, 0.9, 0.0})
	if r.shouldReapIdle(gpuSession("rec"), 0.0, T) {
		t.Fatalf("recurring workload must not early-reclaim")
	}
}

// Anti-thrash: a session is in cooldown within minPauseInterval of its last pause.
func TestPauseCooldown(t *testing.T) {
	T := time.Now()
	r := &IdleReaper{}
	r.recordPause("u", T.Add(-5*time.Minute))
	if !r.inPauseCooldown("u", T) {
		t.Fatalf("within minPauseInterval should be in cooldown")
	}
	r.recordPause("u", T.Add(-15*time.Minute))
	if r.inPauseCooldown("u", T) {
		t.Fatalf("past minPauseInterval should not be in cooldown")
	}
	if r.inPauseCooldown("never", T) {
		t.Fatalf("unseen session must not be in cooldown")
	}
}

// History trims samples older than the recurrence window.
func TestHistoryWindowTrim(t *testing.T) {
	r := &IdleReaper{WorkloadAware: true}
	uid := "trim"
	T := time.Now()
	r.observe(uid, 0.9, T.Add(-3*time.Hour)) // older than 2h window
	r.observe(uid, 0.9, T.Add(-10*time.Minute))
	r.observe(uid, 0.0, T)
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, sm := range r.history[uid] {
		if sm.t.Before(T.Add(-recurrenceWindow)) {
			t.Fatalf("sample older than window was not trimmed: %v", sm.t)
		}
	}
}
