# Standard test-* collection. Pulled out of the root Makefile to keep it
# uncluttered.

.PHONY: test-unit test-coverage test-format test-lint

test-unit: install-dev  ## Run pytest
	$(PYTHON) -m pytest

test-coverage: install-dev  ## Run pytest with coverage threshold
	$(PYTHON) -m pytest --cov=$(PACKAGE_NAME) --cov-report=term --cov-fail-under=80

test-format: install-dev  ## Check formatting (exits non-zero if changes needed)
	$(PYTHON) -m black --check $(PACKAGE_NAME) tests

test-lint: install-dev  ## Lint with flake8
	$(PYTHON) -m flake8 --max-line-length=88 --extend-ignore=E203,W503 $(PACKAGE_NAME) tests
