"""Unit tests for platform_service.review_diff (design v0.4 §6.6.1 契约三).

The volume preflight mirrors the engine's whole-file truncation (CONTRACTS §5.6) so
the platform can detect the trap where a first-file overflow yields an empty diff but
a normal-looking review (§5.8a) — the platform must preflight the volume itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from platform_service import review_diff

_ONE_FILE = (
    "diff --git a/foo.py b/foo.py\n"
    "index 1234567..89abcde 100644\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)

_FILE_A = (
    "diff --git a/a.py b/a.py\n"
    "--- a/a.py\n"
    "+++ b/a.py\n"
    "@@ -1 +1 @@\n"
    "-1\n"
    "+1\n"
)

_FILE_B = (
    "diff --git a/b.py b/b.py\n"
    "--- a/b.py\n"
    "+++ b/b.py\n"
    "@@ -1 +1 @@\n"
    "-2\n"
    "+2\n"
)


def test_small_diff_fits_under_default_limit() -> None:
    vol = review_diff.preflight_diff_volume(_ONE_FILE)
    assert vol.total_chars == len(_ONE_FILE)
    assert vol.fits is True
    assert vol.first_file_overflows is False
    assert vol.truncated_files == ()


def test_over_limit_truncates_by_whole_file() -> None:
    """A diff over the limit drops whole files from the tail, never half a file."""
    diff = _FILE_A + _FILE_B
    vol = review_diff.preflight_diff_volume(diff, limit=len(_FILE_A))
    assert vol.fits is False
    assert vol.first_file_overflows is False
    assert vol.truncated_files == ("b.py",)


def test_first_file_overflow_is_flagged() -> None:
    """The dangerous case: even the first file alone exceeds the limit, so the engine
    would keep nothing and still return a normal-looking review."""
    big = (
        "diff --git a/big.py b/big.py\n"
        "--- a/big.py\n"
        "+++ b/big.py\n"
        "@@ -1 +1 @@\n"
        "-" + ("x" * 80) + "\n"
        "+" + ("x" * 80) + "\n"
    )
    vol = review_diff.preflight_diff_volume(big, limit=50)
    assert vol.fits is False
    assert vol.first_file_overflows is True
    assert vol.truncated_files == ("big.py",)


def test_default_limit_matches_engine() -> None:
    assert review_diff.ENGINE_DIFF_CHAR_LIMIT == 120_000


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return out.stdout


def _repo_with_one_change(tmp_path: Path) -> Path:
    """A throwaway repo: base commit -> edit -> head commit (HEAD~1..HEAD).

    Signing is disabled locally so these fixture commits never reach pinentry —
    the host's global ``commit.gpgsign`` is irrelevant to a synthetic diff sample.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tester@example.com")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "foo.py").write_text("old\n", encoding="utf-8")
    _git(repo, "add", "foo.py")
    _git(repo, "commit", "-qm", "base")
    (repo / "foo.py").write_text("new\n", encoding="utf-8")
    _git(repo, "add", "foo.py")
    _git(repo, "commit", "-qm", "head")
    return repo


def test_generate_diff_uses_git_default_prefix(tmp_path: Path) -> None:
    repo = _repo_with_one_change(tmp_path)
    diff = review_diff.generate_diff(repo, base="HEAD~1", head="HEAD")
    assert "diff --git a/foo.py b/foo.py" in diff
    assert "--- a/foo.py" in diff
    assert "+++ b/foo.py" in diff


@pytest.mark.parametrize(
    ("key", "value"),
    [("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true")],
)
def test_generate_diff_forces_default_prefix_despite_config(
    tmp_path: Path, key: str, value: str
) -> None:
    """契约三 铁律: the engine needs a/ b/ prefixes; a learner's global
    ``diff.noprefix`` / ``diff.mnemonicPrefix`` must not leak in and introduce
    anchor ambiguity (noprefix drops the side, mnemonicPrefix uses c/ c/)."""
    repo = _repo_with_one_change(tmp_path)
    _git(repo, "config", key, value)
    diff = review_diff.generate_diff(repo, base="HEAD~1", head="HEAD")
    assert "diff --git a/foo.py b/foo.py" in diff
    assert "--- a/foo.py" in diff
    assert "\n--- foo.py" not in diff
    assert "c/foo.py" not in diff
