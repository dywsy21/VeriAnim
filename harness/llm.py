"""LiteLLM-backed client used by harness agents."""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
import re
import signal
import socket
import threading
import time
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

    def complete_video(
        self,
        system: str,
        user: str,
        video_path: Path,
        image_paths: list[Path] | None = None,
        *,
        response_format: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        content.append(
            {
                "type": "video_url",
                "video_url": {"url": _video_data_url(video_path)},
            }
        )
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

    def json_video(
        self,
        system: str,
        user: str,
        video_path: Path,
        image_paths: list[Path] | None = None,
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        text = self.complete_video(
            system,
            user,
            video_path,
            image_paths=image_paths,
            response_format="json_object",
            max_tokens=max_tokens,
        )
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
            "num_retries": 0,
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

        if response_format == "json_object":
            if not self.config.stream:
                try:
                    return _content_from_response(_completion_with_retries(completion, kwargs), self.config.name)
                except Exception as first_exc:
                    if "response_format" in kwargs and _mentions_unsupported_parameter(first_exc, "response_format"):
                        retry_kwargs = dict(kwargs)
                        retry_kwargs.pop("response_format", None)
                        try:
                            return _content_from_response(_completion_with_retries(completion, retry_kwargs), self.config.name)
                        except Exception as retry_exc:
                            raise LLMError(f"{self.config.name} LLM JSON call failed: {retry_exc}") from first_exc
                    raise LLMError(f"{self.config.name} LLM JSON call failed: {first_exc}") from first_exc
            stream_kwargs = {**kwargs, "stream": True}
            try:
                return _content_from_stream(_completion_with_retries(completion, stream_kwargs), self.config.name)
            except Exception as first_exc:
                if _mentions_unsupported_parameter(first_exc, "stream"):
                    try:
                        return _content_from_response(_completion_with_retries(completion, kwargs), self.config.name)
                    except Exception as retry_exc:
                        raise LLMError(f"{self.config.name} LLM JSON call failed: {retry_exc}") from first_exc
                if "response_format" in kwargs and _mentions_unsupported_parameter(first_exc, "response_format"):
                    retry_kwargs = dict(kwargs)
                    retry_kwargs.pop("response_format", None)
                    retry_kwargs["stream"] = True
                    try:
                        return _content_from_stream(_completion_with_retries(completion, retry_kwargs), self.config.name)
                    except Exception as retry_exc:
                        raise LLMError(f"{self.config.name} LLM JSON call failed: {retry_exc}") from first_exc
                raise LLMError(f"{self.config.name} LLM JSON call failed: {first_exc}") from first_exc

        if not self.config.stream:
            try:
                return _content_from_response(_completion_with_retries(completion, kwargs), self.config.name)
            except Exception as exc:
                raise LLMError(f"{self.config.name} LLM call failed: {exc}") from exc

        stream_kwargs = {**kwargs, "stream": True}
        try:
            return _content_from_stream(_completion_with_retries(completion, stream_kwargs), self.config.name)
        except Exception as stream_exc:
            if _mentions_unsupported_parameter(stream_exc, "stream"):
                try:
                    return _content_from_response(_completion_with_retries(completion, kwargs), self.config.name)
                except Exception as retry_exc:
                    raise LLMError(f"{self.config.name} LLM call failed: {retry_exc}") from stream_exc
            raise LLMError(f"{self.config.name} LLM streaming call failed: {stream_exc}") from stream_exc


def _completion_with_retries(completion: Any, kwargs: dict[str, Any]) -> Any:
    delays = [0.0, 5.0, 15.0, 45.0]
    last_exc: Exception | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            return _completion_with_parameter_fallback(completion, kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_transient_api_error(exc):
                raise
    assert last_exc is not None
    raise last_exc


def _completion_with_parameter_fallback(completion: Any, kwargs: dict[str, Any]) -> Any:
    attempts = _llm_attempt_count(kwargs)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return _completion_once_with_parameter_fallback(completion, kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts - 1 or not _is_transient_api_error(exc):
                raise
            time.sleep(min(2 ** attempt, 8))
    assert last_exc is not None
    raise last_exc


def _completion_once_with_parameter_fallback(completion: Any, kwargs: dict[str, Any]) -> Any:
    try:
        return _call_completion_with_hard_timeout(completion, kwargs)
    except Exception as exc:
        if _mentions_unsupported_parameter(exc, "temperature") and "temperature" in kwargs:
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("temperature", None)
            return _call_completion_with_hard_timeout(completion, retry_kwargs)
        raise


def _call_completion_with_hard_timeout(completion: Any, kwargs: dict[str, Any]) -> Any:
    timeout = kwargs.get("timeout")
    if not timeout:
        return completion(**kwargs)
    timeout_seconds = max(1.0, float(timeout))
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        return completion(**kwargs)

    def raise_timeout(signum: int, frame: Any) -> None:
        raise TimeoutError(f"LLM completion exceeded local timeout of {timeout_seconds:g}s")

    previous_socket_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout_seconds + 5.0)
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds + 5.0)
    try:
        return completion(**kwargs)
    finally:
        socket.setdefaulttimeout(previous_socket_timeout)
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer and previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _llm_attempt_count(kwargs: dict[str, Any]) -> int:
    model = str(kwargs.get("model") or "").lower()
    provider = str(kwargs.get("custom_llm_provider") or "").lower()
    if model.startswith("dashscope/") or provider == "dashscope":
        return 3
    return 2


def _is_transient_api_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        token in text
        for token in (
            "connection error",
            "connection refused",
            "actively refused",
            "connection reset",
            "remote protocol error",
            "remoteprotocolerror",
            "server disconnected",
            "timeout",
            "timed out",
            "temporarily unavailable",
            "service unavailable",
            "internalservererror",
            "internal server error",
            "internal server",
            "bad gateway",
            "gateway timeout",
            "rate limit",
            "ratelimit",
            "429",
            "502",
            "503",
            "504",
        )
    )


def _mentions_unsupported_parameter(exc: Exception, parameter: str) -> bool:
    text = str(exc).lower()
    return parameter.lower() in text and any(token in text for token in ("deprecated", "unsupported", "not support", "unrecognized", "unknown parameter"))


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
    match = re.search(r"```(?:python)?\s*([\s\S]*)", text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _content_from_response(response: Any, agent_name: str) -> str:
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise LLMError(f"{agent_name} returned an empty response.")
    return content.strip()


def _content_from_stream(stream: Any, agent_name: str) -> str:
    parts: list[str] = []
    for chunk in stream:
        choice = chunk.choices[0]
        delta = getattr(choice, "delta", None)
        content = None
        if isinstance(delta, dict):
            content = delta.get("content")
        elif delta is not None:
            content = getattr(delta, "content", None)
        if content:
            parts.append(content)
    text = "".join(parts).strip()
    if not text:
        raise LLMError(f"{agent_name} returned an empty streaming response.")
    return text


def _image_data_url(path: Path) -> str:
    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _video_data_url(path: Path) -> str:
    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "image/gif"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"
