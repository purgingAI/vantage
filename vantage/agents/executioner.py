"""Executioner — performs code changes in an ISOLATED git worktree/branch.

If the target repo is a real git repo on disk, it cuts a worktree from base_branch, applies
a deterministic non-breaking change and captures a REAL diff (this is where Claude Code
drives edits on server). Otherwise it emits a proposal. It never writes to base or to the
developer's working tree.
"""
from __future__ import annotations

from .base import Agent
from .. import sandbox
from ..state import AgentName, WorkflowState


class Executioner(Agent):
    name = AgentName.EXECUTIONER

    def run(self, state: WorkflowState) -> None:
        branch = f"vantage/{state.kind.value}/{state.request_id}"
        self.start(state, f"apply changes on isolated branch {branch}")

        targets = [c.path for c in state.impacted if c.path][:8]
        repo_path = next((r["path"] for r in self.platform.meta.repos() if r["name"] == state.repo), None)
        wt = None
        if repo_path:
            wt_root = self.platform.settings.data_dir / "worktrees"
            wt = sandbox.prepare_worktree(repo_path, state.base_branch, branch, wt_root)

        if wt:
            plan_actions = [s.action for s in state.plan]
            break_it = any(w in state.request.lower() for w in ("break", "regress", "fail-test"))
            changed = sandbox.apply_change(wt, state.request_id, plan_actions, targets, break_it=break_it)
            state.implementation = {
                "branch": branch, "from_base": state.base_branch, "repo_path": repo_path,
                "worktree": str(wt), "files_touched": changed,
                "diff": sandbox.diff_summary(wt), "mode": "sandbox_worktree", "sandboxed": True,
            }
            summary = f"real diff on {branch} · {len(changed)} file(s)"
        else:
            diff_summary = [f"{s.order}. {s.action} → {targets[i] if i < len(targets) else state.repo}"
                            for i, s in enumerate(state.plan)]
            state.implementation = {
                "branch": branch, "from_base": state.base_branch, "files_touched": targets or [state.repo or "n/a"],
                "diff_summary": diff_summary,
                "mode": "proposal" if self.platform.llm.name == "mock" else "claude_code", "sandboxed": True,
            }
            summary = f"proposed changes on {branch} · {len(targets)} targets"

        self.message(state, AgentName.TESTER, "validate_changes",
                     {"branch": branch, "files": len(targets), "mode": state.implementation["mode"]})
        self.finish(state, summary)
