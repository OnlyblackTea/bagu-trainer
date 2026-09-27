"""Diff preparation for the advisory AI review (design v0.5 §11.2).

The diff has to fit whatever the reviewer accepts, so its volume is accounted for
**before** any backend is called:

* A diff over :data:`REVIEW_DIFF_CHAR_LIMIT` is truncated **by whole file** — never half
  a file — and the dropped files are reported in ``truncated_files``, the platform's only
  incompleteness signal.
* If the **first** file alone exceeds the limit, the kept diff is empty yet the review
  can still return a normal-looking result. The platform must therefore preflight the
  volume *itself* rather than trust a non-empty response.

:func:`preflight_diff_volume` does that whole-file accounting offline so the caller can
refuse / split / warn before submitting anything for review.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# The whole-file truncation threshold for a review diff (design v0.5 §11.2).
REVIEW_DIFF_CHAR_LIMIT = 120_000

_GIT_DIFF_HEADER = "diff --git "


@dataclass(frozen=True)
class DiffVolume:
    """Result of preflighting a unified diff against the review char limit."""

    total_chars: int
    fits: bool
    first_file_overflows: bool
    truncated_files: tuple[str, ...]


def _strip_side_prefix(path: str) -> str:
    """Drop git's default ``a/`` / ``b/`` side prefix from a diff path."""
    for prefix in ("b/", "a/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def _path_of(lines: list[str]) -> str:
    """Recover the changed file's path from one file's diff lines.

    Prefers the ``+++`` side (the post-image), falling back to ``---`` for a
    deletion (``+++ /dev/null``). Paths are read verbatim — git's default prefix
    is assumed (never ``--src-prefix``/``--dst-prefix``, design §11.2).
    """
    for line in lines:
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target != "/dev/null":
                return _strip_side_prefix(target)
    for line in lines:
        if line.startswith("--- "):
            return _strip_side_prefix(line[4:].strip())
    return ""


def _file_chunks(diff: str) -> list[tuple[str, str]]:
    """Split a unified diff into ``(path, chunk_text)`` per changed file.

    Each chunk starts at a ``diff --git`` header and runs up to the next one, so a
    chunk is always a whole file — the unit the reviewer truncates by.
    """
    chunks: list[tuple[str, str]] = []
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith(_GIT_DIFF_HEADER):
            if current:
                chunks.append((_path_of(current), "".join(current)))
            current = [line]
        elif current:
            current.append(line)
    if current:
        chunks.append((_path_of(current), "".join(current)))
    return chunks


def preflight_diff_volume(diff: str, *, limit: int = REVIEW_DIFF_CHAR_LIMIT) -> DiffVolume:
    """Account for how a review diff is whole-file-truncated at ``limit``.

    Keeps a leading run of whole files whose cumulative size stays within ``limit``
    and marks the suffix as ``truncated_files``. ``first_file_overflows`` is the trap
    flag: when even the first file exceeds ``limit`` nothing is kept, yet the backend
    still answers with a normal-looking (but content-free) review.
    """
    chunks = _file_chunks(diff)
    truncated: list[str] = []
    kept = 0
    truncating = False
    for path, text in chunks:
        size = len(text)
        if not truncating and kept + size <= limit:
            kept += size
        else:
            truncating = True
            truncated.append(path)
    return DiffVolume(
        total_chars=len(diff),
        fits=len(diff) <= limit,
        first_file_overflows=bool(chunks) and len(chunks[0][1]) > limit,
        truncated_files=tuple(truncated),
    )


def generate_diff(repo: Path, *, base: str, head: str | None = None) -> str:
    """Return ``git diff base [head]`` for ``repo`` with git's DEFAULT ``a/`` ``b/`` prefix.

    Iron law (design §11.2): never pass ``--src-prefix``/``--dst-prefix`` (nor
    ``--no-prefix``) — noprefix introduces unresolvable anchor ambiguity for anchors.
    The ``-c`` overrides *restore* the canonical prefix rather than supply a custom one,
    so a learner's global ``diff.noprefix`` / ``diff.mnemonicPrefix`` (which would emit
    no side prefix or ``c/ c/``) cannot leak into the diff the reviewer parses. With
    ``head=None`` the diff is ``base`` against the working tree.
    """
    cmd = [
        "git",
        "-c",
        "diff.noprefix=false",
        "-c",
        "diff.mnemonicPrefix=false",
        "diff",
        base,
    ]
    if head is not None:
        cmd.append(head)
    result = subprocess.run(
        cmd,
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _same_as(repo: Path, ref: str, path: str) -> bool:
    """工作区里的 ``path`` 与 ``ref`` 里的那一份是否**逐字节相同**（不存在于工作区 ⇒ False）。

    用 :func:`git hash-object` 而不是读文件内容：大文件不该为了比一比就读进内存，而 hash 正是
    git 自己判断"这份内容有没有变"的方式。
    """
    worktree = subprocess.run(
        ["git", "hash-object", "--", path],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if worktree.returncode != 0:
        return False  # 工作区里没有这个文件（被删了）⇒ 那是个真实的改动，不能剔
    in_ref = subprocess.run(
        ["git", "rev-parse", f"{ref}:{path}"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if in_ref.returncode != 0:
        return False
    return worktree.stdout.strip() == in_ref.stdout.strip()


def changed_files(repo: Path, *, base: str, baseline: str | None = None) -> list[str]:
    """相对 ``base`` 改过的文件（正斜杠、排序、去重），**含未跟踪的新文件**。

    未跟踪也要算：学员新加一个文件却忘了 ``git add`` 是很正常的事，而 maven 照样会编译它——
    "他改了什么"里漏掉它，报告就在说假话。用 ``git ls-files --others --exclude-standard`` 拿，
    它天生就把忽略的文件排除在外。

    给了 ``baseline`` 就把"与它**逐字节相同**"的剔掉——那正是"基线自带、不是我写的"那些文件
    （见 :func:`drop_baseline_files` 里的来龙去脉）。没给就是原样的 ``git diff --name-only``。
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", base],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return []
    paths = {
        line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()
    }
    others = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if others.returncode == 0:
        paths |= {
            line.strip().replace("\\", "/") for line in others.stdout.splitlines() if line.strip()
        }
    if baseline is not None:
        paths = {p for p in paths if not _same_as(repo, baseline, p)}
    return sorted(paths)


def drop_baseline_files(repo: Path, diff: str, *, baseline: str) -> str:
    """把 ``diff`` 里"与基线逐字节相同"的文件整块剔掉（返回新的 diff 文本）。

    只对**内容相同**的文件生效：学员真的改了它，内容就不一样，这一块会留下（判定面照样看得见）。
    路径来自 diff 自己的 ``+++`` 头，所以不需要调用方再传一份文件列表。
    """
    keep: list[str] = []
    for path, chunk in _file_chunks(diff):
        if path and _same_as(repo, baseline, path):
            continue
        keep.append(chunk)
    return "".join(keep)
