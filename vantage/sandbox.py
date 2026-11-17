"""Sandboxed execution — real git worktrees, real diffs, real test runs.

The Executioner works in an isolated `git worktree` cut from base_branch (never touching
base or the developer's checkout). Offline it applies a deterministic, syntactically-safe
change so the pipeline produces a real diff and the Tester runs real checks; on server this
is where Claude Code drives the edits. Everything is cleaned up afterward.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


def _git(cwd: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)


def is_git_repo(path: str | Path) -> bool:
    p = Path(path)
    return p.exists() and _git(p, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def prepare_worktree(repo_path: str, base_branch: str, branch: str, root: Path) -> Path | None:
    """Create an isolated worktree on a fresh branch cut from base_branch."""
    repo = Path(repo_path)
    if not is_git_repo(repo):
        return None
    root.mkdir(parents=True, exist_ok=True)
    wt = root / branch.replace("/", "_")
    if wt.exists():
        _git(repo, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(wt, ignore_errors=True)
    # base ref: prefer the named base branch, else current HEAD
    base = base_branch if _git(repo, "rev-parse", "--verify", base_branch).returncode == 0 else "HEAD"
    r = _git(repo, "worktree", "add", "-b", branch, str(wt), base)
    if r.returncode != 0:
        return None
    return wt


def apply_change(wt: Path, request_id: str, plan_actions: list[str], impacted_files: list[str],
                 break_it: bool = False) -> list[str]:
    """Apply a deterministic change and return changed file paths.

    Prefer a syntactically-safe append to a real impacted Python file; always drop a change
    manifest so there is a concrete, reviewable diff even for non-Python targets. When
    break_it is set (request asks to break/regress), write a genuinely broken file so the
    Tester's real compile check fails — exercising the request-changes path honestly.
    """
    changed: list[str] = []
    marker = f"vantage_{request_id}"
    if break_it:
        bad = wt / ".vantage" / f"broken-{request_id}.py"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("def broken(:\n    return  # intentional syntax error\n")
        changed.append(str(bad.relative_to(wt)))
    for rel in impacted_files:
        f = wt / rel
        if f.suffix == ".py" and f.exists():
            try:
                body = ("\n\n# --- added by Vantage Executioner ---\n"
                        f"def {marker}():\n"
                        f'    """Auto-scaffolded stub for request {request_id}."""\n'
                        "    return True\n")
                f.write_text(f.read_text() + body)
                changed.append(rel)
                break
            except Exception:
                continue
    manifest = wt / ".vantage" / f"change-{request_id}.md"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        f"# Vantage change {request_id}\n\nGenerated {time.ctime()}\n\n## Plan\n"
        + "\n".join(f"- {a}" for a in plan_actions)
        + f"\n\n## Impacted\n" + "\n".join(f"- {p}" for p in impacted_files) + "\n")
    changed.append(str(manifest.relative_to(wt)))
    _git(wt, "add", "-A")
    return changed


def diff_summary(wt: Path, max_lines: int = 40) -> str:
    _git(wt, "add", "-A")
    out = _git(wt, "diff", "--staged", "--stat").stdout.strip()
    return "\n".join(out.splitlines()[:max_lines])


def run_checks(wt: Path, changed: list[str], test_cmd: str | None = None) -> dict:
    """Real validation: compile changed Python; run an optional project test command."""
    results: dict = {"compiled": [], "errors": [], "test_cmd": test_cmd or ""}
    py = [c for c in changed if c.endswith(".py")]
    for rel in py:
        r = subprocess.run(["python3", "-m", "py_compile", str(wt / rel)], capture_output=True, text=True)
        if r.returncode == 0:
            results["compiled"].append(rel)
        else:
            results["errors"].append(f"{rel}: {r.stderr.strip()[:200]}")
    if test_cmd:
        try:
            r = subprocess.run(test_cmd, shell=True, cwd=str(wt), capture_output=True, text=True, timeout=120)
            results["test_rc"] = r.returncode
            results["test_tail"] = (r.stdout + r.stderr)[-400:]
        except Exception as e:
            results["test_rc"] = -1
            results["test_tail"] = str(e)
    results["ok"] = not results["errors"] and results.get("test_rc", 0) == 0
    return results


def cleanup(repo_path: str, wt: Path | None, branch: str) -> None:
    if not wt:
        return
    repo = Path(repo_path)
    try:
        _git(repo, "worktree", "remove", "--force", str(wt))
    except Exception:
        pass
    shutil.rmtree(wt, ignore_errors=True)
    _git(repo, "branch", "-D", branch)
