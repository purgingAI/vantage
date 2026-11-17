"""Vantage HTTP server — zero-dependency (stdlib) API + live-map UI with SSE.

Endpoints:
  GET  /                     -> live-map UI
  GET  /api/summary          -> platform status
  POST /api/repos    {path}  -> register + ingest a repository
  POST /api/ingest           -> run one ingestion pass now
  POST /api/request  {request, repo?, base_branch?} -> start a workflow (async)
  GET  /api/state?id=        -> current workflow state
  GET  /api/runs             -> recent runs
  GET  /api/graph[.graphml]  -> code-graph stats / GraphML export
  GET  /api/stream           -> Server-Sent Events (live map)
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .platform import Platform
from .ingest import Scheduler, ingest_repo
from .orchestration import run_workflow
from .state import RequestKind, WorkflowState

UI = (Path(__file__).parent / "ui" / "index.html")


class App:
    def __init__(self, platform: Platform | None = None) -> None:
        self.platform = platform or Platform()
        self.scheduler = Scheduler(self.platform)
        self.states: dict[str, WorkflowState] = {}

    def start_request(self, request: str, repo: str = "", base_branch: str = "", kind: str = "feature") -> str:
        st = WorkflowState(request=request, repo=repo,
                           base_branch=base_branch or self.platform.settings.base_branch,
                           kind=RequestKind(kind) if kind in {k.value for k in RequestKind} else RequestKind.FEATURE)
        self.states[st.request_id] = st
        threading.Thread(target=run_workflow, args=(self.platform, st), daemon=True).start()
        return st.request_id


def _handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", 0) or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return {}

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/" or u.path == "/index.html":
                html = UI.read_text().encode() if UI.exists() else b"<h1>Vantage</h1>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            elif u.path == "/api/summary":
                self._json({**app.platform.summary(), "last_ingest": app.scheduler.last_run})
            elif u.path == "/api/runs":
                self._json({"runs": app.platform.meta.runs(), "lessons": app.platform.meta.recent_lessons()})
            elif u.path == "/api/recommendations":
                lessons = app.platform.meta.recent_lessons(20)
                runs = app.platform.meta.runs(50)
                decisions: dict = {}
                for r in runs:
                    decisions[r["status"]] = decisions.get(r["status"], 0) + 1
                self._json({"decisions": decisions, "lessons": lessons,
                            "top_recommendations": [l["recommendation"] for l in lessons[:5]]})
            elif u.path == "/api/state":
                rid = (q.get("id") or [""])[0]
                st = app.states.get(rid)
                if st:
                    self._json(st.to_public())
                else:
                    persisted = app.platform.meta.get_run_state(rid)
                    self._json(persisted if persisted else {"error": "not found"}, 200 if persisted else 404)
            elif u.path == "/api/graph":
                self._json(app.platform.graph.stats())
            elif u.path == "/api/graph.graphml":
                data = app.platform.graph.to_graphml().encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif u.path == "/api/stream":
                self._stream()
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            u = urlparse(self.path)
            b = self._body()
            if u.path == "/api/request":
                if not b.get("request"):
                    return self._json({"error": "request required"}, 400)
                rid = app.start_request(b["request"], b.get("repo", ""),
                                        b.get("base_branch", ""), b.get("kind", "feature"))
                self._json({"request_id": rid})
            elif u.path == "/api/repos":
                path = b.get("path")
                if not path:
                    return self._json({"error": "path required"}, 400)
                try:
                    self._json(ingest_repo(app.platform, path, b.get("base_branch"),
                                           max_files=app.platform.settings.max_files))
                except Exception as e:
                    self._json({"error": str(e)}, 500)
            elif u.path == "/api/ingest":
                self._json({"results": app.scheduler.run_once()})
            elif u.path == "/api/seed":
                from .demo import seed
                self._json(seed(app.platform))
            else:
                self._json({"error": "not found"}, 404)

        def _stream(self):
            sub = app.platform.bus.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                for ev in sub.stream():
                    self.wfile.write(ev.sse().encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                sub.close()

    return Handler


def serve(platform: Platform | None = None, start_scheduler: bool = True) -> None:
    app = App(platform)
    s = app.platform.settings
    if s.is_server and s.base_branch == "env/dev" and not __import__("os").environ.get("VANTAGE_BASE_BRANCH"):
        # On server, base branch must be chosen explicitly (not the local default).
        try:
            chosen = input(f"[vantage] server mode — base branch to target [{s.base_branch}]: ").strip()
            if chosen:
                s.base_branch = chosen
        except EOFError:
            pass
    import os as _os
    if not s.is_server and _os.environ.get("VANTAGE_SEED", "1") != "0" and not app.platform.meta.repos():
        from .demo import seed
        threading.Thread(target=lambda: seed(app.platform), daemon=True).start()
        print("[vantage] seeding self-contained demo (PaymentHub) in background…")
    if start_scheduler and (s.repos or app.platform.meta.repos()):
        app.scheduler.start()
    httpd = ThreadingHTTPServer((s.host, s.port), _handler(app))
    print(f"[vantage] {s.env} · base={s.base_branch} · llm={app.platform.llm.name} · "
          f"http://{s.host}:{s.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        app.scheduler.stop()
        httpd.shutdown()


if __name__ == "__main__":
    serve()
