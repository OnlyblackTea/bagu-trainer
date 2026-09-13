"""Diff preparation for the advisory AI review (design v0.4 §6.6.1 契约三).

Two engine facts drive this module (CONTRACTS §5.5/§5.6/§5.8a):

* The engine truncates a diff over :data:`ENGINE_DIFF_CHAR_LIMIT` **by whole file**
  and reports the dropped files in ``truncated_files`` — the platform's only
  incompleteness signal.
* If the **first** file alone exceeds the limit, the kept diff is empty yet the
  review still returns a normal-looking result. The platform must therefore
  preflight the volume *itself* rather than trust a non-empty response.

:func:`preflight_diff_volume` reproduces that whole-file accounting offline so the
caller can refuse / split / warn before ever calling ``submit_for_ai_review``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# The engine's whole-file truncation threshold for a review diff (CONTRACTS §5.6).
ENGINE_DIFF_CHAR_LIMIT = 120_000

_GIT_DIFF_HEADER = "diff --git "


@dataclass(frozen=True)
class DiffVolume:
    """Result of preflighting a unified diff against the engine's char limit."""

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
    is assumed (never ``--src-prefix``/``--dst-prefix``, CONTRACTS §5.5).
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
    chunk is always a whole file — the unit the engine truncates by.
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


def preflight_diff_volume(diff: str, *, limit: int = ENGINE_DIFF_CHAR_LIMIT) -> DiffVolume:
    """Account for how the engine will whole-file-truncate ``diff`` at ``limit``.

    Keeps a leading run of whole files whose cumulative size stays within ``limit``
    and marks the suffix as ``truncated_files``. ``first_file_overflows`` is the trap
    flag: when even the first file exceeds ``limit`` nothing is kept, yet the engine
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

    CONTRACTS §5.5 / 契约三 铁律: never pass ``--src-prefix``/``--dst-prefix`` (nor
    ``--no-prefix``) — noprefix introduces unresolvable anchor ambiguity in the engine.
    The ``-c`` overrides *restore* the canonical prefix rather than supply a custom one,
    so a learner's global ``diff.noprefix`` / ``diff.mnemonicPrefix`` (which would emit
    no side prefix or ``c/ c/``) cannot leak into the diff the engine parses. With
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
