"""End-to-end offline tests: synthetic git repo -> ingest -> run pipeline -> assertions.

Runs fully offline with the mock LLM (zero credits, deterministic)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vantage.config import Settings                       # noqa: E402
from vantage.platform import Platform                     # noqa: E402
from vantage.ingest import ingest_repo                    # noqa: E402
from vantage.orchestration import run_workflow            # noqa: E402
from vantage.state import WorkflowState, NodeStatus       # noqa: E402
from vantage.stores.vectors import embed, cosine          # noqa: E402
from vantage.ingest.git_ingest import extract_symbols     # noqa: E402


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


@pytest.fixture()
def synthetic_repo(tmp_path):
    repo = tmp_path / "svc"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "orders.py").write_text(
        "class OrderService:\n"
        "    def create_order(self, amount):\n"
        "        return self.charge(amount)\n"
        "    def charge(self, amount):\n"
        "        return amount\n"
    )
    (repo / "app" / "api.java").write_text(
        '@RestController\nclass OrderController {\n'
        '  @PostMapping("/orders")\n  public void create() {}\n}\n'
    )
    _git(repo, "init", "-q", "-b", "env/dev")
    _git(repo, "config", "user.email", "t@t.co")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init orders service")
    # a competing unmerged branch touching the same file
    _git(repo, "checkout", "-q", "-b", "feature/discount")
    (repo / "app" / "orders.py").write_text(
        (repo / "app" / "orders.py").read_text() + "\n    def apply_discount(self, amount):\n        return amount\n"
    )
    _git(repo, "commit", "-qam", "wip discount")
    _git(repo, "checkout", "-q", "env/dev")
    return repo


@pytest.fixture()
def platform(tmp_path):
    s = Settings(env="local", data_dir=tmp_path / "data", base_branch="env/dev", llm_provider="mock")
    return Platform(settings=s)


def test_embed_is_deterministic_and_normalized():
    a, b = embed("create order service"), embed("create order service")
    assert a == b
    assert abs(cosine(a, a) - 1.0) < 1e-6
    assert cosine(embed("order payment"), embed("banana tree")) < cosine(embed("order payment"), embed("order charge"))


def test_extract_symbols_python_and_generic():
    py = extract_symbols("x.py", "class A:\n    def foo(self):\n        return bar()\n")
    assert any(s["name"] == "A" and s["kind"] == "class" for s in py["symbols"])
    assert any(s["name"] == "foo" for s in py["symbols"])
    assert "bar" in py["calls"]
    java = extract_symbols("Api.java", '@PostMapping("/orders")\nclass C { void m(){} }')
    assert "/orders" in java["endpoints"]
    assert any(s["name"] == "C" for s in java["symbols"])


def test_ingest_builds_graph_and_vectors(platform, synthetic_repo):
    stats = ingest_repo(platform, str(synthetic_repo), base_branch="env/dev")
    assert stats["symbols"] >= 3
    assert stats["endpoints"] >= 1
    g = platform.graph.stats()
    assert g["by_type"].get("symbol", 0) >= 3
    assert g["by_type"].get("endpoint", 0) >= 1
    assert platform.vectors.count() > 0
    hits = platform.vectors.search("create order", repo="svc", k=5)
    assert hits and hits[0]["repo"] == "svc"


def test_full_pipeline_completes_with_collision(platform, synthetic_repo):
    ingest_repo(platform, str(synthetic_repo), base_branch="env/dev")
    state = WorkflowState(request="Add order discount to create_order", repo="svc", base_branch="env/dev")
    run_workflow(platform, state)

    # every node ran
    for a in ("orchestrator", "planner", "executioner", "tester", "manager"):
        assert state.nodes[a].status in (NodeStatus.COMPLETED, NodeStatus.WAITING, NodeStatus.FAILED)
    # planner grounded the plan in evidence
    assert state.evidence and state.plan
    # change-awareness caught the competing feature/discount branch on the same file
    assert any(c.branch == "feature/discount" for c in state.active_changes)
    # manager escalates because of the collision (never auto-merge)
    assert state.review["decision"] in ("escalate_to_human", "request_changes")
    assert state.human_approval_required is True
    # run persisted + a lesson recorded
    assert platform.meta.runs()
    assert platform.meta.recent_lessons()


def test_failing_tests_request_changes(platform, synthetic_repo):
    ingest_repo(platform, str(synthetic_repo), base_branch="env/dev")
    state = WorkflowState(request="break the charge flow fail-test", repo="svc", base_branch="env/dev")
    run_workflow(platform, state)
    assert state.test_result["failed"] >= 1
    assert state.review["decision"] == "request_changes"
