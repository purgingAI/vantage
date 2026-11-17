"""Vantage — a live map for multi-agent software delivery.

Local-first, zero-dependency core: submit a request and watch Orchestrator -> Planner ->
Executioner -> Tester -> Manager work it, grounded in a code graph + semantic index built
from your repositories, with unmerged-change collision detection.
"""
from .platform import Platform  # noqa: F401
from .state import WorkflowState, RequestKind  # noqa: F401
from .orchestration import run_workflow  # noqa: F401

__version__ = "0.1.0"
