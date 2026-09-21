# 发卷平台 —— 开发者任务。
# 平台是引擎无关的（设计支柱 D4）：没有子模块、没有引擎、没有 WS。
# 阅卷 / forge / AI 评审三个后端都经 Protocol 缝注入，当前是"诚实占位"桩。

PYTEST := python -m pytest
RUFF   := python -m ruff

.DEFAULT_GOAL := help

.PHONY: help test lint fmt serve web e2e clean

help: ## 显示所有可用 target。
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

test: ## 运行后端全部测试。
	$(PYTEST)

lint: ## Ruff 检查。
	$(RUFF) check .

fmt: ## Ruff 格式化 + import 排序。
	$(RUFF) format .
	$(RUFF) check --fix .

serve: ## 起后端（默认用内置 demo Track）。
	python -m platform_api.serve

web: ## 起学习者 SPA 开发服务器。
	cd frontend && npm run dev

e2e: ## 真浏览器端到端（Playwright，自带后端）。
	cd frontend && npx playwright test

clean: ## 清理缓存与本地阅卷产物。
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov .grading
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
