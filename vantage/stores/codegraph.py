"""Structural software graph ("graphlang").

A typed property graph of the codebase — the structural counterpart to the semantic
vector layer. Embedded pure-Python + JSON persistence locally; exportable to GraphML and
mappable 1:1 onto Neo4j/Memgraph on server (nodes = labels, edges = relationships).

Node types:  repo, file, symbol, endpoint, table, config, test, commit, change
Edge types:  contains, defines, calls, uses, implemented_by, validates, changes,
             conflicts_with, owns
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


class CodeGraph:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.nodes: dict[str, dict] = {}
        self._out: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)  # nid -> [(etype, dst, attrs)]
        self._in: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)   # nid -> [(etype, src, attrs)]
        if self.path and self.path.exists():
            self.load()

    # ---- mutation ----
    def add_node(self, nid: str, ntype: str, **attrs) -> str:
        cur = self.nodes.get(nid, {})
        cur.update({"id": nid, "type": ntype, **attrs})
        self.nodes[nid] = cur
        return nid

    def add_edge(self, src: str, etype: str, dst: str, **attrs) -> None:
        if (etype, dst) not in [(e, d) for e, d, _ in self._out[src]]:
            self._out[src].append((etype, dst, attrs))
            self._in[dst].append((etype, src, attrs))

    # ---- queries ----
    def neighbors(self, nid: str, etype: str | None = None) -> list[str]:
        return [d for e, d, _ in self._out.get(nid, []) if etype is None or e == etype]

    def callers(self, nid: str) -> list[str]:
        return [s for e, s, _ in self._in.get(nid, []) if e == "calls"]

    def impacted_by(self, nid: str, depth: int = 2) -> set[str]:
        """Symbols/files reachable via reverse call/defines edges — the blast radius."""
        seen: set[str] = set()
        frontier = [nid]
        for _ in range(depth):
            nxt = []
            for n in frontier:
                for e, s, _ in self._in.get(n, []):
                    if e in ("calls", "defines", "implemented_by", "uses") and s not in seen:
                        seen.add(s)
                        nxt.append(s)
            frontier = nxt
        return seen

    def find(self, ntype: str) -> list[dict]:
        return [n for n in self.nodes.values() if n["type"] == ntype]

    def stats(self) -> dict:
        by_type: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            by_type[n["type"]] += 1
        edges = sum(len(v) for v in self._out.values())
        return {"nodes": len(self.nodes), "edges": edges, "by_type": dict(by_type)}

    def remove_repo_branch(self, repo: str, branch: str) -> None:
        """Drop structural nodes/edges for a repo+branch before re-ingest (idempotency)."""
        drop = {nid for nid, n in self.nodes.items()
                if n.get("repo") == repo and n.get("branch") == branch
                and n["type"] in ("file", "symbol", "endpoint", "table", "config", "test")}
        for nid in drop:
            self.nodes.pop(nid, None)
            self._out.pop(nid, None)
            self._in.pop(nid, None)
        for m in (self._out, self._in):
            for k in list(m.keys()):
                m[k] = [t for t in m[k] if t[1] not in drop]

    # ---- persistence ----
    def to_dict(self) -> dict:
        edges = [{"src": s, "type": e, "dst": d, "attrs": a}
                 for s, lst in self._out.items() for e, d, a in lst]
        return {"nodes": list(self.nodes.values()), "edges": edges}

    def save(self) -> None:
        if not self.path:
            return
        self.path.write_text(json.dumps(self.to_dict()))

    def load(self) -> None:
        data = json.loads(self.path.read_text())
        for n in data.get("nodes", []):
            self.nodes[n["id"]] = n
        for e in data.get("edges", []):
            self.add_edge(e["src"], e["type"], e["dst"], **e.get("attrs", {}))

    def to_graphml(self) -> str:
        """Minimal GraphML export for external viewers / Neo4j import tooling."""
        out = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
               '<key id="type" for="node" attr.name="type" attr.type="string"/>',
               '<key id="etype" for="edge" attr.name="etype" attr.type="string"/>',
               '<graph edgedefault="directed">']
        for nid, n in self.nodes.items():
            out.append(f'<node id="{_x(nid)}"><data key="type">{_x(n["type"])}</data></node>')
        i = 0
        for s, lst in self._out.items():
            for e, d, _ in lst:
                out.append(f'<edge id="e{i}" source="{_x(s)}" target="{_x(d)}">'
                           f'<data key="etype">{_x(e)}</data></edge>')
                i += 1
        out.append("</graph></graphml>")
        return "\n".join(out)


def _x(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))
