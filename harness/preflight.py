"""Startup checks for the local harness."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable

from blender.client import BlenderClient

from .config import AgentModelConfig, HarnessConfig


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    severity: str
    message: str
    fix: str | None = None


def run_preflight(
    config: HarnessConfig,
    *,
    prompt: str | None,
    ir_path: Path | None,
    include_animation: bool,
    skip_vision: bool,
    skip_video: bool,
    check_blender: bool = True,
) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []
    issues.extend(_check_rag_docs(config.rag_docs))
    issues.extend(_check_runs_dir(config.runs_dir))
    if check_blender:
        issues.extend(_check_blender(config))
    issues.extend(
        _check_agent_credentials(
            _required_agents(
                config,
                prompt=prompt,
                ir_path=ir_path,
                include_animation=include_animation,
                skip_vision=skip_vision,
                skip_video=skip_video,
            )
        )
    )
    return issues


def has_errors(issues: Iterable[PreflightIssue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def format_issue(issue: PreflightIssue) -> str:
    prefix = issue.severity.upper()
    if issue.fix:
        return f"[Preflight] {prefix}: {issue.message} Fix: {issue.fix}"
    return f"[Preflight] {prefix}: {issue.message}"


def _check_rag_docs(paths: tuple[Path, ...]) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []
    for path in paths:
        if not path.exists():
            issues.append(
                PreflightIssue(
                    "error",
                    f"RAG path does not exist: {path}",
                    "Set VERIANIM_RAG_DOCS to existing files/directories or restore the docs/rag directory.",
                )
            )
    return issues


def _check_runs_dir(path: Path) -> list[PreflightIssue]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".preflight_write_check"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        return [
            PreflightIssue(
                "error",
                f"Runs directory is not writable: {path} ({exc})",
                "Set VERIANIM_RUNS_DIR to a writable directory.",
            )
        ]
    return []


def _check_blender(config: HarnessConfig) -> list[PreflightIssue]:
    result = BlenderClient.get_scene_info(host=config.blender_host, port=config.blender_port)
    if isinstance(result, dict) and str(result.get("status", "")).lower() == "success":
        return []
    message = result.get("message") if isinstance(result, dict) else str(result)
    return [
        PreflightIssue(
            "error",
            f"Could not reach Blender addon server at {config.blender_host}:{config.blender_port}. {message or ''}".strip(),
            "Start Blender 4.5.4, enable blender/addon.py, and click Start VeriAnim Server, or use --skip-preflight.",
        )
    ]


def _required_agents(
    config: HarnessConfig,
    *,
    prompt: str | None,
    ir_path: Path | None,
    include_animation: bool,
    skip_vision: bool,
    skip_video: bool,
) -> list[AgentModelConfig]:
    agents = [config.coder, config.refiner]
    if prompt and not ir_path:
        agents.insert(0, config.planner)
    if not skip_vision:
        agents.append(config.vision)
    if include_animation and not skip_video:
        agents.append(config.video)
    return agents


def _check_agent_credentials(agents: Iterable[AgentModelConfig]) -> list[PreflightIssue]:
    issues: list[PreflightIssue] = []
    seen: set[str] = set()
    for agent in agents:
        key = f"{agent.name}:{agent.model}"
        if key in seen:
            continue
        seen.add(key)
        if _agent_has_credentials(agent):
            continue
        provider_envs = _provider_env_names(agent.model)
        if not provider_envs:
            continue
        env_list = ", ".join(provider_envs)
        issues.append(
            PreflightIssue(
                "error",
                f"No API key found for {agent.name} model '{agent.model}'.",
                f"Set VERIANIM_{agent.name.upper()}_API_KEY, LITELLM_API_KEY, or one of: {env_list}.",
            )
        )
    return issues


def _agent_has_credentials(agent: AgentModelConfig) -> bool:
    if agent.api_key or os.environ.get("LITELLM_API_KEY"):
        return True
    if agent.api_base and _is_local_endpoint(agent.api_base):
        return True
    return any(os.environ.get(name) for name in _provider_env_names(agent.model))


def _provider_env_names(model: str) -> tuple[str, ...]:
    provider = model.split("/", 1)[0].lower() if "/" in model else ""
    mapping = {
        "anthropic": ("ANTHROPIC_API_KEY",),
        "azure": ("AZURE_API_KEY",),
        "dashscope": ("DASHSCOPE_API_KEY",),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "openai": ("OPENAI_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
    }
    no_key_providers = {"ollama", "lm_studio", "hosted_vllm"}
    if provider in no_key_providers:
        return ()
    return mapping.get(provider, ())


def _is_local_endpoint(value: str) -> bool:
    lowered = value.lower()
    return any(token in lowered for token in ("localhost", "127.0.0.1", "0.0.0.0"))
