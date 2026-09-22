"""前端与后端的**契约对齐**：路由表与版本号（比短码更早失效的两样东西）。

短码的对齐由 ``test_error_codes.py`` 守着，但真让学员撞墙的还有两种漂移：

1. **前端调了一个后端没有的路径**——拼错前缀、后端删了端点、或者部署的后端是旧版本。后者测不到
   （那是部署事实），但"仓库里前端调了一个仓库里后端不存在的路径"测得到，而且它一旦发生，症状
   是 Starlette 自己的 ``{"detail":"Not Found"}``——一个不在我们短码表里的字符串。
   （真发生过：VM 上跑着旧后端，主页"最近练习"显示「未识别的错误：Not Found」。）
2. **两端的版本号不一致**——前端拿 ``EXPECTED_PLATFORM_VERSION`` 和 ``/meta`` 里的
   ``platform_version`` 比，不一致就在主页直说"后端比前端旧"。这个检测只有在**两个常量同源**时
   才有意义，所以这里把 ``frontend/src/version.ts`` 和后端的 ``PLATFORM_VERSION`` 钉在一起。

两条都用最笨的文本扫描 + 真 app 的 OpenAPI 表，不引入解析器。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
FRONTEND_SRC = REPO / "frontend" / "src"
API_TS = FRONTEND_SRC / "api.ts"
VERSION_TS = FRONTEND_SRC / "version.ts"

#: ``request<Out>(`/path`)`` —— 前端所有 HTTP 调用的唯一入口（见 api.ts 的模块 docstring）。
_REQUEST = re.compile(r"""request<[^>]*>\(\s*(?P<q>[`'"])(?P<path>.*?)(?P=q)""", re.DOTALL)

#: 路径里的动态段：前端是 ``${encodeURIComponent(x)}``，后端是 ``{x}``。统一成 ``{}``。
_TEMPLATE = re.compile(r"\$\{[^}]*\}")
_PARAM = re.compile(r"\{[^}]+\}")


def _normalize(path: str) -> str:
    # 先砍掉查询串（前端拼在路径尾巴上的 `?` 参数），再把两端各自的动态段统一成 `{}`。
    stripped = path.split("${query(", 1)[0]
    return _PARAM.sub("{}", _TEMPLATE.sub("{}", stripped)).rstrip("/") or "/"


def _spa_paths() -> set[str]:
    text = API_TS.read_text(encoding="utf-8")
    return {_normalize(m.group("path")) for m in _REQUEST.finditer(text)}


def _backend_paths() -> set[str]:
    import tempfile

    from platform_api.app import create_app
    from platform_api.demo_repo import build_demo_track_repo

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        app = create_app(content_dir=build_demo_track_repo(root / "demo"), forge_base=root / "ws")
        return {_normalize(p) for p in app.openapi()["paths"]}


def test_every_path_the_spa_calls_exists_on_the_backend() -> None:
    spa = _spa_paths()
    # 自检：正则得真扫到东西，否则这条会"因为什么都没扫到"而假绿。
    assert len(spa) >= 15, f"只从 api.ts 扫到 {sorted(spa)}——正则或文件结构变了？"

    backend = _backend_paths()
    missing = sorted(spa - backend)
    assert not missing, (
        f"前端会调这些路径，但后端没有：{missing}。"
        "要么是拼错了/端点被删了，要么是前后端版本不一致——后者学员看到的是"
        "「后端没有这个接口」（HTTP 404，detail 是 Starlette 的 'Not Found'）。"
    )


def test_the_version_the_spa_expects_is_the_one_the_backend_reports() -> None:
    """两端版本号必须同源：前端靠它识破"后端比前端旧"，常量漂了就永远识破不了。"""
    from platform_api.deps import PLATFORM_VERSION

    found = re.search(
        r"EXPECTED_PLATFORM_VERSION\s*=\s*'([^']+)'", VERSION_TS.read_text(encoding="utf-8")
    )
    assert found, f"没能从 {VERSION_TS} 里解析出 EXPECTED_PLATFORM_VERSION"
    assert found.group(1) == PLATFORM_VERSION, (
        f"前端期望 {found.group(1)}，后端是 {PLATFORM_VERSION}：升版本时两边都要改。"
    )


def test_meta_reports_the_same_version_the_spa_expects(client: TestClient) -> None:
    assert client.get("/meta").json()["platform_version"] == client.app.version
