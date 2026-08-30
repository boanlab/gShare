# GShare — top-level build and deployment entry points.
# Pipeline: clean -> images -> push -> compose / helm deploy.
# Every variable below can be overridden from the environment or the command line.

# ── Tooling and registry ──
DOCKER     ?= docker
COMPOSE    ?= docker compose
REGISTRY   ?= docker.io
ORG        ?= boanlab
TAG        ?= latest
PLATFORM   ?= linux/amd64
NOCACHE    ?=

# ── Image coordinates. `api` and `worker` share one backend image. ──
BACKEND_IMAGE  := $(REGISTRY)/$(ORG)/gshare-backend:$(TAG)
OPERATOR_IMAGE := $(REGISTRY)/$(ORG)/gshare-operator:$(TAG)
FRONTEND_IMAGE := $(REGISTRY)/$(ORG)/gshare-frontend:$(TAG)

# ── Kubernetes / Helm ──
HELM_RELEASE ?= gshare
SYS_NS       ?= gshare-system
CHART        ?= charts/gshare
PROD_VALUES  ?= deploy/values/dockerhub.yaml
# Site-specific overlay carrying the real domain. Applied automatically when present.
# Not committed (see .gitignore); start from deploy/values/domain.example.yaml.
DOMAIN_VALUES ?= deploy/values/domain.yaml
DOMAIN_VALUES_ARG := $(if $(wildcard $(DOMAIN_VALUES)),-f $(DOMAIN_VALUES),)
# Data-plane deployments (operator attached to an external control plane) need a
# signed internal JWT; these control the secret it lands in and its lifetime.
OP_JWT_SECRET ?= gshare-operator-internal-jwt
JWT_TTL       ?= 604800

BACKEND_DIR  ?= backend
OPERATOR_DIR ?= operator
FRONTEND_DIR ?= frontend

.DEFAULT_GOAL := help

.PHONY: help
help: ## List available targets
	@grep -hE '^[a-zA-Z0-9_.-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ── Housekeeping ──
.PHONY: clean
clean: ## Remove build artifacts and caches
	# Transient artifacts the e2e test container writes into its bind mount.
	rm -rf test/e2e/frontend/node_modules test/e2e/frontend/package-lock.json test/e2e/frontend/package.json
	rm -rf test/e2e/frontend/out test/e2e/frontend/out-live
	# Artifacts that only appear when building or testing directly on the host.
	rm -rf $(FRONTEND_DIR)/dist $(FRONTEND_DIR)/node_modules $(OPERATOR_DIR)/bin $(BACKEND_DIR)/.venv
	find . -type d -name __pycache__ -not -path '*/node_modules/*' -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned."

.PHONY: from-scratch
from-scratch: clean ## Rebuild every image without using the layer cache
	$(MAKE) images NOCACHE=--no-cache

# ── Image build and push (delegated to the per-component Makefiles) ──
DELEGATE = $(MAKE) -C $(1) $(2) IMAGE=$(3) DOCKER=$(DOCKER) PLATFORM=$(PLATFORM) NOCACHE=$(NOCACHE)

.PHONY: images
images: image-backend image-operator image-frontend ## Build all component images

.PHONY: image-backend
image-backend: ## Build the backend image (shared by api and worker)
	$(call DELEGATE,$(BACKEND_DIR),image,$(BACKEND_IMAGE))

.PHONY: image-operator
image-operator: ## Build the operator image
	$(call DELEGATE,$(OPERATOR_DIR),image,$(OPERATOR_IMAGE))

.PHONY: image-frontend
image-frontend: ## Build the frontend image
	$(call DELEGATE,$(FRONTEND_DIR),image,$(FRONTEND_IMAGE))

.PHONY: gen-openapi
gen-openapi: ## Regenerate the OpenAPI document and the frontend API types
	hack/gen-openapi.sh

.PHONY: login
login: ## Log in to the image registry (DOCKERHUB_USERNAME / DOCKERHUB_TOKEN)
	@if [ -n "$$DOCKERHUB_TOKEN" ]; then \
	  echo "$$DOCKERHUB_TOKEN" | $(DOCKER) login $(REGISTRY) -u "$${DOCKERHUB_USERNAME:-$(ORG)}" --password-stdin; \
	else $(DOCKER) login $(REGISTRY); fi

.PHONY: push
push: ## Push all component images
	$(call DELEGATE,$(BACKEND_DIR),push,$(BACKEND_IMAGE))
	$(call DELEGATE,$(OPERATOR_DIR),push,$(OPERATOR_IMAGE))
	$(call DELEGATE,$(FRONTEND_DIR),push,$(FRONTEND_IMAGE))

.PHONY: release
release: images push ## Build and push all images

# ── Local Docker Compose stack ──
.PHONY: gen-secrets
gen-secrets: ## Generate a .env file with random secrets
	hack/gen-secrets.sh

.PHONY: compose-up
compose-up: ## Start the local stack (API on :8080, console on :8000)
	$(COMPOSE) up -d --build

.PHONY: compose-down
compose-down: ## Stop the local stack and drop its volumes
	$(COMPOSE) down -v

.PHONY: compose-logs
compose-logs: ## Follow local stack logs
	$(COMPOSE) logs -f

.PHONY: compose-operator-token
compose-operator-token: ## Mint a 24h internal JWT for an external operator (CLUSTER_ID=clu_... required)
	@test -n "$(CLUSTER_ID)" || { echo "usage: make compose-operator-token CLUSTER_ID=clu_..."; exit 1; }
	@$(COMPOSE) exec -T gshare-api python -c \
	  "from app.auth.internal_jwt import sign_internal_jwt; print(sign_internal_jwt('operator:$(CLUSTER_ID)', ttl=86400))"

.PHONY: smoke
smoke: ## Wait for the local API to report healthy
	@echo "waiting for api…"; \
	for i in $$(seq 1 30); do \
	  curl -fsS http://localhost:8080/healthz >/dev/null 2>&1 && { echo "api OK"; exit 0; }; sleep 2; \
	done; echo "api did not become healthy"; exit 1

# ── Helm deployments ──
# The chart manages the system namespace itself (PSA labels live on it), so `--create-namespace`
# must NOT be used: helm would create the namespace without release ownership metadata and the
# chart's own Namespace manifest would then fail the ownership check on first install. Instead,
# pre-create the namespace carrying the Helm ownership metadata so the chart adopts it cleanly.
.PHONY: ensure-namespace
ensure-namespace:
	@kubectl get ns $(SYS_NS) >/dev/null 2>&1 || kubectl create ns $(SYS_NS)
	@kubectl label ns $(SYS_NS) app.kubernetes.io/managed-by=Helm --overwrite >/dev/null
	@kubectl annotate ns $(SYS_NS) meta.helm.sh/release-name=$(HELM_RELEASE) \
	  meta.helm.sh/release-namespace=$(SYS_NS) --overwrite >/dev/null

.PHONY: prod-deploy
prod-deploy: ensure-namespace ## Deploy against externally managed Postgres/Redis and secrets
	helm upgrade -i $(HELM_RELEASE) ./$(CHART) -n $(SYS_NS) \
	  -f $(PROD_VALUES) $(DOMAIN_VALUES_ARG) --set images.api.tag=$(TAG) --set images.worker.tag=$(TAG) \
	  --set images.operator.tag=$(TAG) --set images.frontend.tag=$(TAG)

.PHONY: deploy-incluster
deploy-incluster: ensure-namespace ## All-in-one deploy (chart provisions data tier, secrets, CRDs, namespaces)
	helm upgrade -i $(HELM_RELEASE) ./$(CHART) -n $(SYS_NS) \
	  -f deploy/values/incluster.yaml $(DOMAIN_VALUES_ARG) \
	  --set images.api.tag=$(TAG) --set images.worker.tag=$(TAG) \
	  --set images.operator.tag=$(TAG) --set images.frontend.tag=$(TAG)

.PHONY: deploy-dataplane
deploy-dataplane: ## Deploy the data plane only (operator, CRDs, namespaces, RBAC) against an external control plane
	@test -n "$(CLUSTER_ID)" || { echo "usage: make deploy-dataplane CLUSTER_ID=clu_... CONTROL_PLANE_URL=http://<host>:8080"; exit 1; }
	@test -n "$(CONTROL_PLANE_URL)" || { echo "CONTROL_PLANE_URL is required and must be reachable from cluster pods"; exit 1; }
	# 1. Install the chart first so the operator is running and can pick the secret up;
	#    it mounts the internal-JWT secret optionally.
	$(MAKE) ensure-namespace
	helm upgrade -i $(HELM_RELEASE) ./$(CHART) -n $(SYS_NS) \
	  -f deploy/values/dataplane.yaml $(DOMAIN_VALUES_ARG) \
	  --set images.operator.tag=$(TAG) \
	  --set operator.clusterId=$(CLUSTER_ID) \
	  --set operator.controlPlaneUrl=$(CONTROL_PLANE_URL)
	# 2. Have the external control plane sign an internal JWT and inject it into the
	#    target cluster; the operator picks it up on its next reconcile.
	$(MAKE) dataplane-token CLUSTER_ID=$(CLUSTER_ID)
	@echo "data plane deployed. token TTL=$(JWT_TTL)s — renew with 'make dataplane-token CLUSTER_ID=$(CLUSTER_ID)'."

.PHONY: dataplane-token
dataplane-token: ## Re-mint the operator internal JWT and inject it into the target cluster
	@test -n "$(CLUSTER_ID)" || { echo "usage: make dataplane-token CLUSTER_ID=clu_..."; exit 1; }
	@TOKEN=$$($(COMPOSE) exec -T gshare-api python -c \
	  "from app.auth.internal_jwt import sign_internal_jwt; print(sign_internal_jwt('operator:$(CLUSTER_ID)', ttl=$(JWT_TTL)))") && \
	  test -n "$$TOKEN" || { echo "signing failed — is the compose stack up and 'make gen-secrets' done?"; exit 1; }; \
	  kubectl create secret generic $(OP_JWT_SECRET) -n $(SYS_NS) \
	    --from-literal=internal-jwt="$$TOKEN" --dry-run=client -o yaml | kubectl apply -f -
	@echo "$(OP_JWT_SECRET) injected into ns=$(SYS_NS)."

.PHONY: dataplane-token-cron
dataplane-token-cron: ## Install an idempotent daily cron (03:00) that renews the operator token
	@test -n "$(CLUSTER_ID)" || { echo "usage: make dataplane-token-cron CLUSTER_ID=clu_... [MASTER=ubuntu@host]"; exit 1; }
	@line="0 3 * * * cd $(CURDIR) && CLUSTER_ID=$(CLUSTER_ID) MASTER=$(MASTER) ./hack/renew-operator-token.sh >> /tmp/gshare-token-renew.log 2>&1"; \
	( crontab -l 2>/dev/null | grep -v 'renew-operator-token.sh' || true; echo "$$line" ) | crontab - ; \
	echo "cron installed (daily 03:00): $$line"

.PHONY: prod-undeploy
prod-undeploy: ## Uninstall the Helm release
	-helm uninstall $(HELM_RELEASE) -n $(SYS_NS)

.PHONY: deploy-monitoring
deploy-monitoring: ## Optional: Prometheus + dcgm/node/kube-state exporters (usage panels, admin monitoring, idle reaper)
	kubectl apply -f deploy/monitoring/monitoring-stack.yaml
	@echo "point the reaper at it: helm upgrade ... --set operator.prometheusUrl=http://prometheus.monitoring.svc:9090"
	@echo "(deploy/monitoring/dcgm-prometheus.yaml is the minimal dcgm-only variant)"

.PHONY: hami-fork-image
hami-fork-image: ## Optional: build and push the GShare-patched HAMi scheduler extender
	build/hami-fork/build.sh

# ── Test and lint ──
.PHONY: test
test: ## Run unit and functional tests for every component (no real GPU required)
	$(MAKE) -C $(BACKEND_DIR) test ARGS='-m "not realgpu"'
	$(MAKE) -C $(OPERATOR_DIR) test
	$(MAKE) -C $(FRONTEND_DIR) test

.PHONY: lint
lint: ## Lint every component
	$(MAKE) -C $(BACKEND_DIR) lint
	$(MAKE) -C $(OPERATOR_DIR) lint
	$(MAKE) -C $(FRONTEND_DIR) lint

.PHONY: helm-lint
helm-lint: ## Lint the chart and render it against every values overlay
	helm lint $(CHART)
	@for v in deploy/values/*.yaml; do \
	  case "$$v" in *domain.example.yaml) continue;; esac; \
	  echo "-- helm template -f $$v"; \
	  helm template $(HELM_RELEASE) $(CHART) -f "$$v" >/dev/null || exit 1; \
	done

.PHONY: ci
ci: lint test helm-lint ## Full local CI gate

# ── Containerised tests (leave no artifacts on the host) ──
.PHONY: test-docker
test-docker: test-backend-docker test-frontend-docker ## Run all containerised tests

.PHONY: test-backend-docker
test-backend-docker: ## Run backend tests inside a container
	$(DOCKER) build --target test -t gshare-backend-test $(BACKEND_DIR)
	$(DOCKER) run --rm gshare-backend-test

.PHONY: test-frontend-docker
test-frontend-docker: ## Run frontend tests inside a container
	$(DOCKER) build --target test -t gshare-frontend-test $(FRONTEND_DIR)
	$(DOCKER) run --rm gshare-frontend-test

.PHONY: test-docker-clean
test-docker-clean: ## Remove the test images
	-$(DOCKER) rmi gshare-backend-test gshare-frontend-test
