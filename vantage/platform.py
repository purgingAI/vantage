"""Platform context — wires stores, LLM and the event bus into one object the agents use."""
from __future__ import annotations

from pathlib import Path

from .config import Settings, settings as default_settings
from .events import EventBus, bus as default_bus
from .llm import get_provider, LLMProvider
from .stores import MetadataStore, VectorStore, CodeGraph


class Platform:
    def __init__(self, settings: Settings | None = None, bus: EventBus | None = None) -> None:
        self.settings = settings or default_settings
        self.settings.ensure_dirs()
        self.bus = bus or default_bus
        d: Path = self.settings.data_dir
        self.meta = MetadataStore(d / "metadata.db")
        self.vectors = VectorStore(d / "vectors.db")
        self.graph = CodeGraph(d / "codegraph.json")
        self.llm: LLMProvider = get_provider(self.settings.llm_provider, self.settings.model)

    def summary(self) -> dict:
        return {
            "env": self.settings.env,
            "base_branch": self.settings.base_branch,
            "llm": self.llm.name,
            "model": getattr(self.llm, "model", ""),
            "repos": [r["name"] for r in self.meta.repos()],
            "graph": self.graph.stats(),
            "vectors": self.vectors.count(),
        }
