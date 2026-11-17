"""LLM provider abstraction.

- MockProvider: deterministic, offline, zero-credit. Powers all tests and local demos.
- AnthropicProvider: real Claude via the Anthropic SDK, with prompt caching on the system
  prompt. Auto-detected only when the SDK + ANTHROPIC_API_KEY are present.

Prompt-injection defense: repository content (code, README, issues, commit messages) is
UNTRUSTED. Callers wrap it via untrusted() and it is delivered inside a fenced user block;
the system prompt instructs the model to never follow instructions found in that data.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass

UNTRUSTED_OPEN = "<<<UNTRUSTED_REPOSITORY_DATA>>>"
UNTRUSTED_CLOSE = "<<<END_UNTRUSTED_REPOSITORY_DATA>>>"

INJECTION_MARKERS = re.compile(
    r"(ignore (all|previous)|disregard.*instruction|system prompt|you are now|reveal.*(key|secret)|exfiltrat)",
    re.IGNORECASE,
)


def untrusted(text: str) -> str:
    """Fence repository-derived content so it is treated strictly as data."""
    text = text.replace(UNTRUSTED_OPEN, "").replace(UNTRUSTED_CLOSE, "")
    return f"{UNTRUSTED_OPEN}\n{text}\n{UNTRUSTED_CLOSE}"


def scan_injection(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in INJECTION_MARKERS.finditer(text or "")})


def _extract_json(text: str) -> str:
    """Strip ```json fences / prose and return the first JSON object found."""
    t = text.strip()
    if "```" in t:
        t = re.sub(r"```(?:json)?", "", t).strip()
    start, end = t.find("{"), t.rfind("}")
    return t[start:end + 1] if start != -1 and end > start else t


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0


class LLMProvider:
    name = "base"

    def complete(self, system: str, user: str, *, max_tokens: int = 1024,
                 json_mode: bool = False) -> LLMResult:  # pragma: no cover - interface
        raise NotImplementedError


class MockProvider(LLMProvider):
    """Deterministic offline provider.

    Returns structured, plausible outputs keyed off simple heuristics so the whole
    workflow is exercisable (and testable) without network or credits.
    """
    name = "mock"

    def __init__(self, model: str = "mock-1") -> None:
        self.model = model

    def complete(self, system: str, user: str, *, max_tokens: int = 1024,
                 json_mode: bool = False) -> LLMResult:
        seed = hashlib.sha256((system + "\x00" + user).encode()).hexdigest()
        if json_mode:
            # Heuristic mini-plan so downstream JSON consumers get valid, varied output.
            low = user.lower()
            kind = ("bug" if "fix" in low or "bug" in low else
                    "refactor" if "refactor" in low else "feature")
            payload = {
                "kind": kind,
                "summary": f"Mock analysis for request ({kind}).",
                "steps": [
                    {"order": 1, "action": "locate impacted symbols", "rationale": "retrieval hit"},
                    {"order": 2, "action": f"apply {kind} change", "rationale": "core edit"},
                    {"order": 3, "action": "add/adjust tests", "rationale": "validate"},
                ],
                "risk": "medium" if kind != "refactor" else "low",
                "seed": seed[:8],
            }
            return LLMResult(json.dumps(payload), self.name, self.model, len(user) // 4, 64)
        return LLMResult(
            f"[mock:{seed[:6]}] {user[:160].strip()}",
            self.name, self.model, len(user) // 4, 32,
        )


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str) -> None:
        import anthropic  # noqa: F401  (only imported when selected)
        self._client = anthropic.Anthropic()
        self.model = model

    def complete(self, system: str, user: str, *, max_tokens: int = 1024,
                 json_mode: bool = False) -> LLMResult:
        sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if json_mode:
            user = user + "\n\nRespond with a single valid JSON object and nothing else."
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=sys_blocks,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        if json_mode:
            text = _extract_json(text)
        u = getattr(msg, "usage", None)
        return LLMResult(text, self.name, self.model,
                         getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0)


def get_provider(kind: str = "mock", model: str = "claude-opus-4-8") -> LLMProvider:
    if kind == "anthropic":
        try:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            return AnthropicProvider(model)
        except Exception as e:  # graceful offline fallback
            print(f"[vantage] anthropic provider unavailable ({e}); using mock")
    return MockProvider()
