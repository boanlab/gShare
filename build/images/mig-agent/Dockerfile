# The GShare MIG-mode node agent.
#
# Runs as a privileged, node-pinned Job created by the operator's GpuModeChangeReconciler. It
# toggles one physical card's MIG mode with the host driver's nvidia-smi (injected by the nvidia
# runtime), strictly after asserting the card runs no compute processes — the control plane has
# already drained it in the ledger, this is the belt-and-braces check on the metal.
FROM ubuntu:22.04

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends procps \
 && rm -rf /var/lib/apt/lists/*

COPY agent.sh /usr/bin/gshare-mig-agent
RUN chmod +x /usr/bin/gshare-mig-agent

ENTRYPOINT ["/usr/bin/gshare-mig-agent"]
