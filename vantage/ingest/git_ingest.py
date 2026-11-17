"""Repository ingestion — source code is a first-class knowledge source.

Extracts symbols (classes/functions/methods), API endpoints, DB tables, config keys and
call edges from source, plus commit/branch/changed-file metadata from git. Populates the
code graph, the vector store (meaningful chunks) and the metadata store. Idempotent per
repo+branch.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import time
from pathlib import Path

CODE_EXT = {".py", ".java", ".kt", ".js", ".ts", ".tsx", ".go", ".rb", ".cs", ".dart", ".sql"}
SKIP_DIRS = {".git", "node_modules", "build", "dist", "target", ".gradle", "__pycache__", ".venv", "venv"}
MAX_FILE_BYTES = 400_000

# language-agnostic signals
RE_CLASS = re.compile(r"^\s*(?:public|private|protected|final|abstract|export|open|data|sealed|static|\s)*"
                      r"(class|interface|enum|struct|trait)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
RE_FUNC = re.compile(r"^\s*(?:public|private|protected|static|final|async|export|func|fun|def|function|\s)*"
                     r"(?:[A-Za-z_<>\[\].]+\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{)]*\)\s*(?:\{|=>|:|$)", re.M)
RE_ENDPOINT = re.compile(r"""@(?:Get|Post|Put|Delete|Patch|Request)Mapping\s*\(\s*(?:value\s*=\s*)?["']([^"']+)["']"""
                         r"""|@(?:app|router|blueprint|bp)\.(?:get|post|put|delete|patch|route)\s*\(\s*["']([^"']+)["']""")
RE_TABLE = re.compile(r"""@Table\s*\(\s*name\s*=\s*["']([^"']+)["']|CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"']?(\w+)""",
                      re.IGNORECASE)
RE_CONFIG = re.compile(r"""@Value\s*\(\s*["']\$\{([^:}"']+)|@ConfigurationProperties\s*\(\s*prefix\s*=\s*["']([^"']+)""")


def _sha(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode()).hexdigest()[:16]


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def extract_symbols(path: str, text: str) -> dict:
    """Return {symbols, endpoints, tables, configs, calls} for a source file."""
    out: dict = {"symbols": [], "endpoints": [], "tables": [], "configs": [], "calls": []}
    parsed = False
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    out["symbols"].append({"name": node.name, "kind": kind, "lineno": node.lineno,
                                           "signature": node.name})
                if isinstance(node, ast.Call):
                    fn = node.func
                    nm = getattr(fn, "id", None) or getattr(fn, "attr", None)
                    if nm:
                        out["calls"].append(nm)
            parsed = True
        except Exception:
            parsed = False   # fall through to generic symbol regex
    if not parsed:
        for m in RE_CLASS.finditer(text):
            out["symbols"].append({"name": m.group(2), "kind": m.group(1),
                                   "lineno": text[:m.start()].count("\n") + 1,
                                   "signature": m.group(0).strip()[:120]})
        for m in RE_FUNC.finditer(text):
            nm = m.group(1)
            if nm and nm not in {"if", "for", "while", "switch", "catch", "return", "new"}:
                out["symbols"].append({"name": nm, "kind": "function",
                                       "lineno": text[:m.start()].count("\n") + 1,
                                       "signature": m.group(0).strip()[:120]})
    # endpoints / tables / config keys are language-agnostic — always scan.
    for m in RE_ENDPOINT.finditer(text):
        out["endpoints"].append(m.group(1) or m.group(2))
    for m in RE_TABLE.finditer(text):
        out["tables"].append(m.group(1) or m.group(2))
    for m in RE_CONFIG.finditer(text):
        out["configs"].append(m.group(1) or m.group(2))
    return out


def _iter_code_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if Path(fn).suffix in CODE_EXT:
                fp = Path(dirpath) / fn
                try:
                    if fp.stat().st_size <= MAX_FILE_BYTES:
                        yield fp
                except OSError:
                    continue


def ingest_repo(platform, repo_path: str, base_branch: str | None = None, max_files: int = 0) -> dict:
    """Ingest one repository. Returns a stats dict and emits an ingest event.

    max_files>0 caps files processed (keeps memory bounded when mapping many large repos)."""
    root = Path(repo_path).expanduser().resolve()
    name = root.name
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or (base_branch or platform.settings.base_branch)
    head = _git(root, "rev-parse", "HEAD") or "nogit"
    platform.meta.upsert_repo(name, str(root), base_branch or platform.settings.base_branch, head)
    platform.graph.add_node(f"repo:{name}", "repo", repo=name, branch=branch)
    platform.graph.remove_repo_branch(name, branch)

    sym_rows, vec_rows = [], []
    n_files = n_sym = n_ep = n_tbl = 0
    known: dict[str, str] = {}   # symbol name -> node id (for call edges)

    for fp in _iter_code_files(root):
        if max_files and n_files >= max_files:
            break
        rel = str(fp.relative_to(root))
        try:
            text = fp.read_text(errors="ignore")
        except Exception:
            continue
        n_files += 1
        file_id = f"file:{name}:{rel}"
        platform.graph.add_node(file_id, "file", repo=name, branch=branch, path=rel)
        platform.graph.add_edge(f"repo:{name}", "contains", file_id)
        info = extract_symbols(rel, text)
        for s in info["symbols"]:
            sid = f"sym:{name}:{rel}#{s['name']}:{s['lineno']}"
            platform.graph.add_node(sid, "symbol", repo=name, branch=branch, path=rel,
                                    name=s["name"], kind=s["kind"], lineno=s["lineno"])
            platform.graph.add_edge(file_id, "defines", sid)
            known.setdefault(s["name"], sid)
            sym_rows.append({"id": sid, "path": rel, "name": s["name"], "kind": s["kind"],
                             "lineno": s["lineno"], "signature": s["signature"]})
            vec_rows.append({"id": sid, "repo": name, "kind": "symbol", "ref": sid,
                             "text": f"{s['kind']} {s['name']} in {rel}: {s['signature']}"})
            n_sym += 1
        for ep in info["endpoints"]:
            eid = f"endpoint:{name}:{ep}"
            platform.graph.add_node(eid, "endpoint", repo=name, branch=branch, route=ep)
            platform.graph.add_edge(eid, "implemented_by", file_id)
            vec_rows.append({"id": _sha(eid), "repo": name, "kind": "endpoint", "ref": eid,
                             "text": f"API endpoint {ep} in {rel}"})
            n_ep += 1
        for tb in info["tables"]:
            tid = f"table:{name}:{tb}"
            platform.graph.add_node(tid, "table", repo=name, name=tb)
            platform.graph.add_edge(file_id, "uses", tid)
            n_tbl += 1
        for cfg in info["configs"]:
            cid = f"config:{name}:{cfg}"
            platform.graph.add_node(cid, "config", repo=name, key=cfg)
            platform.graph.add_edge(file_id, "uses", cid)
        # call edges (best-effort): file's called names -> known defined symbols
        for callee in set(info["calls"]):
            if callee in known:
                for s in info["symbols"]:
                    src = f"sym:{name}:{rel}#{s['name']}:{s['lineno']}"
                    if known[callee] != src:
                        platform.graph.add_edge(src, "calls", known[callee])

    if sym_rows:
        platform.meta.replace_symbols(name, branch, sym_rows)
    if vec_rows:
        platform.vectors.upsert_many(vec_rows)

    # commits + change-awareness across branches
    n_commits = _ingest_commits(platform, root, name, branch)
    _ingest_active_changes(platform, root, name, base_branch or platform.settings.base_branch)

    platform.graph.save()
    stats = {"repo": name, "branch": branch, "head": head[:10], "files": n_files, "symbols": n_sym,
             "endpoints": n_ep, "tables": n_tbl, "commits": n_commits, "ts": time.time()}
    from ..events import Event
    platform.bus.publish(Event(type="ingest", data=stats))
    return stats


def _ingest_commits(platform, root: Path, name: str, branch: str, limit: int = 200) -> int:
    log = _git(root, "log", f"-{limit}", "--pretty=format:%H%x1f%an%x1f%at%x1f%s")
    n = 0
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, author, ts, subj = parts
        platform.meta.record_commit(sha, name, author, float(ts or 0), subj, branch)
        platform.vectors.upsert(_sha("commit", sha), name, "commit", f"commit:{sha}",
                                f"commit by {author}: {subj}")
        n += 1
    return n


def _ingest_active_changes(platform, root: Path, name: str, base_branch: str,
                           max_branches: int = 15, max_files_per_branch: int = 800) -> None:
    """Record files changed on non-base branches vs the base — the raw material for
    the Planner's change-awareness (who else is touching this area, unmerged). Bounded to
    the most-recently-updated branches so huge repos stay fast and memory-safe."""
    branches = [b.strip() for b in _git(
        root, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)",
        "refs/heads").splitlines() if b.strip()][:max_branches + 1]
    for br in branches:
        if br == base_branch or not br:
            continue
        diff = _git(root, "diff", "--name-only", f"{base_branch}...{br}")
        if not diff:
            continue
        diff = "\n".join(diff.splitlines()[:max_files_per_branch])
        author = _git(root, "log", "-1", "--pretty=format:%an", br) or "unknown"
        ts_raw = _git(root, "log", "-1", "--pretty=format:%at", br)
        ts = float(ts_raw) if ts_raw.isdigit() else time.time()
        head = _git(root, "rev-parse", br) or br
        platform.meta.record_change_files(name, br, head, author, ts,
                                          [p for p in diff.splitlines() if p.strip()])
