"""Application settings via pydantic-settings.

Env prefix is ``GSHARE_`` so e.g. ``GSHARE_DATABASE_URL`` populates ``DATABASE_URL``.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GSHARE_", extra="ignore")

    # ── Datastores ──
    DATABASE_URL: str = "postgresql+asyncpg://gshare:gshare@localhost:5432/gshare"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Database connection pool (per process). SSE streams hold no pooled connection (see
    # get_sse_principal), so this only has to cover request-scoped work. ──
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10

    # JWKS cache TTL for the internal RS256 JWT verifier (below).
    JWKS_TTL_SEC: int = 3600

    # ── Plane-to-plane: RS256 internal JWT, aud=gshare-internal ──
    # PEM private key (external-secrets injected) used to SIGN internal tokens; this plane is the
    # verifier of operator->control callbacks and publishes the public JWKS route.
    INTERNAL_JWT_PRIVATE_KEY: str = ""
    INTERNAL_JWT_KID: str = "internal"  # environment-neutral fallback; in production gen-secrets injects a random kid
    INTERNAL_JWT_AUDIENCE: str = "gshare-internal"
    INTERNAL_JWT_TTL_SEC: int = 300
    INTERNAL_JWKS_URL: str = "https://api.gshare.internal/.well-known/gshare-internal-jwks.json"

    # ── User auth: email+password local login (HS256 bearer JWT). No API Key. ──
    AUTH_ALLOW_LOCAL_PASSWORD: bool = True    # email+password login (the only user auth path)
    # HS256 signing key for user tokens. Deliberately has no default in code — it comes from .env or
    # external-secrets, and an empty value fails fast at startup.
    # Generate with: openssl rand -hex 32. hack/gen-secrets.sh writes it into .env or a Secret.
    USER_JWT_SECRET: str = ""
    # Bootstrap administrator: an email and initial password that guarantee a super_admin account at
    # startup, from GSHARE_BOOTSTRAP_ADMIN_EMAIL and GSHARE_BOOTSTRAP_ADMIN_PASSWORD. The display
    # name is edited from the console.
    BOOTSTRAP_ADMIN_EMAIL: str = ""
    BOOTSTRAP_ADMIN_PASSWORD: str = ""
    # ── Automatic registration of the local cluster (in-cluster, all-in-one) ──
    # When the control plane runs inside the cluster it drives, the external kubeconfig registration
    # flow does not apply, so the local Cluster row is created at startup instead. It is the foreign
    # key target for operator inventory and sessions. Leave this False in production, where external
    # clusters are registered explicitly through the API. The id must match the operator's
    # --cluster-id.
    BOOTSTRAP_LOCAL_CLUSTER: bool = False
    LOCAL_CLUSTER_ID: str = "clu_local"      # must equal operator.clusterId; inventory foreign keys depend on it
    LOCAL_CLUSTER_NAME: str = "local"
    # Base directory the external clusters' kubeconfigs are projected into. Plaintext kubeconfigs
    # are never stored in the database — they are only ever read from a mounted file. On Kubernetes
    # external-secrets projects them here; on Compose and bare metal, mount them directly at
    # <dir>/<cluster_id>/kubeconfig.
    CLUSTER_KUBECONFIG_DIR: str = "/var/run/secrets/gshare/clusters"

    # ── Credit / billing knobs ──
    CONNECTION_TOKEN_TTL_SEC: int = 300      # one-time cnx_ token TTL
    # ── Session connect routing (path-based, single domain) ──
    # Session connect URLs are
    # {SESSION_URL_SCHEME}://{SESSION_DOMAIN}/proxy/{cr}/{lab|terminal|code}. SESSION_DOMAIN is the
    # console domain and must match the operator's --session-domain. Test setups reaching a NodePort
    # directly use http.
    SESSION_DOMAIN: str = "gshare.example.com"
    SESSION_URL_SCHEME: str = "https"
    SESSIONS_NAMESPACE: str = "gshare-sessions"        # namespace holding session pods and services
    # Console image builds: registry prefix kaniko pushes to; final ref is
    # {BUILD_REGISTRY}/{user_id}/{name}:{tag} (lowercased). Env: GSHARE_BUILD_REGISTRY.
    BUILD_REGISTRY: str = "registry.gshare.internal/gshare-user"
    BILLING_INTERVAL_SEC: int = 60
    IDLE_TIMEOUT_SEC: int = 1800            # stamped on the CR; operator reaper idle window
    # Grace window in seconds after credits run out. Still unpaid when it elapses, the session is
    # gracefully paused or yielded. (See docs/paper/manuscript, §Design.)
    GRACE_PERIOD_SEC: int = 600
    # ── Per-card GPU pools ──
    # When on (with the operator's --per-card-mode), pool membership is a per-CARD ledger fact:
    # every GPU session flows through hami-scheduler pinned to the reserved card, exclusive is a
    # 100% HAMi slice, and the gshare.io/gpu-mode node labels stop mattering for placement.
    PER_CARD_MODE: bool = False

    # Audit-log retention in days; 0 keeps everything (not recommended at scale).
    AUDIT_RETENTION_DAYS: int = 180

    # ── Queue fairness (see app/domain/queue_ranking.py). Constants are bounded so that the
    # maximum combined penalty (10*ACTIVE + 24*RECENT) stays below the 1440-minute aging cap:
    # a user with zero usage always outranks a non-priority heavy user eventually. ──
    QUEUE_ACTIVE_PENALTY: float = 30.0        # minutes of aging lost per already-active GPU session
    QUEUE_RECENT_HOURS_PENALTY: float = 2.0   # minutes of aging lost per GPU-hour used in the last 24h

    # ── Balance guardrail for fractional allocations (anti-fragmentation) ──
    # The core share is measured against the whole physical GPU (100), and the VRAM share against
    # the chosen model's full-card VRAM. When the two diverge by more than GPU_BALANCE_TOLERANCE —
    # 2% of VRAM with 100% of the cores, say — the allocation is treated as hoarding and rejected.
    # The full-card reference comes from the per-model offering.gpu_mem_mb, falling back to the
    # constant below only when the offering has none, which keeps mixed fleets working.
    GPU_REFERENCE_MEM_MB: int = 49152       # fallback full-card VRAM for the balance check, used only when the offering has none
    GPU_BALANCE_TOLERANCE: float = 2.0      # allowed ratio between the core and VRAM shares; 1 forces strict proportionality
    GPU_BALANCE_FLOOR: float = 0.05         # floor on the denominator, so very small shares stay allowed for light workloads
    GPU_MIN_FRACTIONAL_MEM_MB: int = 1024   # minimum VRAM per fractional slice, in MB; the same threshold the console uses to disable tiers
    # Placement policy for fractional slices: binpack (tightest two-dimensional fit, consolidating
    # onto cards and keeping empty ones free) or spread (balance the load).
    GPU_PACKING: str = "binpack"
    PROMETHEUS_URL: str = "http://prometheus.monitoring.svc:9090"  # source of aggregated DCGM utilisation
    # ── Host admission headroom ──
    # Host headroom kept out of admission on every node, covering the kubelet/system reserve and
    # daemonset pods, so a session admitted against inventory capacity still schedules. A card only
    # counts as a placement candidate when its node has cpu/mem left for the session's request.
    NODE_RESERVED_CPU: int = 1
    NODE_RESERVED_MEM_GB: int = 2
    # Rate for keeping persistent storage (data volumes), billed continuously per GB-hour against
    # the provisioned quota_gb, whether or not a session is running. 0 disables storage billing.
    # Tunable through GSHARE_STORAGE_CREDIT_PER_GB_HOUR without a rebuild.
    STORAGE_CREDIT_PER_GB_HOUR: float = 0.01
    # How long a deleted volume's data is kept before the operator reclaims its PVC (and, through
    # the CSI driver, the dataset on the storage node). Soft-delete keeps the ledger row; this is
    # the window in which an accidental delete can still be recovered by an operator on the storage
    # side. 0 reclaims on the next sync. GSHARE_VOLUME_RECLAIM_GRACE_HOURS.
    VOLUME_RECLAIM_GRACE_HOURS: float = 24
    # Rate multiplier for spot sessions. A preemptible session borrows a card a resident yielded and
    # is reclaimed when the resident returns, so it pays only this fraction of the normal rate
    # (0.3 = 30%). (See docs/paper/manuscript, §Design.)
    SPOT_DISCOUNT: float = 0.3
    # TTL on a yield resume reservation. A session held losslessly in host RAM by an in-place yield
    # gets a lossless reclaim if it is topped up within this window; past it, the session is demoted
    # to durable — the hold is given up, resume becomes cold, and the card and the priority claim
    # are returned. This is the boundary that prevents indefinite occupancy.
    YIELD_RESERVATION_TTL_SEC: int = 1800
    # Per-node host-RAM budget for yields, as a fraction of node RAM. Evicted VRAM lives in host
    # RAM, so once yields exceed this share the oldest and lowest-priority ones are gracefully
    # demoted to free memory. 0 disables the pressure-driven demotion. (See docs/paper/manuscript,
    # §Design — the host-RAM bound.)
    YIELD_HOST_RAM_FRACTION: float = 0.5
    # Allow fractional borrowing. When true, fractional preemptible sessions share a yielded card,
    # which requires the HAMi extender path (operator.hamiYieldExtender). When false — the default,
    # using the device-plugin bypass — only exclusive sessions can borrow, because the bypass cannot
    # borrow fractionally. (See build/hami-fork.)
    YIELD_FRACTIONAL_BORROW: bool = False

    def require_runtime_secrets(self) -> None:
        """Called at startup: fail immediately when a secret required in production is empty.

        No secret has a default in code — they come from .env or external-secrets — so this is what
        stops a forgotten injection from silently signing tokens with a weak, published value. The
        internal RS256 key is checked separately by ``internal_jwt`` at signing time, so only the
        user token signing key is validated here. """
        missing: list[str] = []
        if self.AUTH_ALLOW_LOCAL_PASSWORD and not self.USER_JWT_SECRET:
            missing.append("GSHARE_USER_JWT_SECRET")
        if missing:
            raise RuntimeError(
                "required secrets are unset: " + ", ".join(missing)
                + " — generate them with hack/gen-secrets.sh and inject via .env or external-secrets."
            )


settings = Settings()
