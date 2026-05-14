"""LiteLLM-backed client used by harness agents."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
import re
from typing import Any

from .config import AgentModelConfig


class LLMError(RuntimeError):
    """Raised when an LLM call fails or returns unusable output."""


class LLMClient:
    def __init__(self, config: AgentModelConfig):
        self.config = config

    def complete_text(
        self,
        system: str,
        user: str,
        *,
        response_format: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._completion(messages, response_format=response_format, max_tokens=max_tokens)

    def complete_multimodal(
        self,
        system: str,
        user: str,
        image_paths: list[Path] | None = None,
        *,
        response_format: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for image_path in image_paths or []:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(image_path)},
                }
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        return self._completion(messages, response_format=response_format, max_tokens=max_tokens)

    def json_text(self, system: str, user: str, *, max_tokens: int | None = None) -> dict[str, Any]:
        text = self.complete_text(system, user, response_format="json_object", max_tokens=max_tokens)
        return extract_json_object(text)

    def json_multimodal(
        self,
        system: str,
        user: str,
        image_paths: list[Path] | None = None,
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        text = self.complete_multimodal(
            system,
            user,
            image_paths=image_paths,
            response_format="json_object",
            max_tokens=max_tokens,
        )
        return extract_json_object(text)

    def _completion(
        self,
        messages: list[dict[str, Any]],
        *,
        response_format: str | None,
        max_tokens: int | None,
    ) -> str:
        try:
            from litellm import completion
        except Exception as exc:
            raise LLMError("LiteLLM is not installed. Run `pip install -r requirements.txt`.") from exc

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.api_version:
            kwargs["api_version"] = self.config.api_version
        if self.config.custom_llm_provider:
            kwargs["custom_llm_provider"] = self.config.custom_llm_provider
        if max_tokens or self.config.max_tokens:
            kwargs["max_tokens"] = max_tokens or self.config.max_tokens
        if self.config.timeout_seconds:
            kwargs["timeout"] = self.config.timeout_seconds
        if response_format == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        try:
            try:
                response = completion(**kwargs)
            except Exception:
                if "response_format" not in kwargs:
                    raise
                kwargs.pop("response_format", None)
                response = completion(**kwargs)
            content = response.choices[0].message.content
            if not isinstance(content, str) or not content.strip():
                raise LLMError(f"{self.config.name} returned an empty response.")
            return content.strip()
        except Exception as exc:
            raise LLMError(f"{self.config.name} LLM call failed: {exc}") from exc


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
        if match:
            return json.loads(match.group(1))
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise LLMError("Could not parse JSON object from LLM response.")


def extract_code_block(text: str) -> str:
    match = re.search(r"```(?:python)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _image_data_url(path: Path) -> str:
    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"
