# Makefile for weather_exporter

.PHONY: check help clean format install install-dev run build-image run-image push-image

PACKAGE_NAME ?= weather_exporter
PROJECT_NAME ?= weather-exporter

# Container image coordinates. IMAGE_TAG is read from the pyproject version so
# the pushed tag always matches the released version.
IMAGE_NAME ?= $(PROJECT_NAME)
IMAGE_TAG ?= $(shell sed -n 's/^version *= *"\(.*\)"/\1/p' pyproject.toml)
REGISTRY ?= ghcr.io/jewzaam

# Container builder: prefer docker, fall back to podman. Override with
# DOCKER=<path> when neither is on PATH.
DOCKER ?= $(shell command -v docker 2>/dev/null || command -v podman 2>/dev/null)

# Local run (run / run-image). Override per-environment.
CONFIG_FILE ?= config.yaml

ifeq ($(OS),Windows_NT)
    VENV_DIR ?= .venv
    PYTHON ?= $(VENV_DIR)/Scripts/python.exe
else
    VENV_DIR ?= .venv
    PYTHON ?= $(VENV_DIR)/bin/python
endif

# Interpreter used to bootstrap the venv. CI overrides to `python` so
# the venv is pinned to the matrix Python installed by setup-python.
PY_SYS ?= python3

# Test targets are pulled into make/test.mk for readability.
-include make/test.mk

$(info venv: $(VENV_DIR))

check: test-format test-lint test-unit test-coverage  ## Run full quality gate (default)

.DEFAULT_GOAL := check

help:  ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-32s\033[0m %s\n", $$1, $$2}'

$(PYTHON):
	$(PY_SYS) -m venv $(VENV_DIR)

clean:  ## Remove build artifacts and caches
	rm -rf build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

format: install-dev  ## Rewrite sources with black
	$(PYTHON) -m black $(PACKAGE_NAME) tests

install: $(PYTHON)  ## Install into the local venv
	$(PYTHON) -m pip install .

install-dev: $(PYTHON)  ## Editable install + dev extras
	$(PYTHON) -m pip install -e ".[dev]"

run: install  ## Run the exporter locally (needs WEATHER_API_KEY)
	@if [ -z "$(WEATHER_API_KEY)" ]; then echo "run: set WEATHER_API_KEY=<key> or weather-service rejects every poll with 401." >&2; exit 1; fi
	$(PYTHON) -m weather_exporter --config $(CONFIG_FILE)

build-image:  ## Build the OCI container image (docker or podman)
	@if [ -z "$(DOCKER)" ]; then echo "build-image: no container builder on PATH (docker or podman). Set DOCKER=<path> to override." >&2; exit 1; fi
	"$(DOCKER)" build -t $(IMAGE_NAME):$(IMAGE_TAG) .

run-image:  ## Run the built container locally (needs WEATHER_API_KEY and a config)
	@if [ -z "$(DOCKER)" ]; then echo "run-image: no container runtime on PATH (docker or podman). Set DOCKER=<path> to override." >&2; exit 1; fi
	@if [ -z "$(WEATHER_API_KEY)" ]; then echo "run-image: set WEATHER_API_KEY=<key> so weather-service accepts the polls." >&2; exit 1; fi
	"$(DOCKER)" run --rm --name $(PROJECT_NAME) \
	    -e WEATHER_API_KEY=$(WEATHER_API_KEY) \
	    -e OPENWEATHERMAP_API_KEY=$(OPENWEATHERMAP_API_KEY) \
	    -e WEATHERGOV_AGENT=$(WEATHERGOV_AGENT) \
	    -v $(abspath $(CONFIG_FILE)):/etc/weather-exporter/config.yaml:ro \
	    $(IMAGE_NAME):$(IMAGE_TAG)

push-image: build-image  ## Tag + push the OCI image to $(REGISTRY)
	@if [ -z "$(DOCKER)" ]; then echo "push-image: no container runtime on PATH (docker or podman). Set DOCKER=<path> to override." >&2; exit 1; fi
	"$(DOCKER)" tag $(IMAGE_NAME):$(IMAGE_TAG) $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)
	"$(DOCKER)" tag $(IMAGE_NAME):$(IMAGE_TAG) $(REGISTRY)/$(IMAGE_NAME):latest
	"$(DOCKER)" push $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)
	"$(DOCKER)" push $(REGISTRY)/$(IMAGE_NAME):latest
