"""Base agent — owns live-map node transitions, structured messaging and event emit."""
from __future__ import annotations

import time

from ..events import Event
from ..state import AgentMessage, AgentName, NodeStatus, WorkflowState


class Agent:
    name: AgentName

    def __init__(self, platform) -> None:
        self.platform = platform

    # ---- lifecycle helpers ----
    def start(self, state: WorkflowState, action: str) -> None:
        n = state.node(self.name)
        n.status = NodeStatus.RUNNING
        n.started_at = time.time()
        n.action = action
        self._emit_node(state)

    def finish(self, state: WorkflowState, summary: str, status: NodeStatus = NodeStatus.COMPLETED) -> None:
        n = state.node(self.name)
        n.status = status
        n.ended_at = time.time()
        n.summary = summary
        n.evidence_count = len(state.evidence)
        self._emit_node(state)

    def fail(self, state: WorkflowState, summary: str) -> None:
        n = state.node(self.name)
        n.retries += 1
        self.finish(state, summary, NodeStatus.FAILED)

    def wait_human(self, state: WorkflowState, summary: str) -> None:
        self.finish(state, summary, NodeStatus.WAITING)

    # ---- comms ----
    def message(self, state: WorkflowState, to: AgentName, intent: str, payload: dict | None = None) -> None:
        msg = AgentMessage(request_id=state.request_id, frm=self.name, to=to,
                           intent=intent, payload=payload or {})
        state.messages.append(msg.as_dict())
        self.platform.bus.publish(Event(type="message", request_id=state.request_id, data=msg.as_dict()))

    def _emit_node(self, state: WorkflowState) -> None:
        n = state.node(self.name)
        self.platform.bus.publish(Event(
            type="node_update", request_id=state.request_id,
            data={"agent": self.name.value, "status": n.status.value, "action": n.action,
                  "summary": n.summary, "evidence_count": n.evidence_count, "retries": n.retries},
        ))

    def run(self, state: WorkflowState) -> None:  # pragma: no cover - interface
        raise NotImplementedError
