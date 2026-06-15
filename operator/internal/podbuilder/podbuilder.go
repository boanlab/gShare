/*
Package podbuilder turns a GShareSession CR into the child K8s objects the
SessionReconciler applies: Pod (by mode), per-session Secret, Service, Ingress.

Mode rules:
  - fractional: runtimeClassName=nvidia + nvidia.com/gpumem + nvidia.com/gpucores
  - schedulerName=hami-scheduler (HAMi vGPU).
  - exclusive : standard device-plugin nvidia.com/gpu:1, no hami-scheduler.
  - mig       : nvidia.com/mig-<profile>:1.
  - cpu       : no GPU, nodeSelector gshare.io/node-type=cpu (free).
*/
package podbuilder

import (
	"crypto/rand"
	"encoding/hex"
	"strconv"
	"strings"

	corev1 "k8s.io/api/core/v1"
	netv1 "k8s.io/api/networking/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/utils/ptr"

	gsharev1 "github.com/gshare/operator/api/v1"
)

// Session connect ports — the session image serves on these.
const (
	portVSCode   = 8080 // code-server, root path /
	portJupyter  = 8888 // jupyterlab, base_url=/lab
	portTerminal = 7681 // ttyd web terminal, base_path /terminal
	// grace (sec) after SIGTERM for the app to write its checkpoint before kill.
	terminationGraceSec = 120
)

// Builder builds child objects for a session into a target Namespace.
type Builder struct {
	// Namespace is where session children are created (session namespace).
	Namespace string
	// IngressClass is the ingress class name, e.g. "nginx".
	IngressClass string
	// ConnectVerifyURL is the SoT control plane's auth-url for ingress (/internal/connect/verify).
	ConnectVerifyURL string
	// SessionDomain is the single console host for path-based session routing:
	// sessions live under {SessionDomain}/proxy/{cr-name}/.... Empty disables the
	// Ingress host (catch-all). The per-session Ingress is HTTP; TLS is terminated upstream.
	SessionDomain string
	// HAMiYieldExtender routes borrow Pods through hami-scheduler (GShare-patched extender: yielded
	// cards as preemptible capacity, fractional/multi-borrow). When false (default — stock HAMi),
	// borrow falls back to the device-plugin BYPASS: NVIDIA_VISIBLE_DEVICES + node pin, exclusive only.
	HAMiYieldExtender bool
}

// randToken returns a 48-hex random token for the per-session proxy credential.
func randToken() string {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		return ""
	}
	return hex.EncodeToString(b)
}

func (b *Builder) ns(s *gsharev1.GShareSession) string {
	if b.Namespace != "" {
		return b.Namespace
	}
	return s.Namespace
}

func podName(s *gsharev1.GShareSession) string    { return "ses-" + s.Name }
func secretName(s *gsharev1.GShareSession) string { return "ses-" + s.Name + "-secret" }
func svcName(s *gsharev1.GShareSession) string    { return "ses-" + s.Name }

// urlPrefix is the per-session URL prefix under the single console host (/proxy/{cr-name}).
// Apps serve under subpaths: jupyter at {prefix}/lab, ttyd at {prefix}/terminal,
// code-server at {prefix}/code (its ingress strips the {prefix}/code prefix).
func urlPrefix(s *gsharev1.GShareSession) string { return "/proxy/" + s.Name }

func labels(s *gsharev1.GShareSession) map[string]string {
	return map[string]string{"gshare.io/session": s.Name}
}

// BuildPod builds the session Pod, switching by mode.
func (b *Builder) BuildPod(s *gsharev1.GShareSession) *corev1.Pod {
	limits := corev1.ResourceList{}
	spec := corev1.PodSpec{
		SecurityContext: &corev1.PodSecurityContext{
			RunAsNonRoot:   ptr.To(true),
			RunAsUser:      ptr.To[int64](1000),
			FSGroup:        ptr.To[int64](1000),
			SeccompProfile: &corev1.SeccompProfile{Type: corev1.SeccompProfileTypeRuntimeDefault},
		},
		AutomountServiceAccountToken: ptr.To(false),
		NodeSelector:                 map[string]string{},
	}
	rc := "nvidia"

	switch {
	case s.Spec.ResourceClass == "cpu":
		// CPU free session — no GPU requested, default kube-scheduler.
		spec.NodeSelector["gshare.io/node-type"] = "cpu"

	case s.Spec.BorrowedGpuUuid != "" && b.HAMiYieldExtender:
		// Borrow via the GShare-patched HAMi extender: yielded cards are preemptible capacity
		// (gshare.io/preemptible), targeted to the chosen card by nvidia.com/use-gpuuuid; HAMi places
		// it (no NodeName pin) so the allocation is properly accounted. Fractional borrow uses
		// gpumem/gpucores; else a full-card exclusive borrow (gpumem-percentage=100 + gpucores=100).
		// (build/hami-fork, docs/paper/manuscript §Implementation)
		spec.RuntimeClassName = &rc
		spec.SchedulerName = "hami-scheduler"
		limits["nvidia.com/gpu"] = resource.MustParse("1")
		if s.Spec.GpuMemMb > 0 {
			limits["nvidia.com/gpumem"] = resource.MustParse(strconv.Itoa(s.Spec.GpuMemMb))
			limits["nvidia.com/gpucores"] = resource.MustParse(strconv.Itoa(s.Spec.GpuCores))
		} else {
			limits["nvidia.com/gpumem-percentage"] = resource.MustParse("100")
			limits["nvidia.com/gpucores"] = resource.MustParse("100")
		}

	case s.Spec.BorrowedGpuUuid != "":
		// Borrow fallback (stock HAMi): device-plugin BYPASS — the nvidia runtime injects the specific
		// yielded card by UUID (NVIDIA_VISIBLE_DEVICES in env(), NO nvidia.com/gpu request), pinned to
		// the resident's node (NodeName below). Exclusive only (one spot session per yielded card).
		// (docs/paper/manuscript, §Design)
		spec.RuntimeClassName = &rc

	case s.Spec.Mode == "fractional":
		// HAMi vGPU: gpumem/gpucores + hami-scheduler.
		spec.RuntimeClassName = &rc
		spec.SchedulerName = "hami-scheduler"
		spec.NodeSelector["gshare.io/gpu-mode"] = "fractional"
		limits["nvidia.com/gpu"] = resource.MustParse("1")
		limits["nvidia.com/gpumem"] = resource.MustParse(strconv.Itoa(s.Spec.GpuMemMb))
		limits["nvidia.com/gpucores"] = resource.MustParse(strconv.Itoa(s.Spec.GpuCores))

	case s.Spec.Mode == "exclusive":
		// Exclusive: standard device-plugin, no hami-scheduler.
		spec.RuntimeClassName = &rc
		spec.NodeSelector["gshare.io/gpu-mode"] = "exclusive"
		limits["nvidia.com/gpu"] = resource.MustParse("1")

	case s.Spec.Mode == "mig":
		spec.RuntimeClassName = &rc
		spec.NodeSelector["gshare.io/gpu-mode"] = "mig"
		limits[corev1.ResourceName("nvidia.com/mig-"+s.Spec.MigProfile)] = resource.MustParse("1")
	}

	// Borrow placement: HAMi-routed borrows are scheduled by hami-scheduler (use-gpuuuid, no pin);
	// bypass borrows pin to the resident's node where the yielded physical card lives.
	if s.Spec.BorrowedGpuUuid != "" && !b.HAMiYieldExtender && s.Spec.BorrowedNode != "" {
		spec.NodeName = s.Spec.BorrowedNode
	}

	// GPU nodes are tainted gshare.io/gpu:NoSchedule, so GPU session Pods need the toleration
	// to land (CPU sessions do not).
	if s.Spec.ResourceClass != "cpu" {
		spec.Tolerations = append(spec.Tolerations, corev1.Toleration{
			Key:      "gshare.io/gpu",
			Operator: corev1.TolerationOpExists,
			Effect:   corev1.TaintEffectNoSchedule,
		})
	}

	// CPU/RAM/disk from the offering flavor → Guaranteed (request=limit); disk is ephemeral-storage.
	// Conservative defaults when unset. GPU limits were filled above.
	cpu, memGb, diskGb := s.Spec.Cpu, s.Spec.MemGb, s.Spec.DiskGb
	if cpu <= 0 {
		cpu = 1
	}
	if memGb <= 0 {
		memGb = 4
	}
	if diskGb <= 0 {
		diskGb = 10
	}
	cpuQ := resource.MustParse(strconv.Itoa(cpu))
	memQ := resource.MustParse(strconv.Itoa(memGb) + "Gi")
	diskQ := resource.MustParse(strconv.Itoa(diskGb) + "Gi")
	limits[corev1.ResourceCPU] = cpuQ
	limits[corev1.ResourceMemory] = memQ
	limits[corev1.ResourceEphemeralStorage] = diskQ

	spec.Containers = []corev1.Container{{
		Name:  "session",
		Image: s.Spec.Image,
		// Always check the registry so an update to a fixed-tag catalogue image is picked up. Layers
		// stay cached, so this costs a manifest check.
		ImagePullPolicy: corev1.PullAlways,
		// PSA restricted: no privilege escalation + drop all caps. seccomp/runAsNonRoot are on the pod
		// securityContext. GPU is accessed via the nvidia runtime/device-plugin (no caps needed).
		SecurityContext: &corev1.SecurityContext{
			AllowPrivilegeEscalation: ptr.To(false),
			Capabilities:             &corev1.Capabilities{Drop: []corev1.Capability{"ALL"}},
		},
		Ports: []corev1.ContainerPort{
			{Name: "vscode", ContainerPort: portVSCode},
			{Name: "jupyter", ContainerPort: portJupyter},
			{Name: "terminal", ContainerPort: portTerminal},
		},
		Resources: corev1.ResourceRequirements{
			Limits: limits,
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:              cpuQ,
				corev1.ResourceMemory:           memQ,
				corev1.ResourceEphemeralStorage: diskQ,
			},
		},
		Env:          b.env(s),
		VolumeMounts: b.mounts(s),
	}}
	spec.Volumes = b.volumes(s)
	// App-level checkpoint fallback: on pause/delete, gives the job time after SIGTERM to write its
	// checkpoint into $GSHARE_CHECKPOINT_DIR; resume auto-resumes from the same volume.
	spec.TerminationGracePeriodSeconds = ptr.To[int64](terminationGraceSec)

	var podAnnos map[string]string
	if s.Spec.BorrowedGpuUuid != "" && b.HAMiYieldExtender {
		// HAMi-routed borrow: mark preemptible (extender treats yielded cards as free capacity) and
		// target the chosen yielded card. (build/hami-fork)
		podAnnos = map[string]string{
			"gshare.io/preemptible":  "true",
			"nvidia.com/use-gpuuuid": s.Spec.BorrowedGpuUuid,
		}
	}

	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:        podName(s),
			Namespace:   b.ns(s),
			Labels:      labels(s),
			Annotations: podAnnos,
		},
		Spec: spec,
	}
}

// checkpointDir: where the app saves/loads checkpoints — under the first persistent volume (survives
// Pod deletion), or a workdir default when no volume is mounted (persistence then depends on mounts).
func checkpointDir(s *gsharev1.GShareSession) string {
	for _, v := range s.Spec.Volumes {
		if v.MountPath != "" {
			return strings.TrimRight(v.MountPath, "/") + "/.gshare/checkpoints"
		}
	}
	return "/workspace/.gshare/checkpoints"
}

// env injects per-session Secret references into the container.
func (b *Builder) env(s *gsharev1.GShareSession) []corev1.EnvVar {
	secretKeyRef := func(secret, key string) *corev1.EnvVarSource {
		return &corev1.EnvVarSource{
			SecretKeyRef: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: secret},
				Key:                  key,
				Optional:             ptr.To(true),
			},
		}
	}
	// PROXY_TOKEN sources from the external Secret named by spec.connect.proxyTokenSecretRef
	// when set (referenced directly, never copied), else from the per-session Secret's
	// proxy_token key.
	proxySecret, proxyKey := secretName(s), "proxy_token"
	if ref := strings.TrimSpace(s.Spec.Connect.ProxyTokenSecretRef); ref != "" {
		proxySecret, proxyKey = ref, "proxy_token"
	}
	env := []corev1.EnvVar{
		{Name: "PROXY_TOKEN", ValueFrom: secretKeyRef(proxySecret, proxyKey)},
		// GSHARE_URL_PREFIX lets the session image set each app's base-path: jupyter
		// --ServerApp.base_url=${PREFIX}/lab, ttyd --base-path ${PREFIX}/terminal.
		// code-server stays at root (its ingress strips the ${PREFIX}/code prefix).
		{Name: "GSHARE_URL_PREFIX", Value: urlPrefix(s)},
		// App-level checkpoint fallback: the job saves/loads here so resume continues after pause(SIGTERM).
		{Name: "GSHARE_CHECKPOINT_DIR", Value: checkpointDir(s)},
	}
	// Bypass borrow (stock HAMi): the nvidia runtime injects the specific yielded card by UUID
	// (no nvidia.com/gpu request). HAMi-routed borrow instead gets the card from the device-plugin
	// via the nvidia.com/use-gpuuuid annotation. (build/hami-fork)
	if s.Spec.BorrowedGpuUuid != "" && !b.HAMiYieldExtender {
		env = append(env,
			corev1.EnvVar{Name: "NVIDIA_VISIBLE_DEVICES", Value: strings.TrimSpace(s.Spec.BorrowedGpuUuid)},
			corev1.EnvVar{Name: "NVIDIA_DRIVER_CAPABILITIES", Value: "all"},
		)
	}
	return env
}

// mounts maps VolumeSpec -> VolumeMount.
func (b *Builder) mounts(s *gsharev1.GShareSession) []corev1.VolumeMount {
	out := make([]corev1.VolumeMount, 0, len(s.Spec.Volumes))
	for _, v := range s.Spec.Volumes {
		out = append(out, corev1.VolumeMount{
			Name:      v.Name,
			MountPath: v.MountPath,
			ReadOnly:  v.Mode == "ReadOnlyMany",
		})
	}
	return out
}

// volumes maps VolumeSpec -> PVC-backed Volume.
func (b *Builder) volumes(s *gsharev1.GShareSession) []corev1.Volume {
	out := make([]corev1.Volume, 0, len(s.Spec.Volumes))
	for _, v := range s.Spec.Volumes {
		out = append(out, corev1.Volume{
			Name: v.Name,
			VolumeSource: corev1.VolumeSource{
				PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{
					ClaimName: v.Name,
					ReadOnly:  v.Mode == "ReadOnlyMany",
				},
			},
		})
	}
	return out
}

// BuildSecret projects CR spec.connect into the per-session Secret.
func (b *Builder) BuildSecret(s *gsharev1.GShareSession) *corev1.Secret {
	// When spec.connect.proxyTokenSecretRef is set the proxy token lives in that external Secret, so
	// we must not write a proxy_token key here; when unset it is generated (random per session, no
	// unauthenticated exposure; applyIfAbsent fixes it on first create).
	data := map[string]string{}
	if strings.TrimSpace(s.Spec.Connect.ProxyTokenSecretRef) == "" {
		data["proxy_token"] = randToken()
	}
	return &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      secretName(s),
			Namespace: b.ns(s),
			Labels:    labels(s),
		},
		Type:       corev1.SecretTypeOpaque,
		StringData: data,
	}
}

// BuildService exposes vscode(8080)/jupyter(8888)/terminal(7681).
func (b *Builder) BuildService(s *gsharev1.GShareSession) *corev1.Service {
	return &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:      svcName(s),
			Namespace: b.ns(s),
			Labels:    labels(s),
		},
		Spec: corev1.ServiceSpec{
			Selector: labels(s),
			Ports: []corev1.ServicePort{
				{Name: "vscode", Port: portVSCode, TargetPort: intStr(portVSCode)},
				{Name: "jupyter", Port: portJupyter, TargetPort: intStr(portJupyter)},
				{Name: "terminal", Port: portTerminal, TargetPort: intStr(portTerminal)},
			},
		},
	}
}

// appsIngressName / vscodeIngressName are the two per-session Ingress names.
func appsIngressName(s *gsharev1.GShareSession) string   { return "ses-" + s.Name + "-apps" }
func vscodeIngressName(s *gsharev1.GShareSession) string { return "ses-" + s.Name + "-vscode" }

// authAnnotations adds forward-auth ingress annotations to m so every request is
// verified by the SoT control plane's cnx auth-url, with a Set-Cookie on first redeem.
// No-op when ConnectVerifyURL is empty.
func (b *Builder) authAnnotations(m map[string]string) {
	if b.ConnectVerifyURL != "" {
		m["nginx.ingress.kubernetes.io/auth-url"] = b.ConnectVerifyURL
		m["nginx.ingress.kubernetes.io/auth-response-headers"] = "Set-Cookie"
	}
}

func (b *Builder) ingressClass() *string {
	if b.IngressClass != "" {
		return &b.IngressClass
	}
	return nil
}

// BuildIngress builds the per-session apps Ingress (Host = {SessionDomain}, no rewrite).
// The apps serve under their full prefix, so paths map straight through:
//   - /proxy/{cr}/lab      → jupyter (8888)
//   - /proxy/{cr}/terminal → ttyd    (7681)
func (b *Builder) BuildIngress(s *gsharev1.GShareSession) *netv1.Ingress {
	annotations := map[string]string{}
	b.authAnnotations(annotations)

	prefix := urlPrefix(s)
	path := func(p string, port int) netv1.HTTPIngressPath {
		pt := netv1.PathTypePrefix
		return netv1.HTTPIngressPath{
			Path: p, PathType: &pt,
			Backend: netv1.IngressBackend{Service: &netv1.IngressServiceBackend{
				Name: svcName(s), Port: netv1.ServiceBackendPort{Number: int32(port)},
			}},
		}
	}
	return &netv1.Ingress{
		ObjectMeta: metav1.ObjectMeta{
			Name:        appsIngressName(s),
			Namespace:   b.ns(s),
			Labels:      labels(s),
			Annotations: annotations,
		},
		Spec: netv1.IngressSpec{
			IngressClassName: b.ingressClass(),
			Rules: []netv1.IngressRule{{
				Host: b.SessionDomain,
				IngressRuleValue: netv1.IngressRuleValue{
					HTTP: &netv1.HTTPIngressRuleValue{
						Paths: []netv1.HTTPIngressPath{
							path(prefix+"/lab", portJupyter),
							path(prefix+"/terminal", portTerminal),
						},
					},
				},
			}},
		},
	}
}

// BuildVSCodeIngress builds the per-session code-server Ingress. code-server serves at
// root with relative asset URLs, so a prefix-stripping regex rewrite works. Scoped to
// the /code subpath so the greedy regex does not shadow the apps Ingress /lab, /terminal.
//
//	/proxy/{cr}/code(/|$)(.*) → vscode (8080), tail re-mapped to /$2.
//
// Host = {SessionDomain}, same forward-auth.
func (b *Builder) BuildVSCodeIngress(s *gsharev1.GShareSession) *netv1.Ingress {
	annotations := map[string]string{
		"nginx.ingress.kubernetes.io/use-regex":      "true",
		"nginx.ingress.kubernetes.io/rewrite-target": "/$2",
	}
	b.authAnnotations(annotations)

	pt := netv1.PathTypeImplementationSpecific
	return &netv1.Ingress{
		ObjectMeta: metav1.ObjectMeta{
			Name:        vscodeIngressName(s),
			Namespace:   b.ns(s),
			Labels:      labels(s),
			Annotations: annotations,
		},
		Spec: netv1.IngressSpec{
			IngressClassName: b.ingressClass(),
			Rules: []netv1.IngressRule{{
				Host: b.SessionDomain,
				IngressRuleValue: netv1.IngressRuleValue{
					HTTP: &netv1.HTTPIngressRuleValue{
						Paths: []netv1.HTTPIngressPath{{
							Path:     urlPrefix(s) + "/code(/|$)(.*)",
							PathType: &pt,
							Backend: netv1.IngressBackend{Service: &netv1.IngressServiceBackend{
								Name: svcName(s), Port: netv1.ServiceBackendPort{Number: int32(portVSCode)},
							}},
						}},
					},
				},
			}},
		},
	}
}

func intStr(p int) intstr.IntOrString { return intstr.FromInt32(int32(p)) }
