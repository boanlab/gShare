# GShare cluster operator — kubebuilder-style Makefile

IMG ?= ghcr.io/gshare/cluster-operator:v1
ENVTEST_K8S_VERSION = 1.30.0

# Local bin for tooling (controller-gen, setup-envtest, kustomize).
LOCALBIN ?= $(shell pwd)/bin
CONTROLLER_GEN ?= $(LOCALBIN)/controller-gen
ENVTEST ?= $(LOCALBIN)/setup-envtest
KUSTOMIZE ?= $(LOCALBIN)/kustomize

CONTROLLER_TOOLS_VERSION ?= v0.15.0
# release-0.19 or newer: earlier setup-envtest resolves assets from the retired
# storage.googleapis.com/kubebuilder-tools bucket, which now answers 401.
ENVTEST_VERSION ?= release-0.19

CRD_OPTIONS ?= crd:generateEmbeddedObjectMeta=true

.PHONY: all
all: build

##@ Development

.PHONY: manifests
manifests: controller-gen ## Generate CRD + RBAC manifests into config/.
	$(CONTROLLER_GEN) rbac:roleName=gshare-operator $(CRD_OPTIONS) \
		paths="./api/..." paths="./internal/..." paths="./cmd/..." \
		output:crd:artifacts:config=config/crd/bases \
		output:rbac:artifacts:config=config/rbac

.PHONY: generate
generate: controller-gen ## Generate zz_generated.deepcopy.go.
	$(CONTROLLER_GEN) object:headerFile="hack/boilerplate.go.txt" paths="./api/..."

.PHONY: fmt
fmt: ## go fmt.
	go fmt ./...

.PHONY: vet
vet: ## go vet.
	go vet ./...

.PHONY: lint
lint: ## Check gofmt and run go vet; the CI lint gate.
	@out=$$(gofmt -l .); if [ -n "$$out" ]; then printf 'gofmt needed:\n%s\n' "$$out"; exit 1; fi
	go vet ./...

.PHONY: test
test: manifests generate fmt vet envtest ## Run unit + envtest tests.
	@# Resolved in the recipe, not with $(shell ...): on a cold checkout setup-envtest is installed
	@# by the envtest prerequisite above, long after the Makefile is parsed.
	KUBEBUILDER_ASSETS="$$($(ENVTEST) use $(ENVTEST_K8S_VERSION) --bin-dir $(LOCALBIN) -p path)" \
		go test ./... -coverprofile cover.out

##@ Build

.PHONY: build
build: manifests generate fmt vet ## Build manager binary.
	go build -o bin/manager cmd/main.go

.PHONY: run
run: manifests generate fmt vet ## Run the operator against the current kubeconfig cluster.
	go run ./cmd/main.go

.PHONY: docker-build
docker-build: ## Build the operator image.
	docker build -t ${IMG} .

.PHONY: docker-push
docker-push: ## Push the operator image.
	docker push ${IMG}

# ── Image targets. The root Makefile delegates here, injecting IMAGE. ──
DOCKER   ?= docker
PLATFORM ?= linux/amd64
NOCACHE  ?=
IMAGE    ?= $(IMG)

.PHONY: image push
image: ## Build the operator image
	$(DOCKER) build --platform $(PLATFORM) $(NOCACHE) -t $(IMAGE) .

push: ## Push the operator image
	$(DOCKER) push $(IMAGE)

##@ Deployment

.PHONY: install
install: manifests ## Install CRDs into the cluster.
	kubectl apply -f config/crd/bases

.PHONY: uninstall
uninstall: manifests ## Remove CRDs from the cluster.
	kubectl delete -f config/crd/bases --ignore-not-found

.PHONY: deploy
deploy: manifests ## Deploy operator (RBAC + manager) to the cluster.
	kubectl apply -f config/rbac
	kubectl apply -f config/manager

.PHONY: undeploy
undeploy: ## Remove operator from the cluster.
	kubectl delete -f config/manager --ignore-not-found
	kubectl delete -f config/rbac --ignore-not-found

##@ Tooling

$(LOCALBIN):
	mkdir -p $(LOCALBIN)

.PHONY: controller-gen
controller-gen: $(LOCALBIN)
	test -s $(CONTROLLER_GEN) || GOBIN=$(LOCALBIN) go install sigs.k8s.io/controller-tools/cmd/controller-gen@$(CONTROLLER_TOOLS_VERSION)

.PHONY: envtest
envtest: $(LOCALBIN)
	test -s $(ENVTEST) || GOBIN=$(LOCALBIN) go install sigs.k8s.io/controller-runtime/tools/setup-envtest@$(ENVTEST_VERSION)
