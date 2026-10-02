"""Optional LLM summarizer adapters (AWS Bedrock, Azure OpenAI).

Both adapters are lazy: the SDK is imported and the client is built on first
``summarize`` call, so importing or constructing them needs no SDK, credentials,
or network. They are never exercised by tests or by the default run path.

The prompt is built only from ``IncidentContext.to_prompt_dict()`` (aggregated
check metadata). Raw rows never leave the process.
"""

from __future__ import annotations

import json
import os
from typing import Any

from reliability.exceptions import SummarizerError
from reliability.summarizer.context import IncidentContext

SYSTEM_PROMPT = (
    "You are an on-call data reliability assistant. Using ONLY the JSON check metadata provided, "
    "write a concise markdown incident note with sections: What failed, Likely cause, Blast radius, "
    "Suggested next steps. Do not invent facts that are not supported by the metadata."
)


def build_prompt(ctx: IncidentContext) -> str:
    """User message for an LLM: aggregated metadata as JSON, no row values."""
    return "Check metadata:\n```json\n" + json.dumps(ctx.to_prompt_dict(), indent=2, default=str) + "\n```"


def _require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SummarizerError(f"missing environment variables: {', '.join(missing)}")
    return {n: os.environ[n] for n in names}


class BedrockSummarizer:
    """AWS Bedrock (Converse API). Env: AWS_REGION, BEDROCK_MODEL_ID; credentials via the AWS default chain."""

    name = "aws-bedrock"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            env = _require_env("AWS_REGION", "BEDROCK_MODEL_ID")
            try:
                import boto3  # type: ignore[import-not-found]
            except ImportError as exc:
                raise SummarizerError("pip install boto3 to use the Bedrock summarizer") from exc
            self._client = boto3.client("bedrock-runtime", region_name=env["AWS_REGION"])
        return self._client

    def summarize(self, ctx: IncidentContext) -> str:
        resp = self.client.converse(
            modelId=os.environ["BEDROCK_MODEL_ID"],
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": build_prompt(ctx)}]}],
            inferenceConfig={"maxTokens": 1200, "temperature": 0.1},
        )
        return resp["output"]["message"]["content"][0]["text"]


class AzureOpenAISummarizer:
    """Azure OpenAI. Env: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT."""

    name = "azure-openai"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            env = _require_env("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")
            try:
                from openai import AzureOpenAI  # type: ignore[import-not-found]
            except ImportError as exc:
                raise SummarizerError("pip install openai to use the Azure OpenAI summarizer") from exc
            self._client = AzureOpenAI(
                azure_endpoint=env["AZURE_OPENAI_ENDPOINT"], api_key=env["AZURE_OPENAI_API_KEY"],
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01"),
            )
        return self._client

    def summarize(self, ctx: IncidentContext) -> str:
        resp = self.client.chat.completions.create(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": build_prompt(ctx)}],
            temperature=0.1,
            max_tokens=1200,
        )
        return resp.choices[0].message.content
