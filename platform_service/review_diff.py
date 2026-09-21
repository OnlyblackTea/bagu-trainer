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
