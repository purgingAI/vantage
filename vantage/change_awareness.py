"""Change-awareness: 'someone else is already changing this'.

Given the components a request will touch, surface active/unmerged work (other branches,
recent pushes) that overlaps by file, symbol, or shared table/endpoint — so a developer
sees incoming changes BEFORE anyone merges. This is Vantage's core differentiator.
"""
from __future__ import annotations

from .state import ActiveChange, ImpactedComponent


def detect(platform, repo: str, impacted: list[ImpactedComponent], base_branch: str) -> list[ActiveChange]:
    changes = platform.meta.active_change_files(repo, exclude_branch=base_branch)
    if not changes:
        return []

    impacted_paths = {c.path for c in impacted if c.path}
    impacted_syms = {c.symbol for c in impacted if c.symbol}
    # tables used by impacted files, via the graph
    impacted_tables: set[str] = set()
    for c in impacted:
        fid = f"file:{repo}:{c.path}"
        for t in platform.graph.neighbors(fid, "uses"):
            if t.startswith(f"table:{repo}:"):
                impacted_tables.add(t.split(":", 2)[2])

    out: list[ActiveChange] = []
    seen: set[tuple] = set()
    for ch in changes:
        path, branch, author = ch["path"], ch["branch"], ch["author"]
        kind = detail = ""
        if path in impacted_paths:
            kind, detail = "file_overlap", f"{branch} edits the same file"
        else:
            # symbol overlap: does the changed file define a symbol we also touch?
            fid = f"file:{repo}:{path}"
            for sid in platform.graph.neighbors(fid, "defines"):
                sname = platform.graph.nodes.get(sid, {}).get("name")
                if sname and sname in impacted_syms:
                    kind, detail = "symbol_overlap", f"{branch} changes symbol {sname}"
                    break
            if not kind:
                # shared table: changed file uses a table our request touches
                for t in platform.graph.neighbors(fid, "uses"):
                    if t.startswith(f"table:{repo}:") and t.split(":", 2)[2] in impacted_tables:
                        kind, detail = "shared_table", f"{branch} shares table {t.split(':',2)[2]}"
                        break
        if not kind:
            continue
        key = (branch, path, kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(ActiveChange(repo=repo, branch=branch, author=author, path=path,
                                kind=kind, detail=detail))
    # file overlaps first (highest-confidence collisions)
    order = {"file_overlap": 0, "symbol_overlap": 1, "shared_table": 2}
    out.sort(key=lambda a: order.get(a.kind, 9))
    return out
