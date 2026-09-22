"""短码必须两端对齐（设计 spec §C.8）。

§C.8 的约定是"错误一律 ``{"detail": "<短码>"}``，措辞交给 SPA"。这条约定有一个**沉默的失效模式**：
后端新发一个短码，前端 `humanize()` 的 switch 里没有它，于是落到 default——学员看到
「后端返回了未识别的错误：instance_not_found」，一个既没解释也没出路的死胡同。它不会让任何测试
变红，也没有任何编译期检查（Python 的字符串和 TS 的 union 谁也不认识谁）。

所以这里用最笨但有效的办法把两端钉在一起：**扫后端源码里所有 ``detail="…"`` 字面量，逐个要求
出现在 ``frontend/src/api.ts`` 里**。加短码时前端不同步加，这条就红。

覆盖范围：**字面量**形式的短码（``HTTPException`` / ``JSONResponse``，也就是路由与异常处理器的
全部出口）。后台阅卷失败走 ``jobs.classify_grading_error``，它返回的是同一套短码（且那些短码在
``app.py`` 的处理器里也以字面量出现），所以也被覆盖；如果将来新增一个**只**在
``classify_grading_error`` 里出现的短码，这条测试看不到它——那时把那里的返回值也纳入扫描。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API_PY = REPO / "platform_api"
FRONTEND_API = REPO / "frontend" / "src" / "api.ts"

#: ``HTTPException(..., detail="x")`` / ``content={"detail": "x"}`` 两种写法。
_DETAIL_LITERAL = re.compile(r'''detail["']?\s*[:=]\s*["']([a-z][a-z0-9_]*)["']''')


def _emitted_codes() -> set[str]:
    codes: set[str] = set()
    for path in sorted(API_PY.rglob("*.py")):
        for match in _DETAIL_LITERAL.finditer(path.read_text(encoding="utf-8")):
            codes.add(match.group(1))
    return codes


def test_every_backend_short_code_is_known_to_the_spa() -> None:
    codes = _emitted_codes()
    # 自检：正则得真的扫到东西，否则这条测试会"因为什么都没扫到"而假绿。
    assert len(codes) >= 15, f"只扫到 {sorted(codes)}——正则或目录结构变了？"

    spa = FRONTEND_API.read_text(encoding="utf-8")
    missing = sorted(code for code in codes if f"'{code}'" not in spa)
    assert not missing, (
        f"后端会发这些短码，但前端 frontend/src/api.ts 不认识：{missing}。"
        "请把每个都加进 ErrorCode 与 humanize()（说清'发生了什么 + 我该做什么'），"
        "否则学员会看到「未识别的错误：<短码>」。"
    )


def test_frontend_does_not_invent_codes_the_backend_never_sends() -> None:
    """反向也查一次：前端列了后端从不发的码，说明契约漂了（或短码改了名而前端没跟上）。"""
    codes = _emitted_codes()
    spa = FRONTEND_API.read_text(encoding="utf-8")
    # 前端自己的两条不是后端短码：网络层失败、以及非 JSON 错误体的兜底。
    own = {"network_error", "http_422"}
    declared = set(re.findall(r"^\s*\|\s*'([a-z][a-z0-9_]*)'", spa, flags=re.MULTILINE))
    assert declared, "没能从前端 ErrorCode union 里解析出任何短码"
    unknown = sorted(declared - codes - own)
    assert not unknown, f"前端声明了后端从不发的短码：{unknown}"
