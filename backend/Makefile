# gshare-backend — image, test, and lint targets. The root Makefile delegates here, injecting IMAGE.

PYTEST ?= pytest
RUFF   ?= ruff
ARGS   ?=
COV    ?= --cov=app --cov-report=term-missing

# ── Image ──
DOCKER   ?= docker
PLATFORM ?= linux/amd64
NOCACHE  ?=
IMAGE    ?= gshare-backend:dev

.PHONY: image push test cov lint fmt
image: ## Build the image, shared by api and worker
	$(DOCKER) build --platform $(PLATFORM) $(NOCACHE) -t $(IMAGE) .

push: ## Push the image
	$(DOCKER) push $(IMAGE)

test: ## Run unit and functional tests with a coverage summary
	$(PYTEST) $(ARGS) $(COV)

cov: ## Write an HTML coverage report to htmlcov/
	$(PYTEST) $(ARGS) --cov=app --cov-report=html --cov-report=term-missing

lint: ## Lint with ruff
	$(RUFF) check app tests

fmt: ## Apply ruff autofixes
	$(RUFF) check --fix app tests
