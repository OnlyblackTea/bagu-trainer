"""Unit tests for platform_service.review_diff (design v0.5 §11.2).

The volume preflight mirrors the whole-file truncation rule (REVIEW_DIFF_CHAR_LIMIT) so
the platform can detect the trap where a first-file overflow yields an empty diff but
a normal-looking review — the platform must preflight the volume itself.
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
    """The dangerous case: even the first file alone exceeds the limit, so the backend
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


def test_default_limit_matches_review_budget() -> None:
    assert review_diff.REVIEW_DIFF_CHAR_LIMIT == 120_000


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


def test_drop_baseline_files_removes_only_files_identical_to_the_baseline(
    tmp_path: Path,
) -> None:
    """链式 Track 结构下"本阶段的验收测试"会跟着 merge 进工作区。

    它不是学员写的东西，却会出现在"相对你开始这一阶段"的 diff 里；``src/test/`` 又是判定面，
    留着会被判成"改了判据"。这条钉住：**内容相同的整块剔掉，内容不同的留下**。
    """
    repo = _repo_with_one_change(tmp_path)  # HEAD = "new"，工作区也是 "new"
    baseline = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "mine.py").write_text("mine\n", encoding="utf-8")  # 新文件（未跟踪）

    # 相对 HEAD~1 的 diff 里两块都有：foo.py（HEAD 带来的）+ mine.py（我新写的）
    diff = review_diff.generate_diff(repo, base="HEAD~1")
    assert "foo.py" in diff
    pruned = review_diff.drop_baseline_files(repo, diff, baseline=baseline)
    assert "foo.py" not in pruned  # 与基线逐字节相同 ⇒ 不是我写的
    assert "foo.py" in review_diff.generate_diff(repo, base="HEAD~1")  # 没被改坏

    # 真改了 foo.py（内容与基线不同）⇒ 必须留下，否则"改了判据"就看不见了
    (repo / "foo.py").write_text("new + tampered\n", encoding="utf-8")
    tampered = review_diff.drop_baseline_files(
        repo, review_diff.generate_diff(repo, base="HEAD~1"), baseline=baseline
    )
    assert "foo.py" in tampered


def test_changed_files_drops_baseline_files_but_keeps_new_ones(tmp_path: Path) -> None:
    """``changed_files(base=..., baseline=...)``：剔掉基线自带的那份，留下学员真写的。"""
    repo = _repo_with_one_change(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "mine.py").write_text("new file\n", encoding="utf-8")

    assert review_diff.changed_files(repo, base="HEAD~1") == ["foo.py", "mine.py"]
    assert review_diff.changed_files(repo, base="HEAD~1", baseline=baseline) == ["mine.py"]


@pytest.mark.parametrize(
    ("key", "value"),
    [("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true")],
)
def test_generate_diff_forces_default_prefix_despite_config(
    tmp_path: Path, key: str, value: str
) -> None:
    """Iron law: git's default a/ b/ prefixes are required; a learner's global
    ``diff.noprefix`` / ``diff.mnemonicPrefix`` must not leak in and introduce
    anchor ambiguity (noprefix drops the side, mnemonicPrefix uses c/ c/)."""
    repo = _repo_with_one_change(tmp_path)
    _git(repo, "config", key, value)
    diff = review_diff.generate_diff(repo, base="HEAD~1", head="HEAD")
    assert "diff --git a/foo.py b/foo.py" in diff
    assert "--- a/foo.py" in diff
    assert "\n--- foo.py" not in diff
    assert "c/foo.py" not in diff
