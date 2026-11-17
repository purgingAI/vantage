"""Tests for the real-git sandbox executioner + the self-contained demo seed."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vantage.config import Settings                       # noqa: E402
from vantage.platform import Platform                     # noqa: E402
from vantage.demo import build_demo_repo, seed            # noqa: E402
from vantage.ingest import ingest_repo                    # noqa: E402
from vantage.orchestration import run_workflow            # noqa: E402
from vantage.state import WorkflowState                   # noqa: E402


@pytest.fixture()
def platform(tmp_path):
    return Platform(settings=Settings(env="local", data_dir=tmp_path / "d",
                                      base_branch="env/dev", llm_provider="mock"))


def test_build_demo_repo_has_branches(tmp_path):
    repo = build_demo_repo(tmp_path / "ph")
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--format=%(refname:short)"],
                              capture_output=True, text=True).stdout.split()
    assert "env/dev" in branches
    assert "feature/instant-discount" in branches
    assert "fix/refund-rounding" in branches


def test_seed_populates_and_detects_collisions(platform):
    result = seed(platform)
    assert result["ingest"]["symbols"] >= 5
    assert platform.meta.repos()
    # the discount request must collide with feature/instant-discount
    assert any(r["collisions"] > 0 for r in result["runs"])
    assert platform.meta.runs() and platform.meta.recent_lessons()


def test_sandbox_worktree_real_diff_and_cleanup(platform):
    repo = build_demo_repo(platform.settings.data_dir / "demo" / "paymenthub")
    ingest_repo(platform, str(repo), base_branch="env/dev")
    st = WorkflowState(request="Add instant discount to create_order", repo="paymenthub",
                       base_branch="env/dev")
    run_workflow(platform, st)

    assert st.implementation.get("mode") == "sandbox_worktree"
    assert st.implementation.get("diff")            # a real git diff --stat
    assert st.test_result["framework"] == "sandbox"
    # worktree + temp branch cleaned up
    assert not Path(st.implementation["worktree"]).exists()
    branches = subprocess.run(["git", "-C", str(repo), "branch", "--format=%(refname:short)"],
                              capture_output=True, text=True).stdout.split()
    assert st.implementation["branch"] not in branches
    # base branch untouched (working tree clean)
    porcelain = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                               capture_output=True, text=True).stdout.strip()
    assert porcelain == ""
