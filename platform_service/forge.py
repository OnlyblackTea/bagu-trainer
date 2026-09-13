"""Forge 缝（A′）：fork/clone 与 PR 的抽象。

学员驱动本地 git，平台只 gate + 指示（设计 spec §C.5）：provision 把 track-repo 拉到本地
工作区（占位 = 本地 clone；未来 = Gitea fork），diff 用 :mod:`platform_service.review_diff`
跑真本地 git，PR 是占位 URL（未来 = Gitea PR）。平台**不替学员跑 git 写操作**。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import TicketInstance


@dataclass(frozen=True)
class Workspace:
    """一个实例的本地工作区。``base_ref``/``head_ref`` 是 diff 的两端（占位用 origin/* 分支
    模拟"骨架 → 学员解法"）；``simulated`` 供 UI 标"模拟"。"""

    path: Path
    branch: str
    base_ref: str
    head_ref: str
    simulated: bool = True


@runtime_checkable
class ForgeClient(Protocol):
    def workspace_path(self, instance: TicketInstance) -> Path: ...

    def provision(self, instance: TicketInstance) -> Workspace: ...

    def create_pr(self, instance: TicketInstance, *, base: str, head: str) -> str: ...
