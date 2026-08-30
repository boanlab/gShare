/*
GShare cluster operator manager entrypoint.

Boots the controller-runtime manager, registers the SessionReconciler +
Inventory/Health controllers + IdleReaper runnable, and wires the SoT client from
flags/env.
*/
package main

import (
	"context"
	"flag"
	"k8s.io/client-go/kubernetes"
	"os"
	"strings"
	"time"

	admissionregistrationv1 "k8s.io/api/admissionregistration/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"
	ctrlwebhook "sigs.k8s.io/controller-runtime/pkg/webhook"
	"sigs.k8s.io/controller-runtime/pkg/webhook/admission"

	gsharev1 "github.com/gshare/operator/api/v1"
	"github.com/gshare/operator/internal/checkpoint"
	"github.com/gshare/operator/internal/controller"
	"github.com/gshare/operator/internal/dcgm"
	"github.com/gshare/operator/internal/health"
	"github.com/gshare/operator/internal/imagebuild"
	"github.com/gshare/operator/internal/inventory"
	"github.com/gshare/operator/internal/migagent"
	"github.com/gshare/operator/internal/podbuilder"
	"github.com/gshare/operator/internal/reaper"
	"github.com/gshare/operator/internal/sot"
	"github.com/gshare/operator/internal/volumes"
	whk "github.com/gshare/operator/internal/webhook"
)

var (
	scheme   = runtime.NewScheme()
	setupLog = ctrl.Log.WithName("setup")
)

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(gsharev1.AddToScheme(scheme))
}

// Leader election: the manager holds a Lease in its own namespace and records the handover on
// an Event. Only needed with --leader-elect, but the ClusterRole is static.
// +kubebuilder:rbac:groups=coordination.k8s.io,resources=leases,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch

func main() {
	var (
		metricsAddr          string
		probeAddr            string
		enableLeaderElection bool
		clusterID            string
		sessionNamespace     string
		ingressClass         string
		connectVerifyURL     string
		sessionDomain        string

		// SoT control plane wiring.
		sotEndpoint   string
		sotStatusPath string
		sotAuditPath  string
		jwtTokenFile  string

		// lossless-pause. Empty agent image → checkpointer disabled → cold pause fallback.
		losslessAgentImage string
		losslessNamespace  string
		losslessSA         string
		losslessPVC        string

		// Idle reaper GPU-util source. Empty → no util signal → GPU idle-reaping disabled (max-runtime only).
		prometheusURL  string
		hamiMonitorURL string

		// Route borrow Pods through the GShare-patched HAMi extender (else device-plugin bypass).
		hamiYieldExtender bool

		// Session container image pull policy (IfNotPresent default; Always re-checks the registry).
		sessionImagePullPolicy string

		// Per-card pools: hami-scheduler + ledger UUID pin for every GPU session.
		perCardMode bool

		// mig-agent image for GpuModeChange (hami-core<->mig card transitions); empty disables it.
		migAgentImage         string
		kanikoImage           string
		buildInsecureRegistry bool

		// StorageClass for session-volume PVCs; empty uses the cluster default.
		volumeStorageClass string
		volumeSyncInterval time.Duration

		// Pod lend-guard ValidatingWebhook (defense-in-depth over the backend ledger).
		webhookEnabled    bool
		webhookPort       int
		webhookCertDir    string
		webhookService    string
		webhookConfigName string
		systemNamespace   string
	)

	flag.StringVar(&metricsAddr, "metrics-bind-address", ":8080", "Metrics endpoint bind address.")
	flag.StringVar(&probeAddr, "health-probe-bind-address", ":8081", "Health probe bind address.")
	flag.BoolVar(&enableLeaderElection, "leader-elect", false,
		"Enable leader election (replicas:1 single active).")
	flag.StringVar(&clusterID, "cluster-id", os.Getenv("CLUSTER_ID"),
		"Cluster identifier for this operator instance (multi-cluster, no federation).")
	flag.StringVar(&sessionNamespace, "session-namespace", os.Getenv("SESSION_NAMESPACE"),
		"Namespace where session children are created.")
	flag.StringVar(&ingressClass, "ingress-class", "nginx", "Ingress class.")
	flag.StringVar(&connectVerifyURL, "connect-verify-url", os.Getenv("CONNECT_VERIFY_URL"),
		"SoT control plane auth-url for ingress (/internal/connect/verify).")
	flag.StringVar(&sessionDomain, "session-domain", os.Getenv("SESSION_DOMAIN"),
		"Console domain for path-based session routing ({domain}/proxy/{cr-name}/...).")

	flag.StringVar(&sotEndpoint, "sot-endpoint", os.Getenv("SOT_ENDPOINT"),
		"SoT base URL, e.g. https://api.gshare.internal.")
	flag.StringVar(&sotStatusPath, "sot-status-path", envOr("SOT_STATUS_PATH", "/internal/sessions/{id}/status"),
		"Signed status callback route template.")
	flag.StringVar(&sotAuditPath, "sot-audit-path", envOr("SOT_AUDIT_PATH", "/internal/audit/operator"),
		"Operator audit callback route.")
	flag.StringVar(&jwtTokenFile, "internal-jwt-token-file", envOr("INTERNAL_JWT_TOKEN_FILE", "/var/run/gshare/internal-jwt"),
		"Path to the short-lived internal JWT issued by the SoT control plane (RS256, aud=gshare-internal).")
	flag.StringVar(&losslessAgentImage, "lossless-agent-image", os.Getenv("LOSSLESS_AGENT_IMAGE"),
		"gshare-lossless-agent image (criu + cuda-checkpoint). Empty disables lossless pause (cold fallback).")
	flag.StringVar(&losslessNamespace, "lossless-namespace", envOr("LOSSLESS_NAMESPACE", "gshare-infra"),
		"Privileged namespace for the lossless-pause agent Job.")
	flag.StringVar(&losslessSA, "lossless-service-account", envOr("LOSSLESS_SERVICE_ACCOUNT", "gshare-lossless-agent"),
		"Privileged ServiceAccount for the agent Job.")
	flag.StringVar(&losslessPVC, "lossless-checkpoint-pvc", os.Getenv("LOSSLESS_CHECKPOINT_PVC"),
		"RWX PVC (in the agent namespace) that stores checkpoints.")
	flag.StringVar(&prometheusURL, "prometheus-url", os.Getenv("PROMETHEUS_URL"),
		"Prometheus base URL for per-GPU DCGM util (idle reaper).")
	flag.StringVar(&hamiMonitorURL, "hami-monitor-url", os.Getenv("HAMI_MONITOR_URL"),
		"HAMi vGPU-monitor URL for per-GPU util (idle reaper) — works without a Prometheus. Preferred over --prometheus-url.")
	flag.BoolVar(&hamiYieldExtender, "hami-yield-extender", os.Getenv("HAMI_YIELD_EXTENDER") == "true",
		"Route borrow Pods through the GShare-patched HAMi extender (fractional/accounted). Off → device-plugin bypass.")
	flag.StringVar(&sessionImagePullPolicy, "session-image-pull-policy", envOr("SESSION_IMAGE_PULL_POLICY", "IfNotPresent"),
		"imagePullPolicy for session containers: IfNotPresent (default; supports locally imported images) or Always.")
	flag.BoolVar(&perCardMode, "per-card-mode", os.Getenv("PER_CARD_MODE") == "true",
		"Per-card GPU pools: drop the gshare.io/gpu-mode nodeSelector, route every GPU session through hami-scheduler pinned to the ledger-reserved card (spec.pinnedGpuUuid); exclusive becomes a 100% HAMi slice.")
	flag.StringVar(&migAgentImage, "mig-agent-image", os.Getenv("MIG_AGENT_IMAGE"),
		"gshare-mig-agent image executing GpuModeChange card transitions (nvidia-smi -mig). Empty disables the controller.")
	flag.StringVar(&kanikoImage, "kaniko-image", os.Getenv("KANIKO_IMAGE"),
		"kaniko executor image for console image builds (GShareImageBuild). Empty fails builds fast.")
	flag.BoolVar(&buildInsecureRegistry, "build-insecure-registry", os.Getenv("BUILD_INSECURE_REGISTRY") == "true",
		"pass --insecure/--skip-tls-verify to kaniko for plain-HTTP registries.")
	flag.DurationVar(&volumeSyncInterval, "volume-sync-interval", envDurationOr("VOLUME_SYNC_INTERVAL", 5*time.Minute),
		"How often session-volume PVCs are reported to the control plane (usage, quota growth, reclaim). 0 disables.")
	flag.StringVar(&volumeStorageClass, "volume-storage-class", os.Getenv("VOLUME_STORAGE_CLASS"),
		"StorageClass for the PVCs backing session volumes; empty uses the cluster default.")
	flag.BoolVar(&webhookEnabled, "webhook-enabled", os.Getenv("WEBHOOK_ENABLED") == "true",
		"Enable the pod lend-guard ValidatingWebhook (admission-layer borrow invariant).")
	flag.IntVar(&webhookPort, "webhook-port", 9443, "Webhook server bind port.")
	flag.StringVar(&webhookCertDir, "webhook-cert-dir", "/tmp/gshare-webhook-certs",
		"Directory for the self-generated webhook serving cert.")
	flag.StringVar(&webhookService, "webhook-service", envOr("WEBHOOK_SERVICE", "gshare-webhook"),
		"Webhook Service name (for the self-signed cert SANs).")
	flag.StringVar(&webhookConfigName, "webhook-config-name", envOr("WEBHOOK_CONFIG_NAME", "gshare-pod-lend-guard"),
		"ValidatingWebhookConfiguration name to inject the caBundle into.")
	flag.StringVar(&systemNamespace, "system-namespace", os.Getenv("SYSTEM_NAMESPACE"),
		"Namespace the operator runs in (webhook Service namespace / cert SANs).")

	// Production default: JSON encoder / Info level. --zap-devel enables console/debug logging.
	opts := zap.Options{Development: false}
	opts.BindFlags(flag.CommandLine)
	flag.Parse()
	ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)))

	mgrOpts := ctrl.Options{
		Scheme:                 scheme,
		Metrics:                metricsserver.Options{BindAddress: metricsAddr},
		HealthProbeBindAddress: probeAddr,
		LeaderElection:         enableLeaderElection,
		// Lease name must be a DNS-1123 subdomain, so sanitize the cluster-id for the
		// lease NAME only; it is carried verbatim on data callbacks.
		LeaderElectionID: "gshare-operator." + dnsSafe(clusterID),
	}
	if webhookEnabled {
		mgrOpts.WebhookServer = ctrlwebhook.NewServer(ctrlwebhook.Options{Port: webhookPort, CertDir: webhookCertDir})
	}
	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), mgrOpts)
	if err != nil {
		setupLog.Error(err, "unable to start manager")
		os.Exit(1)
	}

	// SoT client (control-plane callbacks; operator is token holder only).
	sotClient := sot.New(sot.Config{
		BaseURL:    sotEndpoint,
		StatusPath: sotStatusPath,
		AuditPath:  sotAuditPath,
		TokenFile:  jwtTokenFile,
		ClusterID:  clusterID,
	})

	builder := &podbuilder.Builder{
		Namespace:              sessionNamespace,
		IngressClass:           ingressClass,
		ConnectVerifyURL:       connectVerifyURL,
		SessionDomain:          sessionDomain,
		HAMiYieldExtender:      hamiYieldExtender,
		SessionImagePullPolicy: corev1.PullPolicy(sessionImagePullPolicy),
		PerCardMode:            perCardMode,
	}

	// lossless-pause checkpointer: enabled only when an agent image is configured (else nil → cold pause).
	var checkpointer controller.Checkpointer
	if losslessAgentImage != "" {
		checkpointer = &checkpoint.JobCheckpointer{
			Client:         mgr.GetClient(),
			AgentImage:     losslessAgentImage,
			Namespace:      losslessNamespace,
			ServiceAccount: losslessSA,
			CheckpointPVC:  losslessPVC,
		}
		setupLog.Info("lossless-pause enabled", "agentImage", losslessAgentImage, "namespace", losslessNamespace)
	}

	if err := (&controller.SessionReconciler{
		VolumeStorageClass: volumeStorageClass,
		Client:             mgr.GetClient(),
		EventReader:        mgr.GetAPIReader(),
		Scheme:             mgr.GetScheme(),
		Builder:            builder,
		SoT:                sotClient,
		ClusterID:          clusterID,
		Checkpointer:       checkpointer,
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "GShareSession")
		os.Exit(1)
	}

	if err := (&inventory.InventoryReconciler{
		Client:    mgr.GetClient(),
		Scheme:    mgr.GetScheme(),
		SoT:       sotClient,
		ClusterID: clusterID,
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "Inventory")
		os.Exit(1)
	}

	if err := (&health.HealthReconciler{
		Client:    mgr.GetClient(),
		Scheme:    mgr.GetScheme(),
		SoT:       sotClient,
		ClusterID: clusterID,
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "Health")
		os.Exit(1)
	}

	// GpuModeChange: card-level hami-core<->mig transitions through a privileged node Job.
	// Registered regardless so Pending resources stay visible; a Job only runs with an image.
	if err := (&migagent.Reconciler{
		Client:                mgr.GetClient(),
		AgentImage:            migAgentImage,
		Namespace:             losslessNamespace, // same privileged namespace + SA as the checkpointer
		ServiceAccount:        losslessSA,
		DevicePluginNamespace: "kube-system",
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "GpuModeChange")
		os.Exit(1)
	}
	if migAgentImage != "" {
		setupLog.Info("mig-agent enabled", "image", migAgentImage)
	}

	// GShareImageBuild: console image builds through kaniko Jobs in the infra namespace.
	// Registered regardless so queued builds fail fast (with a clear message) when unconfigured.
	buildClientset, bcErr := kubernetes.NewForConfig(mgr.GetConfig())
	if bcErr != nil {
		setupLog.Error(bcErr, "unable to build clientset for build logs")
		os.Exit(1)
	}
	if err := (&imagebuild.Reconciler{
		Client:           mgr.GetClient(),
		KanikoImage:      kanikoImage,
		Namespace:        losslessNamespace, // gshare-infra: kaniko's root user cannot pass the sessions namespace's restricted PSS
		ServiceAccount:   "default",         // the build Job needs no API permissions
		InsecureRegistry: buildInsecureRegistry,
		SoT:              sotClient,
		ReadPodLog:       imagebuild.ClientsetLogReader(buildClientset),
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "GShareImageBuild")
		os.Exit(1)
	}
	if kanikoImage != "" {
		setupLog.Info("image-builds enabled", "kanikoImage", kanikoImage, "insecureRegistry", buildInsecureRegistry)
	}

	reaperRunnable := &reaper.IdleReaper{
		Client:    mgr.GetClient(),
		SoT:       sotClient,
		ClusterID: clusterID,
		Interval:  time.Minute,
	}
	// With a util source, idle detection is activity-based + workload-aware (recurring bursts defer the
	// pause). Prometheus is preferred when configured: the DCGM exporter's series cover the whole
	// fleet, while the HAMi monitor URL is usually a SERVICE that round-robins across per-node
	// device-plugin pods — on a multi-GPU-node cluster most queries then land on a pod that does not
	// know the UUID, and the busy(1.0) fail-safe resets the idle streak forever (no idle-pause, no
	// warning). HAMi stays as the no-Prometheus fallback and is reliable on single-GPU-node setups.
	// With no source, GPU idle-reaping is disabled (reaper guard) so active sessions are never paused.
	switch {
	case prometheusURL != "":
		reaperRunnable.DCGM = dcgm.NewPrometheus(prometheusURL, 90*time.Second)
		reaperRunnable.WorkloadAware = true
	case hamiMonitorURL != "":
		reaperRunnable.DCGM = dcgm.NewHAMiMonitor(hamiMonitorURL)
		reaperRunnable.WorkloadAware = true
	}
	if err := mgr.Add(reaperRunnable); err != nil {
		setupLog.Error(err, "unable to add reaper")
		os.Exit(1)
	}

	// Session-volume PVCs: usage (kubelet volume stats) up to the ledger, approved quota growth and
	// post-grace reclaim back down. The control plane never touches PVCs itself.
	if volumeSyncInterval > 0 {
		clientset, cerr := kubernetes.NewForConfig(mgr.GetConfig())
		if cerr != nil {
			setupLog.Error(cerr, "unable to build clientset for kubelet stats")
			os.Exit(1)
		}
		if err := mgr.Add(&volumes.Syncer{
			Client:    mgr.GetClient(),
			SoT:       sotClient,
			Namespace: sessionNamespace,
			Interval:  volumeSyncInterval,
			Stats:     volumes.KubeletStats(clientset),
		}); err != nil {
			setupLog.Error(err, "unable to add volume syncer")
			os.Exit(1)
		}
	}

	if webhookEnabled {
		// Self-managed serving cert: generate for the Service DNS, then inject the caBundle into
		// the ValidatingWebhookConfiguration (no cert-manager dependency).
		dnsNames := whk.ServiceDNSNames(webhookService, systemNamespace)
		caPEM, gerr := whk.GenerateSelfSignedCert(webhookCertDir, dnsNames, time.Now().AddDate(1, 0, 0))
		if gerr != nil {
			setupLog.Error(gerr, "unable to generate webhook cert")
			os.Exit(1)
		}
		mgr.GetWebhookServer().Register("/validate-pod", &admission.Webhook{Handler: &whk.PodLendGuard{
			Client:           mgr.GetClient(),
			Decoder:          admission.NewDecoder(scheme),
			SessionNamespace: sessionNamespace,
		}})
		// Patch the caBundle with an uncached client (the manager cache is not started yet).
		if c, cerr := client.New(mgr.GetConfig(), client.Options{Scheme: scheme}); cerr != nil {
			setupLog.Error(cerr, "webhook caBundle client; webhook will not be called until caBundle is set")
		} else if perr := injectCABundle(context.Background(), c, webhookConfigName, caPEM); perr != nil {
			setupLog.Error(perr, "inject webhook caBundle; webhook will not be called until caBundle is set")
		} else {
			setupLog.Info("pod lend-guard webhook enabled", "service", webhookService, "config", webhookConfigName)
		}
	}

	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up health check")
		os.Exit(1)
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up ready check")
		os.Exit(1)
	}

	setupLog.Info("starting manager", "clusterID", clusterID)
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		setupLog.Error(err, "problem running manager")
		os.Exit(1)
	}
}

// injectCABundle sets the caBundle on every webhook of the named ValidatingWebhookConfiguration.
// +kubebuilder:rbac:groups=admissionregistration.k8s.io,resources=validatingwebhookconfigurations,verbs=get;list;watch;update
func injectCABundle(ctx context.Context, c client.Client, name string, caPEM []byte) error {
	var vwc admissionregistrationv1.ValidatingWebhookConfiguration
	if err := c.Get(ctx, client.ObjectKey{Name: name}, &vwc); err != nil {
		return err
	}
	for i := range vwc.Webhooks {
		vwc.Webhooks[i].ClientConfig.CABundle = caPEM
	}
	return c.Update(ctx, &vwc)
}

func envDurationOr(key string, def time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return def
}

func envOr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// dnsSafe maps a cluster-id to a DNS-1123-subdomain-safe token for the leader-election
// lease name: lowercase, any char outside [a-z0-9-] replaced with '-', edges trimmed.
func dnsSafe(s string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(s) {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '-' {
			b.WriteRune(r)
		} else {
			b.WriteRune('-')
		}
	}
	out := strings.Trim(b.String(), "-")
	if out == "" {
		out = "default"
	}
	return out
}
