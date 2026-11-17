# ▲ Vantage

**A live map for multi-agent software delivery — with a change‑collision radar.**

Submit an engineering request and watch a team of agents work it end‑to‑end —
**Orchestrator → Planner → Executioner → Tester → Manager/Merger** — grounded in a
**code graph + semantic index** built continuously from your repositories. Vantage's
differentiator: it shows you **who else is already changing the same code, before anyone
merges** (active branches / recent pushes overlapping your request by file, symbol, or
shared table/endpoint).

> Inspired by the DevPulse agentic spec, rebuilt as an independent, **offline‑first,
> zero‑dependency** platform. It runs locally with embedded stores and a deterministic
> mock LLM (no API credits), then scales out to real backends on the server.

## Why it's different
- **Change‑collision radar** — preflight awareness of unmerged work touching your area.
- **Learns from code, not just commits** — symbols, endpoints, DB tables, config keys are
  first‑class, extracted from source (Python AST + language‑agnostic parsing for Java/JS/Go/…).
- **Zero‑dependency local core** — pure stdlib (sqlite, http.server/SSE, ast, git). It just runs.
- **Grounded + injection‑hardened** — every decision traces to evidence; repository text is
  treated as untrusted data.
- **Never silently merges** — Manager approves‑with‑PR, requests changes, or escalates to a human.

## Quick start (local, offline)
```bash
cd ~/vantage
./run.sh            # creates venv, runs tests, serves the UI
# open http://127.0.0.1:8060
```
Then in the UI: **Ingest repo** (`/abs/path/to/a/git/repo`) → **Submit request** → watch the live map.

Run tests only:
```bash
./.venv/bin/python -m pytest -q tests/
```

## Architecture
```
              ┌────────────── live map (SSE) ──────────────┐
 request ─▶ Orchestrator ─▶ Planner ─▶ Executioner ─▶ Tester ─▶ Manager/Merger
                              │  retrieval + impact + collisions        │ approve/request/escalate
   Scheduler (every 10 min) ──┴── ingests repos → { code graph, vectors, metadata, changes }
```
| Layer | Local (default) | Server adapter |
|------|------------------|----------------|
| Vectors / semantic | embedded (sqlite + hashing embedder) | **ClickHouse** (or Qdrant) + Voyage embeddings |
| Code graph "graphlang" | embedded (JSON, GraphML export) | **Neo4j / Memgraph** |
| Metadata / runs / lessons | **SQLite** | **PostgreSQL** |
| Events / live map | in‑proc bus + SSE | Redis / Kafka |
| LLM | **MockProvider** (deterministic) | **Anthropic Claude** (+ Claude Code for Executioner) |

Swap via env vars (`VANTAGE_VECTORS`, `VANTAGE_GRAPH`, `VANTAGE_META`, `VANTAGE_LLM`). The
store/provider interfaces are stable, so backends drop in without touching agents.

## Going to server
Local base branch defaults to `env/dev`. In `VANTAGE_ENV=server`, Vantage **asks for the base
branch** on startup (or set `VANTAGE_BASE_BRANCH`) so agents target the right integration line
wherever they're deployed. Set `VANTAGE_LLM=anthropic` and `ANTHROPIC_API_KEY` for real Claude.

## Configuration (env)
`VANTAGE_ENV` local|server · `VANTAGE_PORT` (8060) · `VANTAGE_BASE_BRANCH` (env/dev) ·
`VANTAGE_SCHEDULE_S` (600) · `VANTAGE_REPOS` (colon‑sep paths) · `VANTAGE_LLM` mock|anthropic ·
`VANTAGE_MODEL` (claude-opus-4-8) · `VANTAGE_VECTORS`/`VANTAGE_GRAPH`/`VANTAGE_META`.

## Status
Phase 1 — full pipeline, ingestion, code graph, semantic retrieval, change‑collision radar,
live‑map UI, self‑learning evaluation memory. Offline, tests green. See `docs/ROADMAP.md`.

## Security
Per‑repo permissions, sandboxed execution, audited tool calls, human approval before merge,
prompt‑injection defense, and secrets only from env — never committed.
