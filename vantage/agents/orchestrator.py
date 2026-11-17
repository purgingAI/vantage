"""Orchestrator — owns workflow state, routing and (in the runner) retries/escalation.

Routing: if the request doesn't name a repo, pick the best-matching repo via semantic
retrieval so downstream agents work in the right place.
"""
from __future__ import annotations

from .base import Agent
from ..state import AgentName, WorkflowState


class Orchestrator(Agent):
    name = AgentName.ORCHESTRATOR

    def run(self, state: WorkflowState) -> None:
        self.start(state, "route request + prepare workspace")

        if not state.repo:
            hits = self.platform.vectors.search(state.request, k=5)
            if hits:
                # majority repo among top hits
                tally: dict[str, float] = {}
                for h in hits:
                    tally[h["repo"]] = tally.get(h["repo"], 0.0) + h["score"]
                state.repo = max(tally, key=tally.get)

        state.base_branch = state.base_branch or self.platform.settings.base_branch
        state.workspace = f"vantage/{state.repo or 'unknown'}/{state.request_id}"
        self.message(state, AgentName.PLANNER, "plan_request",
                     {"repo": state.repo, "base_branch": state.base_branch})
        self.finish(state, f"routed to repo='{state.repo or 'n/a'}' base='{state.base_branch}'")
