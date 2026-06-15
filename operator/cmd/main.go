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
	"os"
	"strings"
	"time"

	admissionregistrationv1 "k8s.io/api/admissionregistration/v1"
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
	"github.com/gshare/operator/internal/inventory"
	"github.com/gshare/operator/internal/podbuilder"
	"github.com/gshare/operator/internal/reaper"
	"github.com/gshare/operator/internal/sot"
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
		jwksURL       string

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
	flag.StringVar(&jwksURL, "internal-jwks-url", os.Getenv("INTERNAL_JWKS_URL"),
		"SoT control plane internal JWKS URL (verification only; operator holds no private key).")
	flag.StringVar(&prometheusURL, "prometheus-url", os.Getenv("PROMETHEUS_URL"),
		"Prometheus base URL for per-GPU DCGM util (idle reaper).")
	flag.StringVar(&hamiMonitorURL, "hami-monitor-url", os.Getenv("HAMI_MONITOR_URL"),
		"HAMi vGPU-monitor URL for per-GPU util (idle reaper) — works without a Prometheus. Preferred over --prometheus-url.")
	flag.BoolVar(&hamiYieldExtender, "hami-yield-extender", os.Getenv("HAMI_YIELD_EXTENDER") == "true",
		"Route borrow Pods through the GShare-patched HAMi extender (fractional/accounted). Off → device-plugin bypass.")
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
		Namespace:         sessionNamespace,
		IngressClass:      ingressClass,
		ConnectVerifyURL:  connectVerifyURL,
		SessionDomain:     sessionDomain,
		HAMiYieldExtender: hamiYieldExtender,
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
		Client:       mgr.GetClient(),
		Scheme:       mgr.GetScheme(),
		Builder:      builder,
		SoT:          sotClient,
		ClusterID:    clusterID,
		Checkpointer: checkpointer,
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

	reaperRunnable := &reaper.IdleReaper{
		Client:    mgr.GetClient(),
		SoT:       sotClient,
		ClusterID: clusterID,
		Interval:  time.Minute,
	}
	// With a util source, idle detection is activity-based + workload-aware (recurring bursts defer the
	// pause). The HAMi monitor (already on any HAMi cluster) is preferred — no Prometheus needed. With
	// no source, GPU idle-reaping is disabled (reaper guard) so active sessions are never paused.
	switch {
	case hamiMonitorURL != "":
		reaperRunnable.DCGM = dcgm.NewHAMiMonitor(hamiMonitorURL)
		reaperRunnable.WorkloadAware = true
	case prometheusURL != "":
		reaperRunnable.DCGM = dcgm.NewPrometheus(prometheusURL, 90*time.Second)
		reaperRunnable.WorkloadAware = true
	}
	if err := mgr.Add(reaperRunnable); err != nil {
		setupLog.Error(err, "unable to add reaper")
		os.Exit(1)
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
