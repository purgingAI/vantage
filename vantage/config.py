"""Vantage configuration.

Local-first: everything defaults to embedded stores and the offline mock LLM so the
platform runs with zero external services and zero API credits. Point at real backends
(Anthropic, ClickHouse, Neo4j, Postgres) via environment variables when you go to server.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path


def _load_dotenvish() -> None:
    """Best-effort load of ANTHROPIC_API_KEY from the user's known .env files.

    We never commit or print secrets; this only populates os.environ if not already set.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    for candidate in (
        Path.home() / "vantage" / ".env",
        Path.home() / "creator-os" / ".env",
        Path.home() / "trading-ai" / ".env",
    ):
        try:
            if not candidate.exists():
                continue
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY=") and "=" in line:
                    os.environ.setdefault("ANTHROPIC_API_KEY", line.split("=", 1)[1].strip().strip('"'))
                    return
        except Exception:
            continue


@dataclass
class Settings:
    # env = "local" (offline, embedded) | "server" (prompt for base branch, real backends)
    env: str = field(default_factory=lambda: os.environ.get("VANTAGE_ENV", "local"))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("VANTAGE_DATA", str(Path.home() / "vantage" / "data"))))
    host: str = field(default_factory=lambda: os.environ.get("VANTAGE_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("VANTAGE_PORT", "8060")))

    # Base branch. Local defaults to env/dev; on server we require it to be chosen explicitly.
    base_branch: str = field(default_factory=lambda: os.environ.get("VANTAGE_BASE_BRANCH", "env/dev"))

    # Ingestion cadence (seconds). Spec target: every 10 minutes.
    schedule_interval_s: int = field(default_factory=lambda: int(os.environ.get("VANTAGE_SCHEDULE_S", "600")))

    # Cap files processed per repo (0 = unlimited). Keeps memory bounded when mapping many
    # large repos on a modest machine.
    max_files: int = field(default_factory=lambda: int(os.environ.get("VANTAGE_MAX_FILES", "0")))

    # Repositories to ingest (colon-separated absolute paths). Empty = none until added via API.
    repos: list[str] = field(default_factory=lambda: [p for p in os.environ.get("VANTAGE_REPOS", "").split(":") if p])

    # LLM: "mock" (offline, deterministic) | "anthropic"
    llm_provider: str = field(default_factory=lambda: os.environ.get("VANTAGE_LLM", "mock"))
    model: str = field(default_factory=lambda: os.environ.get("VANTAGE_MODEL", "claude-opus-4-8"))

    # Storage backends (local embedded by default).
    vector_backend: str = field(default_factory=lambda: os.environ.get("VANTAGE_VECTORS", "embedded"))   # embedded|clickhouse
    graph_backend: str = field(default_factory=lambda: os.environ.get("VANTAGE_GRAPH", "embedded"))       # embedded|neo4j
    meta_backend: str = field(default_factory=lambda: os.environ.get("VANTAGE_META", "sqlite"))           # sqlite|postgres

    @property
    def is_server(self) -> bool:
        return self.env == "server"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def redacted(self) -> dict:
        d = asdict(self)
        d["data_dir"] = str(self.data_dir)
        d["anthropic_key_present"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
        return d


_load_dotenvish()
settings = Settings()
settings.ensure_dirs()
