# gshare-frontend — image, test, and lint targets. The root Makefile delegates here, injecting IMAGE.

NPM ?= npm

# ── Image ──
DOCKER   ?= docker
PLATFORM ?= linux/amd64
NOCACHE  ?=
IMAGE    ?= gshare-frontend:dev

.PHONY: image push install test lint build
image: ## Build the image (SPA served by nginx)
	$(DOCKER) build --platform $(PLATFORM) $(NOCACHE) -t $(IMAGE) .

push: ## Push the image
	$(DOCKER) push $(IMAGE)

install: ## Install dependencies
	$(NPM) ci || $(NPM) install

test: ## Run the vitest unit tests
	$(NPM) run test

lint: ## eslint
	$(NPM) run lint

build: ## Type-check and build for production
	$(NPM) run build
