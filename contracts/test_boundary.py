"""GPL-boundary guard (design §6.6, NOTICE; run via `make test-boundary`).

Statically scans every platform module and fails if any of them imports
prar-agent's internal Python symbols, or if any module other than the single
adapter knows the engine's wire paths. That arm's-length boundary is what is
designed to keep bagu-trainer a separate program from the GPL engine; collapsing
it (importing engine internals in-process) risks making this repo a derivative
work. This test reads source only, so it runs without the engine — or even
httpx/websockets — installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# All platform-side packages that must respect the boundary.
PLATFORM_PACKAGES = ("orchestrator", "platform_service", "paper_cli", "contracts")
# Only runtime packages are scanned for wire-path confinement (test code may name
# the paths in order to assert them).
RUNTIME_PACKAGES = ("orchestrator", "platform_service", "paper_cli")

# The engine's top-level package is `app` (submodule layout: backend/src/app/...).
# NOTICE forbids importing its internal namespaces in-process.
FORBIDDEN_IMPORT_ROOTS = {"app"}
FORBIDDEN_IMPORT_PREFIXES = (
    "app.core",
    "app.llm",
    "app.memory",
    "app.tools",
    "app.services",
    "app.api",
    "app.db",
)

# The single adapter is the ONLY runtime file allowed to know the engine wire paths.
SINGLE_ADAPTER = REPO_ROOT / "orchestrator" / "prar_bridge.py"
ENGINE_PATH_MARKERS = (
    "/api/sessions",
    "/api/adapters",
    "/api/memories",
    "/api/providers",
    "/api/ws/",
)


def _python_files(packages: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for pkg in packages:
        base = REPO_ROOT / pkg
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module)
    return roots


def _is_forbidden(root: str) -> bool:
    return root in FORBIDDEN_IMPORT_ROOTS or root.startswith(FORBIDDEN_IMPORT_PREFIXES)


@pytest.mark.parametrize(
    "path",
    _python_files(PLATFORM_PACKAGES),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_no_engine_internal_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = sorted(r for r in _imported_roots(tree) if _is_forbidden(r))
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} imports prar-agent internals {offenders}; "
        "the GPL boundary (design §6.6, NOTICE) requires reaching the engine only "
        "over its out-of-process HTTP/WS API via orchestrator/prar_bridge.py."
    )


def test_single_adapter_exists() -> None:
    assert SINGLE_ADAPTER.is_file(), "orchestrator/prar_bridge.py (the single adapter) is missing"


def test_engine_wire_paths_confined_to_single_adapter() -> None:
    leaks: list[str] = []
    adapter = SINGLE_ADAPTER.resolve()
    for path in _python_files(RUNTIME_PACKAGES):
        if path.resolve() == adapter:
            continue
        text = path.read_text(encoding="utf-8")
        hits = [m for m in ENGINE_PATH_MARKERS if m in text]
        if hits:
            leaks.append(f"{path.relative_to(REPO_ROOT)} -> {hits}")
    assert not leaks, (
        "engine API paths must be funnelled through the single adapter "
        f"orchestrator/prar_bridge.py; also found in: {leaks}"
    )
