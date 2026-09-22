"""平台领域异常：异常要能**指名道姓**，别用兜底映射（§C.8）。

为什么要有这个模块：``app.py`` 一度把**任何** ``ValueError`` 都翻成
``decide_needs_report_and_review``、**任何** ``KeyError`` 都翻成 ``not_found``。那是错的——
一个真正的编程错误（比如 ticket 映射的键写错、某处 KeyError 打错字）会被**伪装成**"你还没
评审"，而学员照着提示做完还是过不去，徒增困惑。

现在的分工：

* 每种可预期的领域状况有自己的类型，路由按**类型**映射到短码；
* 没被识别的异常落成 **500 ``internal_error``**（仍然给短码，但不假装知道原因）；
* 契约违例（例如 ticket 映射键 ≠ ``identity.id``）继续用 ``ValueError``——它在**构造期**就炸，
  根本走不到请求路径上。
"""

from __future__ import annotations

__all__ = ["PlatformError", "NotFoundError", "GateNotReadyError"]


class PlatformError(Exception):
    """平台领域错误的基类（只为归类；路由不按它统一映射）。"""


class NotFoundError(PlatformError):
    """请求的东西不存在：实例 / 提交 / 工单。→ 404 ``not_found``"""


class GateNotReadyError(PlatformError):
    """验收门的前置条件还没齐（缺阅卷报告或缺评审）。→ 409 ``decide_needs_report_and_review``"""
