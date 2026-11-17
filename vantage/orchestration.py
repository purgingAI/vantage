"""Workflow runner — drives the fixed agent pipeline and persists the run.

Orchestrator -> Planner -> Executioner -> Tester -> Manager. The Orchestrator owns
retries/escalation; here we keep the sequencing, terminal status and durable persistence.
"""
from __future__ import annotations

import time
from pathlib import Path

from .agents import Executioner, Manager, Orchestrator, Planner, Tester
from .events import Event
from .state import NodeStatus, WorkflowState


def run_workflow(platform, state: WorkflowState) -> WorkflowState:
    state.status = NodeStatus.RUNNING
    platform.bus.publish(Event(type="workflow", request_id=state.request_id,
                               data={"status": "running", "request": state.request}))
    platform.meta.save_run(state.request_id, state.request, state.repo, "running",
                           state.created_at, None, state.to_public())

    pipeline = [Orchestrator(platform), Planner(platform), Executioner(platform),
                Tester(platform), Manager(platform)]
    try:
        for agent in pipeline:
            agent.run(state)
            if state.node(agent.name).status == NodeStatus.FAILED:
                state.status = NodeStatus.FAILED
                break
        else:
            state.status = (NodeStatus.WAITING if state.human_approval_required
                            else NodeStatus.COMPLETED)
    except Exception as e:  # never leave a run dangling
        state.status = NodeStatus.FAILED
        platform.bus.publish(Event(type="log", request_id=state.request_id, data={"error": str(e)}))
    finally:
        impl = state.implementation
        if impl.get("mode") == "sandbox_worktree" and impl.get("repo_path") and impl.get("worktree"):
            from . import sandbox
            sandbox.cleanup(impl["repo_path"], Path(impl["worktree"]), impl["branch"])

    platform.meta.save_run(state.request_id, state.request, state.repo, state.status.value,
                           state.created_at, time.time(), state.to_public())
    platform.bus.publish(Event(type="workflow", request_id=state.request_id,
                               data={"status": state.status.value,
                                     "decision": state.review.get("decision"),
                                     "human_approval_required": state.human_approval_required}))
    return state
