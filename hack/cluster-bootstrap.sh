#!/usr/bin/env bash
set -euo pipefail
# Bootstraps a GPU Kubernetes cluster for GShare: kubeadm plus the full HAMi stack.
#
# Stack: Kubernetes 1.36, containerd with the NVIDIA runtime, flannel, HAMi, ingress-nginx,
# local-path, and RuntimeClass nvidia. On GPU nodes it also installs CRIU and cuda-checkpoint and
# labels them, which is what enables lossless yield; set LOSSLESS=skip to leave that out.
#
# Topology: one control plane, N GPU workers, and optionally M CPU workers, on Ubuntu 22.04 or 24.04.
#
# Recommended, driving every node over SSH. Requires ssh-copy-id and passwordless remote sudo:
#   cp hack/cluster-info.example hack/cluster-info     # MASTER_NODE / WORKER_NODES [/ CPU_WORKERS]
#   ./hack/cluster-bootstrap.sh up                     # prereqs→init→join→addons→label→verify
# By hand: prereqs [--gpu] / init / join "<kubeadm join…>" / addons /
#          label <node> <cpu|exclusive|fractional> / verify
#
# On a GPU node where nvidia-smi does not work, the driver is installed automatically
# (NVIDIA_DRIVER=auto|<package>|skip). A fresh install normally needs a reboot, which
# NVIDIA_DRIVER_REBOOT=1 performs. Versions and CIDRs can be overridden from the environment.

K8S_MINOR="${K8S_MINOR:-1.36}"                         # the pkgs.k8s.io channel
POD_CIDR="${POD_CIDR:-10.244.0.0/16}"                  # flannel default
FLANNEL_URL="${FLANNEL_URL:-https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml}"
LOCAL_PATH_VER="${LOCAL_PATH_VER:-v0.0.30}"
INGRESS_NS="${INGRESS_NS:-ingress-nginx}"
HAMI_REPO="${HAMI_REPO:-https://project-hami.github.io/HAMi/}"
NVIDIA_DRIVER="${NVIDIA_DRIVER:-auto}"                 # auto (the ubuntu-drivers recommendation), an explicit package such as nvidia-driver-550-server, or skip
NVIDIA_DRIVER_REBOOT="${NVIDIA_DRIVER_REBOOT:-0}"      # 1 reboots automatically after a fresh driver install
LOSSLESS="${LOSSLESS:-auto}"                           # auto installs CRIU and cuda-checkpoint on GPU nodes, which lossless yield requires; skip leaves them out
CRIU_VERSION="${CRIU_VERSION:-v3.19}"                  # tag to build from source where apt has no package, as on noble
CUDA_CHECKPOINT_URL="${CUDA_CHECKPOINT_URL:-https://raw.githubusercontent.com/NVIDIA/cuda-checkpoint/main/bin/x86_64/cuda-checkpoint}"  # a single binary, needing driver R550 or later

log(){ printf "\033[36m[cluster]\033[0m %s\n" "$*" >&2; }
die(){ printf "\033[31m[cluster][ERROR]\033[0m %s\n" "$*" >&2; exit 1; }
need_root(){ [ "$(id -u)" = 0 ] || die "this step must run as root (use sudo)"; }
have(){ command -v "$1" >/dev/null 2>&1; }

# ── GPU driver: installed automatically when nvidia-smi does not work ──────
# Loading the kernel module normally needs a reboot afterwards, signalled through
# NVIDIA_REBOOT_REQUIRED and handled at the end.
ensure_nvidia_driver(){
  if have nvidia-smi && nvidia-smi >/dev/null 2>&1; then return 0; fi
  if [ "$NVIDIA_DRIVER" = skip ]; then
    die "this is a GPU node but nvidia-smi does not work; install the NVIDIA driver first (automatic installation is off because NVIDIA_DRIVER=skip)."
  fi
  log "no NVIDIA driver detected; installing automatically (NVIDIA_DRIVER=${NVIDIA_DRIVER})"
  apt-get update -y
  if [ "$NVIDIA_DRIVER" = auto ]; then
    apt-get install -y ubuntu-drivers-common
    ubuntu-drivers install || ubuntu-drivers autoinstall || die "ubuntu-drivers could not install a driver; name the package explicitly with NVIDIA_DRIVER."
  else
    apt-get install -y "$NVIDIA_DRIVER" || die "could not install the driver package: $NVIDIA_DRIVER"
  fi
  if nvidia-smi >/dev/null 2>&1; then
    log "nvidia-smi works; no reboot needed."
  else
    NVIDIA_REBOOT_REQUIRED=1
    log "driver installed; a reboot is needed to load the kernel module (nvidia-smi does not work yet)."
  fi
}

# ── Lossless tooling on GPU nodes: CRIU for process checkpoint/restore, cuda-checkpoint for VRAM
#    eviction ──
# These are what in-place yield and graceful demotion require. Failure here is not fatal: yield falls
# back to cold and the node simply is not labelled.
# When both are ready the script leaves /tmp/gshare-lossless-ready, which the control plane reads
# before labelling.
ensure_lossless(){
  [ "$LOSSLESS" = skip ] && { log "LOSSLESS=skip: not installing CRIU or cuda-checkpoint; yield falls back to cold"; return 0; }
  rm -f /tmp/gshare-lossless-ready
  log "installing the lossless tooling (CRIU and cuda-checkpoint)"
  # cuda-checkpoint is a single NVIDIA binary that works on driver R550 and later. This only downloads
  # it; it becomes functional once the driver is loaded.
  if [ ! -x /usr/bin/cuda-checkpoint ]; then
    curl -fsSL -o /usr/bin/cuda-checkpoint "$CUDA_CHECKPOINT_URL" && chmod +x /usr/bin/cuda-checkpoint \
      || log "  ⚠ could not download cuda-checkpoint; install it by hand"
  fi
  # CRIU: prefer apt, and build from source where there is no package (as on noble). runc invokes
  # criu from PATH, so it is installed into /usr/sbin.
  have criu || apt-get install -y criu 2>/dev/null || true
  if ! have criu; then
    log "  no CRIU package available; building ${CRIU_VERSION} from source"
    apt-get install -y git build-essential pkg-config libbsd-dev libcap-dev libnl-3-dev libnet-dev \
      libprotobuf-dev libprotobuf-c-dev protobuf-c-compiler protobuf-compiler python3-protobuf \
      libnftables-dev libgnutls28-dev iproute2 2>/dev/null || log "  ⚠ some CRIU build dependencies failed to install"
    rm -rf /tmp/criu-src \
      && git clone --depth 1 --branch "${CRIU_VERSION}" https://github.com/checkpoint-restore/criu.git /tmp/criu-src \
      && make -C /tmp/criu-src WERROR=0 -j"$(nproc)" \
      && install -m0755 /tmp/criu-src/criu/criu /usr/sbin/criu \
      || log "  ⚠ CRIU failed to build; yield falls back to cold"
  fi
  if have criu && criu --version >/dev/null 2>&1 && [ -x /usr/bin/cuda-checkpoint ]; then
    : >/tmp/gshare-lossless-ready
    log "  lossless tooling ready (CRIU and cuda-checkpoint); the control plane will label this node."
  else
    log "  ⚠ lossless tooling incomplete (CRIU or cuda-checkpoint missing); yield falls back to cold and the node is not labelled."
  fi
}

# ── Common node preparation: container runtime, the kube* packages, kernel modules, and sysctls ──
cmd_prereqs(){
  need_root
  local GPU=0; [ "${1:-}" = "--gpu" ] && GPU=1
  log "disabling swap, loading kernel modules, applying sysctls"
  swapoff -a; sed -ri.bak '/^[^#].*\sswap\s/ s/^/#/' /etc/fstab || true   # already-commented swap lines are left alone, so re-running is safe
  cat >/etc/modules-load.d/k8s.conf <<EOF
overlay
br_netfilter
EOF
  modprobe overlay; modprobe br_netfilter
  cat >/etc/sysctl.d/k8s.conf <<EOF
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
  sysctl --system >/dev/null

  log "installing and configuring containerd with SystemdCgroup"
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg apt-transport-https
  if ! have containerd; then apt-get install -y containerd; fi
  mkdir -p /etc/containerd
  containerd config default >/etc/containerd/config.toml
  sed -ri 's/(SystemdCgroup = )false/\1true/' /etc/containerd/config.toml

  if [ "$GPU" = 1 ]; then
    ensure_nvidia_driver
    log "installing nvidia-container-toolkit and registering the nvidia runtime with containerd"
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
      | gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      >/etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update -y && apt-get install -y nvidia-container-toolkit
    # Register nvidia as containerd's *default* runtime. The HAMi device plugin sets no
    # runtimeClassName, so NVML only initialises when the default is nvidia; otherwise it
    # CrashLoopBackOffs with ERROR_LIBRARY_NOT_FOUND.
    # containerd 2.x (config v3) takes this as a drop-in at /etc/containerd/conf.d/99-nvidia.toml,
    # loaded through config.toml's imports.
    nvidia-ctk runtime configure --runtime=containerd --set-as-default=true || true
    ensure_lossless   # CRIU and cuda-checkpoint, which lossless yield needs; failure is not fatal
  fi
  systemctl restart containerd; systemctl enable containerd

  log "installing kubeadm, kubelet, and kubectl ${K8S_MINOR}"
  mkdir -p /etc/apt/keyrings
  curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_MINOR}/deb/Release.key" \
    | gpg --batch --yes --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
  echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v${K8S_MINOR}/deb/ /" \
    >/etc/apt/sources.list.d/kubernetes.list
  apt-get update -y && apt-get install -y kubelet kubeadm kubectl
  apt-mark hold kubelet kubeadm kubectl
  systemctl enable --now kubelet
  log "prereqs complete (GPU=$GPU)."

  if [ "${NVIDIA_REBOOT_REQUIRED:-0}" = 1 ]; then
    : >/tmp/gshare-nvidia-reboot-required   # the `up` orchestrator watches for this, reboots, and waits to reconnect
    if [ "$NVIDIA_DRIVER_REBOOT" = 1 ]; then
      log "⚠ NVIDIA driver freshly installed and NVIDIA_DRIVER_REBOOT=1, so rebooting now. Re-run this node's next step afterwards."
      reboot
    else
      log "⚠ NVIDIA driver freshly installed. Run 'sudo reboot', confirm nvidia-smi works, then continue. The `up` path reboots and waits automatically; standalone runs need NVIDIA_DRIVER_REBOOT=1."
    fi
  fi
}

# Is the control plane healthy: admin.conf present and the API server answering /healthz.
cp_healthy(){ [ -f /etc/kubernetes/admin.conf ] && KUBECONFIG=/etc/kubernetes/admin.conf kubectl get --raw=/healthz >/dev/null 2>&1; }
# Are there leftovers from an earlier, incomplete init: manifests, admin.conf, or a non-empty etcd.
cp_has_state(){
  [ -f /etc/kubernetes/manifests/kube-apiserver.yaml ] || [ -f /etc/kubernetes/admin.conf ] \
    || { [ -d /var/lib/etcd ] && [ -n "$(ls -A /var/lib/etcd 2>/dev/null)" ]; }
}

# ── control-plane: kubeadm init + flannel ────────────────────────────────────
cmd_init(){
  need_root
  if cp_healthy; then
    log "a healthy control plane is already running; skipping kubeadm init and refreshing only the kubeconfig, flannel, and join token."
  else
    if cp_has_state; then
      # An API server holding the port, or leftover manifests and etcd data, without a healthy
      # control plane, means a dirty or incomplete init. Running kubeadm init as-is would fail
      # preflight on Port-6443, FileAvailable, or DirAvailable--var-lib-etcd.
      [ "${INIT_FORCE_RESET:-1}" = 1 ] \
        || die "found leftovers from a previous control plane that is not healthy. Re-run with INIT_FORCE_RESET=1 to clean up with kubeadm reset, or run 'sudo kubeadm reset -f' by hand."
      log "cleaning up the previous init: kubeadm reset -f, plus /etc/cni/net.d"
      kubeadm reset -f
      rm -rf /etc/cni/net.d
    fi
    log "kubeadm init (pod-cidr=${POD_CIDR})"
    kubeadm init --pod-network-cidr="${POD_CIDR}"
  fi
  local U="${SUDO_USER:-root}" H; H="$(eval echo "~${U}")"
  mkdir -p "$H/.kube"; cp -f /etc/kubernetes/admin.conf "$H/.kube/config"; chown -R "$U" "$H/.kube"
  export KUBECONFIG=/etc/kubernetes/admin.conf
  log "applying the flannel CNI"
  kubectl apply -f "${FLANNEL_URL}"
  log "init complete. The join command is written to /tmp/gshare-kubeadm-join.sh for the orchestrator:"
  echo "─────────────────────────────────────────────"
  kubeadm token create --print-join-command | tee /tmp/gshare-kubeadm-join.sh
  chmod 0644 /tmp/gshare-kubeadm-join.sh
  echo "─────────────────────────────────────────────"
}

# ── Workers: join ───────────────────────────────────────────────────────────
cmd_join(){
  need_root
  [ -n "${1:-}" ] || die "usage: join \"kubeadm join <host>:6443 --token ... --discovery-token-ca-cert-hash sha256:...\""
  if [ -f /etc/kubernetes/kubelet.conf ]; then
    log "already joined (/etc/kubernetes/kubelet.conf exists); skipping."
    return 0
  fi
  log "joining the cluster"
  eval "$*"
  log "joined. Run the label step from the control plane next."
}

# Install helm v3 when it is missing, using get-helm-3, which calls sudo internally and therefore
# needs NOPASSWD. Set HELM_SKIP_INSTALL=1 to disable.
ensure_helm(){
  have helm && return 0
  [ "${HELM_SKIP_INSTALL:-0}" = 1 ] && die "helm v3 is required and automatic installation is off (HELM_SKIP_INSTALL=1). See https://helm.sh/docs/intro/install/"
  log "helm v3 not found; installing it with get-helm-3"
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash \
    || die "could not install helm; install it manually and re-run. See https://helm.sh/docs/intro/install/"
  have helm || die "helm was installed but is not on PATH; start a new shell and re-run"
}

# ── Control plane: addons (HAMi, ingress, local-path, RuntimeClass) ──
cmd_addons(){
  have kubectl || die "kubectl is required; run this on the control plane"
  ensure_helm

  log "[1/4] RuntimeClass nvidia"
  kubectl apply -f - <<'EOF'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: { name: nvidia }
handler: nvidia
EOF

  log "[2/4] HAMi: the fractional GPU device plugin and scheduler. GPU nodes need the 'gpu=on' label, applied by the label step below."
  helm repo add hami "${HAMI_REPO}" >/dev/null 2>&1 || true
  helm repo update >/dev/null
  local SVER; SVER="$(kubectl version -o json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["serverVersion"]["gitVersion"])' 2>/dev/null || echo '')"
  if helm status hami -n kube-system >/dev/null 2>&1; then
    log "  HAMi is already installed; skipping"
  else
    helm install hami hami/hami -n kube-system \
      ${SVER:+--set scheduler.kubeScheduler.imageTag="${SVER}"}
  fi

  log "[3/4] ingress-nginx (NodePort 30080/30443)"
  helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true
  helm repo update >/dev/null
  helm upgrade -i ingress-nginx ingress-nginx/ingress-nginx -n "${INGRESS_NS}" --create-namespace \
    --set controller.service.type=NodePort \
    --set controller.service.nodePorts.http=30080 \
    --set controller.service.nodePorts.https=30443

  log "[4/4] local-path-provisioner ${LOCAL_PATH_VER}, as the default StorageClass"
  kubectl apply -f "https://raw.githubusercontent.com/rancher/local-path-provisioner/${LOCAL_PATH_VER}/deploy/local-path-storage.yaml"
  kubectl annotate storageclass local-path storageclass.kubernetes.io/is-default-class=true --overwrite
  log "addons complete. Mark the GPU and CPU nodes with the 'label' step."
}

# ── Node labelling: HAMi's gpu=on plus GShare's gshare.io/* ─────────────────────────────
cmd_label(){
  have kubectl || die "kubectl is required"
  local node="${1:-}" mode="${2:-}"
  [ -n "$node" ] && [ -n "$mode" ] || die "usage: label <node> <cpu|exclusive|fractional>"
  case "$mode" in
    cpu)
      kubectl label node "$node" gshare.io/node-type=cpu --overwrite ;;
    exclusive|fractional)
      kubectl label node "$node" gpu=on gshare.io/gpu=true "gshare.io/gpu-mode=${mode}" --overwrite ;;
    *) die "mode must be cpu, exclusive, or fractional" ;;
  esac
  log "labelling $node as $mode"
}

# ── Verification ────────────────────────────────────────────────────────────
cmd_verify(){
  have kubectl || die "kubectl is required"
  log "nodes"; kubectl get nodes -o wide
  log "GPU capacity and HAMi labels"
  kubectl get nodes -o json | python3 -c "
import json,sys
for n in json.load(sys.stdin)['items']:
    l=n['metadata']['labels']; c=n['status'].get('capacity',{})
    lossless='ready' if l.get('gshare.io/criu')=='ready' and l.get('gshare.io/cuda-checkpoint')=='ready' else '-'
    print(' ', n['metadata']['name'],
          'gpu-mode=',l.get('gshare.io/gpu-mode','-'),
          'nvidia.com/gpu=',c.get('nvidia.com/gpu','-'),
          'gpumem=',c.get('nvidia.com/gpumem','-'),
          'lossless=',lossless)
"
  log "addon pods"; kubectl get pods -A | grep -iE "hami|ingress-nginx|flannel|local-path" || true
  log "StorageClass"; kubectl get sc
  log "RuntimeClass"; kubectl get runtimeclass
  cat <<'EOF'

[next] The cluster is ready. Deploy GShare:
  make deploy-incluster                # all-in-one: the chart brings up the data tier, secrets, CRDs,
                                       # namespaces, the operator token, and the local cluster
                                       # registration, with the operator webhook and the lossless
                                       # agent enabled. On nodes labelled ready, yield is lossless.
  # Front it with a reverse proxy that terminates TLS for the console domain and forwards to the
  # ingress-nginx NodePort (:30080, plain HTTP).
  # (opt-in) workload-aware idle : make deploy-monitoring + helm --set operator.prometheusUrl=http://prometheus.monitoring.svc:9090
  # Optional, to borrow yielded cards: make hami-fork-image, then
  #   kubectl -n kube-system set image deploy/hami-scheduler vgpu-scheduler-extender=<image>
  # For a production install against external CloudNativePG, Redis, and external-secrets, see
  # make prod-deploy with deploy/values/dockerhub.yaml.
EOF
}

# ── Remote orchestration (`up`): read cluster-info and set every node up over SSH ──────
# Requires SSH key authentication from this machine to each node (ssh-copy-id) and passwordless
# remote sudo.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
REMOTE_PATH="/tmp/cluster-bootstrap.sh"

load_cluster_info(){
  local f="${CLUSTER_INFO:-$(dirname "$SELF")/cluster-info}"
  [ -f "$f" ] || die "configuration file not found: $f. Copy hack/cluster-info.example and fill it in."
  # shellcheck disable=SC1090
  . "$f"
  [ -n "${MASTER_NODE:-}" ]  || die "cluster-info must define MASTER_NODE as user@ip"
  [ -n "${WORKER_NODES:-}" ] || die "cluster-info must define WORKER_NODES as user@ip[:mode], comma separated"
  CPU_WORKERS="${CPU_WORKERS:-}"   # optional GPU-less workers as comma-separated user@ip, labelled cpu
  SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=accept-new -o ConnectTimeout=10}"
}
rsh(){ local tgt="$1"; shift; ssh $SSH_OPTS "$tgt" "$@"; }                 # run over ssh
rcp(){ scp $SSH_OPTS -q "$1" "$2"; }                                       # copy with scp
host_addr(){ echo "${1%%:*}"; }                                           # user@ip:mode -> user@ip
host_mode(){ case "$1" in *:*) echo "${1##*:}";; *) echo "fractional";; esac; }  # mode; fractional when omitted
node_name(){ rsh "$1" 'hostname' 2>/dev/null | tr '[:upper:]' '[:lower:]' | tr -d '\r'; }
boot_id(){ rsh "$1" 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null | tr -d '\r' || true; }
# Reboot a node and wait until it has really restarted — the boot_id changes — and SSH answers again.
# Times out after REBOOT_WAIT_TRIES times five seconds.
reboot_and_wait(){
  local tgt="$1" before; before="$(boot_id "$tgt")"
  rsh "$tgt" 'sudo reboot' >/dev/null 2>&1 || true        # the connection dropping is expected
  local i=0 tries="${REBOOT_WAIT_TRIES:-120}" cur
  while [ "$i" -lt "$tries" ]; do
    i=$((i+1)); sleep 5
    cur="$(boot_id "$tgt")"
    [ -n "$cur" ] && [ "$cur" != "$before" ] && return 0  # a changed boot id means it restarted and SSH is back
  done
  return 1
}

cmd_up(){
  load_cluster_info
  IFS=',' read -ra WORKERS <<< "$WORKER_NODES"
  local CPU_WK=(); [ -n "$CPU_WORKERS" ] && IFS=',' read -ra CPU_WK <<< "$CPU_WORKERS"
  log "targets: master=${MASTER_NODE}  gpu-workers=${WORKERS[*]}  cpu-workers=${CPU_WK[*]:-(none)}"

  log "[0/6] copying this script to every node"
  rcp "$SELF" "${MASTER_NODE}:${REMOTE_PATH}"
  for w in "${WORKERS[@]}"; do rcp "$SELF" "$(host_addr "$w"):${REMOTE_PATH}"; done
  for c in "${CPU_WK[@]}"; do rcp "$SELF" "$(host_addr "$c"):${REMOTE_PATH}"; done

  log "[1/6] prereqs, in parallel: master and CPU workers without --gpu, GPU workers with it"
  rsh "$MASTER_NODE" "sudo bash ${REMOTE_PATH} prereqs" &
  for w in "${WORKERS[@]}"; do rsh "$(host_addr "$w")" "sudo bash ${REMOTE_PATH} prereqs --gpu" & done
  for c in "${CPU_WK[@]}"; do rsh "$(host_addr "$c")" "sudo bash ${REMOTE_PATH} prereqs" & done
  wait
  log "prereqs complete"

  log "[1.5/6] rebooting the GPU workers that got a driver, and waiting for each to come back"
  for w in "${WORKERS[@]}"; do
    local wa; wa="$(host_addr "$w")"
    if rsh "$wa" 'test -f /tmp/gshare-nvidia-reboot-required'; then
      log "  ${wa}: NVIDIA driver freshly installed; rebooting and waiting to reconnect"
      rsh "$wa" 'sudo rm -f /tmp/gshare-nvidia-reboot-required' || true
      reboot_and_wait "$wa" || die "timed out rebooting or reconnecting to ${wa}; raise REBOOT_WAIT_TRIES"
      rsh "$wa" 'nvidia-smi >/dev/null 2>&1' || die "nvidia-smi still does not work after the reboot: ${wa}"
      log "  ${wa}: reconnected, nvidia-smi works"
    fi
  done

  log "[2/6] control-plane init"
  rsh "$MASTER_NODE" "sudo bash ${REMOTE_PATH} init"
  local JOIN; JOIN="$(rsh "$MASTER_NODE" "cat /tmp/gshare-kubeadm-join.sh" | tr -d '\r')"
  [ -n "$JOIN" ] || die "could not read the join command from the master"

  log "[3/6] joining the workers, GPU and CPU in parallel"
  for w in "${WORKERS[@]}"; do
    rsh "$(host_addr "$w")" "sudo bash ${REMOTE_PATH} join '${JOIN}'" &
  done
  for c in "${CPU_WK[@]}"; do
    rsh "$(host_addr "$c")" "sudo bash ${REMOTE_PATH} join '${JOIN}'" &
  done
  wait
  log "workers joined"

  log "[4/6] addons (master)"
  rsh "$MASTER_NODE" "bash ${REMOTE_PATH} addons"

  log "[5/6] labelling the nodes"
  local mname; mname="$(node_name "$MASTER_NODE")"
  rsh "$MASTER_NODE" "bash ${REMOTE_PATH} label ${mname} cpu"
  for w in "${WORKERS[@]}"; do
    local wname wmode wa; wa="$(host_addr "$w")"; wname="$(node_name "$wa")"; wmode="$(host_mode "$w")"
    rsh "$MASTER_NODE" "bash ${REMOTE_PATH} label ${wname} ${wmode}"
    # A GPU node with the lossless tooling ready gets the criu and cuda-checkpoint ready labels,
    # which is what the operator's lossless gate checks.
    if rsh "$wa" 'test -f /tmp/gshare-lossless-ready'; then
      rsh "$MASTER_NODE" "kubectl label node ${wname} gshare.io/criu=ready gshare.io/cuda-checkpoint=ready --overwrite"
      log "  ${wname}: labelled ready for lossless operation (CRIU and cuda-checkpoint)"
    fi
  done
  for c in "${CPU_WK[@]}"; do
    local cname; cname="$(node_name "$(host_addr "$c")")"
    rsh "$MASTER_NODE" "bash ${REMOTE_PATH} label ${cname} cpu"
  done

  log "[6/6] verify"
  rsh "$MASTER_NODE" "bash ${REMOTE_PATH} verify"
  log "cluster bootstrap complete 🎉  (kubeconfig: master:~/.kube/config)"
}

usage(){ sed -n '3,12p' "$0"; }

case "${1:-}" in
  up)      shift; cmd_up;;                              # set every node up over SSH, driven by cluster-info
  prereqs) shift; cmd_prereqs "${1:-}";;
  init)    shift; cmd_init;;
  join)    shift; cmd_join "$@";;
  addons)  shift; cmd_addons;;
  label)   shift; cmd_label "${1:-}" "${2:-}";;
  verify)  shift; cmd_verify;;
  ""|-h|--help) usage;;
  *) die "unknown command: $1 (up|prereqs|init|join|addons|label|verify)";;
esac
