# The GShare session base image: the runtime for an interactive session with VS Code, JupyterLab, and
# a web terminal.
# The GPU variant swaps FROM for nvidia/cuda:*-cudnn-runtime and adds the same layers.
# Runs as non-root (uid 1000), which satisfies restricted Pod Security in gshare-sessions.
FROM codercom/code-server:latest

USER root
ARG TTYD_VERSION=1.7.7

# Runtime dependencies: JupyterLab, the ttyd web terminal, and supporting tools.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-pip python3-venv iproute2 tini ca-certificates wget && \
    rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir --break-system-packages jupyterlab notebook 2>/dev/null || \
    pip3 install --no-cache-dir jupyterlab notebook
RUN wget -qO /usr/local/bin/ttyd \
      "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64" && \
    chmod +x /usr/local/bin/ttyd

COPY entrypoint.sh /usr/local/bin/gshare-session
RUN chmod +x /usr/local/bin/gshare-session

USER 1000
EXPOSE 8080 8888 7681
ENTRYPOINT ["tini", "--", "/usr/local/bin/gshare-session"]
