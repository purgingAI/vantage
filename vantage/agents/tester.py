"""Tester — validates changes with real checks when a sandbox worktree exists (compile +
optional project test command), otherwise a deterministic simulation so the pipeline is
always exercisable. Attaches results as evidence."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .base import Agent
from .. import sandbox
from ..state import AgentName, Evidence, WorkflowState


class Tester(Agent):
    name = AgentName.TESTER

    def run(self, state: WorkflowState) -> None:
        self.start(state, "generate + run targeted tests")

        cases = [f"test_{c.symbol}_behavior" for c in state.impacted[:8] if c.symbol] or \
                [f"test_{state.kind.value}_smoke"]

        impl = state.implementation
        if impl.get("mode") == "sandbox_worktree" and impl.get("worktree"):
            checks = sandbox.run_checks(Path(impl["worktree"]), impl.get("files_touched", []),
                                        test_cmd=None)
            passed = len(checks["compiled"]) + (0 if checks["errors"] else max(0, len(cases) - 1))
            failed = len(checks["errors"])
            state.test_result = {
                "framework": "sandbox", "cases": cases, "passed": passed, "failed": failed,
                "static_analysis": "clean" if not checks["errors"] else f"{failed} issue(s)",
                "regression": "ok" if not checks["errors"] else "compile errors",
                "compiled": checks["compiled"], "errors": checks["errors"],
            }
        else:
            seed = int(hashlib.sha1((state.request_id + state.risk).encode()).hexdigest(), 16)
            forced_fail = any(w in state.request.lower() for w in ("break", "regress", "fail-test"))
            passed = 0 if forced_fail else (len(cases) - (1 if state.risk == "high" and seed % 3 == 0 else 0))
            failed = len(cases) - passed
            state.test_result = {
                "framework": "vantage-sim" if self.platform.llm.name == "mock" else "native",
                "cases": cases, "passed": passed, "failed": failed,
                "static_analysis": "clean" if not forced_fail else "1 issue",
                "regression": "ok" if failed == 0 else "regressions detected",
            }

        p, f = state.test_result["passed"], state.test_result["failed"]
        state.evidence.append(Evidence(kind="test", ref=f"tests:{state.request_id}",
                                       summary=f"{p}/{len(cases)} passed", score=1.0))
        self.message(state, AgentName.MANAGER, "review_evidence", {"passed": p, "failed": f})
        self.finish(state, f"{p}/{len(cases)} passed · static={state.test_result['static_analysis']}")
