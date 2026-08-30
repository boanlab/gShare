// Package volumes keeps the PVCs behind session volumes in step with the control plane's ledger.
//
// The control plane never touches the workload API, so three things about a volume's claim can
// only be done from here: read how much of it is used (kubelet volume stats), grow it when a quota
// increase was approved, and delete it — and, through the CSI driver, the data — once the ledger
// says the volume is gone and past its grace window. Every tick the syncer reports what it sees
// and executes exactly what the control plane answers; it decides nothing on its own.
package volumes

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	"k8s.io/client-go/kubernetes"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	"github.com/gshare/operator/internal/podbuilder"
	"github.com/gshare/operator/internal/sot"
)

const (
	// sessionLabel is the pod label carrying the session CR name (see podbuilder labels()).
	sessionLabel = "gshare.io/session"
	// sessionPodPrefix prefixes every session pod name: "ses-" + CR name (podbuilder podName()).
	sessionPodPrefix = "ses-"
)

// StatsFetcher returns the kubelet /stats/summary document for a node.
type StatsFetcher func(ctx context.Context, node string) ([]byte, error)

// KubeletStats reads the summary through the API server's node proxy (RBAC: nodes/proxy get).
func KubeletStats(cs kubernetes.Interface) StatsFetcher {
	return func(ctx context.Context, node string) ([]byte, error) {
		return cs.CoreV1().RESTClient().Get().
			Resource("nodes").Name(node).SubResource("proxy").Suffix("stats/summary").
			DoRaw(ctx)
	}
}

// Syncer is a manager Runnable; it runs on the leader only.
type Syncer struct {
	Client    client.Client
	SoT       sot.Reporter
	Namespace string
	Interval  time.Duration
	// Stats may be nil, in which case usage is not reported (quota growth and reclaim still work).
	Stats StatsFetcher
}

func (s *Syncer) NeedLeaderElection() bool { return true }

func (s *Syncer) Start(ctx context.Context) error {
	interval := s.Interval
	if interval <= 0 {
		interval = 5 * time.Minute
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		if err := s.Tick(ctx); err != nil && ctx.Err() == nil {
			log.FromContext(ctx).Error(err, "volume sync tick failed")
		}
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
		}
	}
}

// Tick performs one report/apply cycle.
func (s *Syncer) Tick(ctx context.Context) error {
	logger := log.FromContext(ctx).WithName("volumes")

	var pvcs corev1.PersistentVolumeClaimList
	if err := s.Client.List(ctx, &pvcs, client.InNamespace(s.Namespace), client.HasLabels{podbuilder.VolumeLabel}); err != nil {
		return fmt.Errorf("list session-volume PVCs: %w", err)
	}

	// Which claims are mounted right now, and on which nodes: only those have kubelet stats.
	var pods corev1.PodList
	if err := s.Client.List(ctx, &pods, client.InNamespace(s.Namespace)); err != nil {
		return fmt.Errorf("list session pods: %w", err)
	}
	mounted := map[string]bool{}
	nodes := map[string]struct{}{}
	// Session pods with an ephemeral-storage (scratch disk) limit: their usage rides along on
	// the same sync so the control plane can warn before the kubelet evicts. Keyed by pod name,
	// which is what the kubelet summary's podRef carries; the value keeps the CR name + limit.
	type diskPod struct {
		crName     string
		limitBytes int64
	}
	diskPods := map[string]diskPod{}
	for i := range pods.Items {
		p := &pods.Items[i]
		if p.Status.Phase == corev1.PodSucceeded || p.Status.Phase == corev1.PodFailed {
			continue
		}
		for _, v := range p.Spec.Volumes {
			if v.PersistentVolumeClaim == nil {
				continue
			}
			mounted[v.PersistentVolumeClaim.ClaimName] = true
			if p.Spec.NodeName != "" {
				nodes[p.Spec.NodeName] = struct{}{}
			}
		}
		if !strings.HasPrefix(p.Name, sessionPodPrefix) || len(p.Spec.Containers) == 0 {
			continue
		}
		limit, ok := p.Spec.Containers[0].Resources.Limits[corev1.ResourceEphemeralStorage]
		if !ok || limit.IsZero() {
			continue
		}
		crName := p.Labels[sessionLabel]
		if crName == "" {
			crName = strings.TrimPrefix(p.Name, sessionPodPrefix)
		}
		diskPods[p.Name] = diskPod{crName: crName, limitBytes: limit.Value()}
		if p.Spec.NodeName != "" {
			nodes[p.Spec.NodeName] = struct{}{}
		}
	}
	if len(pvcs.Items) == 0 && (s.Stats == nil || len(diskPods) == 0) {
		return nil
	}

	usage := map[string]int64{}
	podEphemeral := map[string]int64{}
	if s.Stats != nil {
		for node := range nodes {
			raw, err := s.Stats(ctx, node)
			if err != nil {
				logger.Info("kubelet stats unavailable; usage not reported for this node", "node", node, "err", err.Error())
				continue
			}
			for name, used := range usageFromSummary(raw, s.Namespace) {
				usage[name] = used
			}
			for name, used := range ephemeralFromSummary(raw, s.Namespace) {
				podEphemeral[name] = used
			}
		}
	}

	observed := make([]sot.VolumeObserved, 0, len(pvcs.Items))
	byName := map[string]*corev1.PersistentVolumeClaim{}
	for i := range pvcs.Items {
		p := &pvcs.Items[i]
		byName[p.Name] = p
		o := sot.VolumeObserved{
			Name:       p.Name,
			VolumeID:   p.Annotations[podbuilder.VolumeIDAnnotation],
			CapacityGb: requestGb(p),
			Mounted:    mounted[p.Name],
		}
		if used, ok := usage[p.Name]; ok {
			u := used
			o.UsedBytes = &u
		}
		observed = append(observed, o)
	}

	// Scratch-disk entries only where the kubelet actually reported usage (Stats nil, or a
	// node fetch failing, must not fabricate zero readings).
	sessions := make([]sot.SessionDisk, 0, len(diskPods))
	for podName, d := range diskPods {
		used, ok := podEphemeral[podName]
		if !ok {
			continue
		}
		sessions = append(sessions, sot.SessionDisk{
			Name:                d.crName,
			EphemeralUsedBytes:  used,
			EphemeralLimitBytes: d.limitBytes,
		})
	}
	sort.Slice(sessions, func(i, j int) bool { return sessions[i].Name < sessions[j].Name })

	res, err := s.SoT.SyncVolumes(ctx, observed, sessions)
	if err != nil {
		return fmt.Errorf("report volumes: %w", err)
	}
	logger.Info("volume sync", "claims", len(observed), "mounted", len(mounted), "nodesQueried", len(nodes),
		"withUsage", len(usage), "sessions", len(sessions), "directives", len(res.Volumes))
	if res.Orphans > 0 {
		logger.Info("PVCs without a ledger row; left untouched", "count", res.Orphans)
	}

	var firstErr error
	for _, d := range res.Volumes {
		p := byName[d.Name]
		if p == nil {
			continue
		}
		switch {
		case d.Reclaim:
			if mounted[p.Name] {
				// Deleted in the ledger but still in use: the control plane refuses deletion of a
				// mounted volume, so this is a race with a session going down. Next tick.
				logger.Info("reclaim deferred: claim still mounted", "pvc", p.Name)
				continue
			}
			if err := client.IgnoreNotFound(s.Client.Delete(ctx, p)); err != nil {
				logger.Error(err, "reclaim PVC", "pvc", p.Name)
				if firstErr == nil {
					firstErr = err
				}
				continue
			}
			logger.Info("reclaimed session volume", "pvc", p.Name, "volume", d.VolumeID)
		case d.QuotaGb != nil && *d.QuotaGb > requestGb(p):
			want := resource.MustParse(fmt.Sprintf("%dGi", *d.QuotaGb))
			patch := client.MergeFrom(p.DeepCopy())
			if p.Spec.Resources.Requests == nil {
				p.Spec.Resources.Requests = corev1.ResourceList{}
			}
			p.Spec.Resources.Requests[corev1.ResourceStorage] = want
			if err := s.Client.Patch(ctx, p, patch); err != nil {
				logger.Error(err, "grow PVC", "pvc", p.Name, "toGb", *d.QuotaGb)
				if firstErr == nil {
					firstErr = err
				}
				continue
			}
			logger.Info("grew session volume", "pvc", p.Name, "toGb", *d.QuotaGb)
		}
	}
	return firstErr
}

// requestGb is the claim's storage request in whole GiB (0 when unset).
func requestGb(p *corev1.PersistentVolumeClaim) int {
	q, ok := p.Spec.Resources.Requests[corev1.ResourceStorage]
	if !ok {
		return 0
	}
	return int(q.Value() / (1 << 30))
}

// summary is the subset of kubelet's /stats/summary this needs.
type summary struct {
	Pods []struct {
		PodRef struct {
			Name      string `json:"name"`
			Namespace string `json:"namespace"`
		} `json:"podRef"`
		EphemeralStorage *struct {
			UsedBytes *int64 `json:"usedBytes"`
		} `json:"ephemeral-storage"`
		Volume []struct {
			UsedBytes *int64 `json:"usedBytes"`
			PVCRef    *struct {
				Name      string `json:"name"`
				Namespace string `json:"namespace"`
			} `json:"pvcRef"`
		} `json:"volume"`
	} `json:"pods"`
}

// ephemeralFromSummary maps pod name -> the pod's top-level ephemeral-storage usedBytes,
// for pods in ns, from one node's summary.
func ephemeralFromSummary(raw []byte, ns string) map[string]int64 {
	out := map[string]int64{}
	var sm summary
	if err := json.Unmarshal(raw, &sm); err != nil {
		return out
	}
	for _, p := range sm.Pods {
		if p.PodRef.Namespace != ns || p.EphemeralStorage == nil || p.EphemeralStorage.UsedBytes == nil {
			continue
		}
		out[p.PodRef.Name] = *p.EphemeralStorage.UsedBytes
	}
	return out
}

// usageFromSummary maps PVC name -> used bytes for claims in ns, from one node's summary.
func usageFromSummary(raw []byte, ns string) map[string]int64 {
	out := map[string]int64{}
	var sm summary
	if err := json.Unmarshal(raw, &sm); err != nil {
		return out
	}
	for _, p := range sm.Pods {
		for _, v := range p.Volume {
			if v.PVCRef == nil || v.UsedBytes == nil || v.PVCRef.Namespace != ns {
				continue
			}
			out[v.PVCRef.Name] = *v.UsedBytes
		}
	}
	return out
}
