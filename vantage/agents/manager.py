"""Manager / Merger — reviews evidence, weighs conflicts/risk/policy, and produces a merge
recommendation. NEVER silently merges: it approves-with-PR, requests changes, or escalates
to a human. Also writes a durable evaluation record (self-learning memory).
"""
from __future__ import annotations

from .base import Agent
from ..state import AgentName, Decision, NodeStatus, WorkflowState


class Manager(Agent):
    name = AgentName.MANAGER

    def run(self, state: WorkflowState) -> None:
        self.start(state, "review evidence + merge policy")

        failed = state.test_result.get("failed", 0)
        collisions = len(state.active_changes)
        high_risk = state.risk == "high"

        if failed > 0:
            decision, reason = Decision.REQUEST_CHANGES, f"{failed} test(s) failing"
        elif collisions > 0 or high_risk:
            decision, reason = Decision.ESCALATE, (
                f"{collisions} unmerged collision(s); manual review before merge"
                if collisions else "high-risk change requires human approval")
        else:
            decision, reason = Decision.APPROVE, "tests green, no collisions, acceptable risk"

        state.human_approval_required = decision != Decision.APPROVE
        state.review = {
            "decision": decision.value,
            "reason": reason,
            "risk": state.risk,
            "collisions": collisions,
            "pr_action": "open_pr" if decision == Decision.APPROVE else "hold",
            "policy": "no auto-merge to base; PR + approval required",
        }

        # self-learning: record outcome + a lesson/recommendation for future runs
        outcome = "approved" if decision == Decision.APPROVE else decision.value
        lesson = (f"{state.kind.value} on {state.repo}: {reason}")
        rec = ("proceed to PR" if decision == Decision.APPROVE else
               "resolve failing tests before re-run" if failed else
               "coordinate with owners of overlapping branches")
        try:
            self.platform.meta.add_evaluation(state.request_id, outcome, lesson, rec)
        except Exception:
            pass

        status = NodeStatus.WAITING if state.human_approval_required else NodeStatus.COMPLETED
        self.message(state, AgentName.ORCHESTRATOR, "final_decision", state.review)
        self.finish(state, f"{decision.value} · {reason}", status)
