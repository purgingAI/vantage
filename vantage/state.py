"""Typed shared state — the contract every agent reads/writes.

Agents communicate structured artifacts (not just free text). The WorkflowState is the
single source of truth that streams to the live map. Kept as stdlib dataclasses so the
core has zero external dependencies.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class AgentName(str, Enum):
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    EXECUTIONER = "executioner"
    TESTER = "tester"
    MANAGER = "manager"          # manager / merger
    SCHEDULER = "scheduler"      # ingestion


# The fixed pipeline the live map renders as nodes/edges.
PIPELINE: list[AgentName] = [
    AgentName.ORCHESTRATOR,
    AgentName.PLANNER,
    AgentName.EXECUTIONER,
    AgentName.TESTER,
    AgentName.MANAGER,
]


class NodeStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"          # e.g. waiting for human approval


class RequestKind(str, Enum):
    FEATURE = "feature"
    BUG = "bug"
    REFACTOR = "refactor"
    QUESTION = "question"


class Decision(str, Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    ESCALATE = "escalate_to_human"


def _now() -> float:
    return time.time()


@dataclass
class Evidence:
    """A traceable pointer to source truth backing an agent claim."""
    kind: str                    # symbol|file|commit|test|endpoint|change|doc
    ref: str                     # e.g. "repo:path#symbol" or a commit sha
    summary: str = ""
    score: float = 0.0


@dataclass
class ImpactedComponent:
    repo: str
    path: str
    symbol: str = ""
    reason: str = ""


@dataclass
class ActiveChange:
    """Unmerged work that may collide with the request (change-awareness)."""
    repo: str
    branch: str
    author: str
    path: str
    symbol: str = ""
    kind: str = "file_overlap"   # file_overlap|symbol_overlap|shared_api|shared_table
    detail: str = ""


@dataclass
class PlanStep:
    order: int
    action: str
    target: str = ""             # repo:path[#symbol]
    rationale: str = ""


@dataclass
class NodeState:
    agent: AgentName
    status: NodeStatus = NodeStatus.QUEUED
    started_at: float | None = None
    ended_at: float | None = None
    action: str = ""
    summary: str = ""
    evidence_count: int = 0
    retries: int = 0


@dataclass
class WorkflowState:
    request: str
    kind: RequestKind = RequestKind.FEATURE
    repo: str = ""
    base_branch: str = "env/dev"
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    workspace: str = ""
    created_at: float = field(default_factory=_now)
    status: NodeStatus = NodeStatus.QUEUED

    # agent artifacts
    plan: list[PlanStep] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    impacted: list[ImpactedComponent] = field(default_factory=list)
    active_changes: list[ActiveChange] = field(default_factory=list)
    implementation: dict[str, Any] = field(default_factory=dict)
    test_result: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    risk: str = "unknown"
    human_approval_required: bool = False

    # live-map node states, keyed by agent
    nodes: dict[str, NodeState] = field(default_factory=lambda: {a.value: NodeState(agent=a) for a in PIPELINE})
    messages: list[dict] = field(default_factory=list)   # structured inter-agent messages

    def node(self, agent: AgentName) -> NodeState:
        return self.nodes[agent.value]

    def to_public(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["status"] = self.status.value
        for k, n in d["nodes"].items():
            n["agent"] = n["agent"].value if isinstance(n["agent"], AgentName) else n["agent"]
            n["status"] = n["status"].value if isinstance(n["status"], NodeStatus) else n["status"]
        return d


@dataclass
class AgentMessage:
    """Structured message on the bus between agents."""
    request_id: str
    frm: AgentName
    to: AgentName
    intent: str
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=_now)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["frm"] = self.frm.value
        d["to"] = self.to.value
        return d
