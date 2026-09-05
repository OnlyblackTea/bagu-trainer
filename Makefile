# Paper Platform — developer tasks.
# The engine (prar-agent) is a git submodule pinned to a public tag; nothing here
# builds or imports it. We only orchestrate it over HTTP/WS (see NOTICE / design §6.6-§6.7).

SUBMODULE      := prar-agent
PINNED_TAG     := v0.1.0
PYTEST         := python -m pytest
RUFF           := python -m ruff

.DEFAULT_GOAL := help

.PHONY: help bootstrap clone-hint submodule-status test test-boundary test-contract \
        lint fmt bump-submodule clean

help: ## Show available targets.
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

clone-hint: ## Remind contributors how to clone with the submodule.
	@echo "Clone in one shot:  git clone --recursive https://github.com/OnlyblackTea/paper-platform.git"
	@echo "Or after a plain clone run:  make bootstrap"

bootstrap: ## Initialize + fetch the pinned prar-agent submodule (run after a plain clone).
	git submodule update --init --recursive
	@echo "Submodule state:"
	@git submodule status $(SUBMODULE)

submodule-status: ## Show the pinned submodule commit/tag.
	@git submodule status $(SUBMODULE)

test: ## Run all contract + boundary tests (gates every submodule bump).
	$(PYTEST)

test-boundary: ## Run ONLY the GPL-boundary guard (no engine internals imported).
	$(PYTEST) contracts/test_boundary.py

test-contract: ## Run ONLY the prar-agent HTTP/WS contract tests.
	$(PYTEST) contracts/test_prar_contract.py

lint: ## Ruff lint.
	$(RUFF) check orchestrator platform_service paper_cli contracts

fmt: ## Ruff format + import sort.
	$(RUFF) format orchestrator platform_service paper_cli contracts
	$(RUFF) check --fix orchestrator platform_service paper_cli contracts

bump-submodule: ## Bump prar-agent to PINNED_TAG, run contract tests, leave the pointer staged.
	@echo ">> Fetching tags and checking out $(SUBMODULE) @ $(PINNED_TAG)"
	cd $(SUBMODULE) && git fetch --tags --quiet && git checkout --quiet $(PINNED_TAG)
	@echo ">> Running contract tests against the new pin (must be green to commit the bump)"
	$(MAKE) test
	git add $(SUBMODULE)
	@echo ">> Pointer staged. Review 'git submodule status' then commit if green."

clean: ## Remove caches and local grading artifacts.
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov .grading
	find . -type d -name __pycache__ -not -path './$(SUBMODULE)/*' -prune -exec rm -rf {} +
