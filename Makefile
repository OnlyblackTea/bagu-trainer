# 发卷平台 —— 开发者任务。
# 引擎（prar-agent）是 pin 到公开 tag 的 git 子模块；这里不构建、也不导入它。
# 我们只通过 HTTP/WS 编排它（见 NOTICE / 设计文档 §6.6-§6.7）。

SUBMODULE      := prar-agent
PINNED_TAG     := v0.1.0
PYTEST         := python -m pytest
RUFF           := python -m ruff

.DEFAULT_GOAL := help

.PHONY: help bootstrap clone-hint submodule-status test test-boundary test-contract \
        lint fmt bump-submodule clean

help: ## 显示所有可用 target。
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

clone-hint: ## 提醒贡献者如何连子模块一起 clone。
	@echo "一次性 clone（含子模块）：git clone --recursive https://github.com/OnlyblackTea/paper-platform.git"
	@echo "或者普通 clone 之后运行：make bootstrap"

bootstrap: ## 初始化并拉取 pin 的 prar-agent 子模块（普通 clone 之后运行）。
	git submodule update --init --recursive
	@echo "子模块状态："
	@git submodule status $(SUBMODULE)

submodule-status: ## 显示子模块 pin 的 commit/tag。
	@git submodule status $(SUBMODULE)

test: ## 运行全部契约 + 边界测试（每次子模块 bump 的关卡）。
	$(PYTEST)

test-boundary: ## 只运行 GPL 边界守卫（确保未导入引擎内部）。
	$(PYTEST) contracts/test_boundary.py

test-contract: ## 只运行 prar-agent 的 HTTP/WS 契约测试。
	$(PYTEST) contracts/test_prar_contract.py

lint: ## Ruff 检查。
	$(RUFF) check orchestrator platform_service paper_cli contracts

fmt: ## Ruff 格式化 + import 排序。
	$(RUFF) format orchestrator platform_service paper_cli contracts
	$(RUFF) check --fix orchestrator platform_service paper_cli contracts

bump-submodule: ## 把 prar-agent bump 到 PINNED_TAG，跑契约测试，并把指针留在暂存区。
	@echo ">> 拉取 tag 并检出 $(SUBMODULE) @ $(PINNED_TAG)"
	cd $(SUBMODULE) && git fetch --tags --quiet && git checkout --quiet $(PINNED_TAG)
	@echo ">> 针对新 pin 运行契约测试（必须全绿才能提交这次 bump）"
	$(MAKE) test
	git add $(SUBMODULE)
	@echo ">> 指针已暂存。检查 'git submodule status'，全绿即可提交。"

clean: ## 清理缓存与本地阅卷产物。
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov .grading
	find . -type d -name __pycache__ -not -path './$(SUBMODULE)/*' -prune -exec rm -rf {} +
