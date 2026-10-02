"""Incident summarizer providers.

Every provider implements ``summarize(ctx: IncidentContext) -> str`` (markdown).
Select with ``RELIABILITY_SUMMARIZER`` = ``rule_based`` (default) | ``bedrock`` | ``azure_openai``.
"""

from __future__ import annotations

import os
from typing import Protocol

from reliability.exceptions import SummarizerError
from reliability.summarizer.context import IncidentContext, build_context
from reliability.summarizer.llm import AzureOpenAISummarizer, BedrockSummarizer, build_prompt
from reliability.summarizer.rule_based import RuleBasedSummarizer


class Summarizer(Protocol):
    name: str

    def summarize(self, ctx: IncidentContext) -> str: ...


_PROVIDERS: dict[str, type] = {
    "rule_based": RuleBasedSummarizer,
    "bedrock": BedrockSummarizer,
    "azure_openai": AzureOpenAISummarizer,
}


def get_summarizer(provider: str | None = None) -> Summarizer:
    """Construct a provider (LLM clients are built lazily, on first use)."""
    key = (provider or os.environ.get("RELIABILITY_SUMMARIZER", "rule_based")).lower()
    if key not in _PROVIDERS:
        raise SummarizerError(f"unknown summarizer {key!r}; choose from {sorted(_PROVIDERS)}")
    return _PROVIDERS[key]()


__all__ = [
    "AzureOpenAISummarizer", "BedrockSummarizer", "IncidentContext", "RuleBasedSummarizer",
    "Summarizer", "build_context", "build_prompt", "get_summarizer",
]
