"""Configuration for the local LL3M harness."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        # Keep imports usable before optional dependencies are installed.
        env_path = Path(".env")
        if not env_path.exists():
            return
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_optional(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class AgentModelConfig:
    name: str
    model: str
    temperature: float = 0.1
    max_tokens: int | None = None
    timeout_seconds: int | None = None
    api_base: str | None = None
    api_key: str | None = None
    api_version: str | None = None
    custom_llm_provider: str | None = None


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    planner: AgentModelConfig
    coder: AgentModelConfig
    refiner: AgentModelConfig
    vision: AgentModelConfig
    video: AgentModelConfig
    max_refinement_rounds: int
    max_visual_refinement_rounds: int
    max_video_refinement_rounds: int
    rag_docs: tuple[Path, ...]
    runs_dir: Path
    blender_host: str
    blender_port: int
    headless_rendering: bool
    render_width: int
    render_height: int
    tui_initial_animation: bool
    tui_skip_vision: bool
    tui_skip_video: bool

    @classmethod
    def from_env(cls) -> "HarnessConfig":
        _load_dotenv()
        rag_docs = tuple(
            Path(part.strip())
            for part in _env("LL3M_RAG_DOCS", "docs/rag,docs/ir.md").split(",")
            if part.strip()
        )
        return cls(
            planner=_agent_config("planner", "openai/gpt-4.1", 0.2),
            coder=_agent_config("coder", "openai/gpt-4.1", 0.1),
            refiner=_agent_config("refiner", "openai/gpt-4.1", 0.1),
            vision=_agent_config("vision", "openai/gpt-4.1", 0.0),
            video=_agent_config("video", "dashscope/qwen-omni-turbo", 0.0),
            max_refinement_rounds=_env_int("LL3M_MAX_REFINEMENT_ROUNDS", 2),
            max_visual_refinement_rounds=_env_int("LL3M_MAX_VISUAL_REFINEMENT_ROUNDS", 6),
            max_video_refinement_rounds=_env_int("LL3M_MAX_VIDEO_REFINEMENT_ROUNDS", 6),
            rag_docs=rag_docs,
            runs_dir=Path(_env("LL3M_RUNS_DIR", "runs")),
            blender_host=_env("LL3M_BLENDER_HOST", "localhost"),
            blender_port=_env_int("LL3M_BLENDER_PORT", 8888),
            headless_rendering=_env_bool("LL3M_HEADLESS_RENDERING", False),
            render_width=_env_int("LL3M_RENDER_WIDTH", 1280),
            render_height=_env_int("LL3M_RENDER_HEIGHT", 720),
            tui_initial_animation=_env_bool("LL3M_TUI_INITIAL_ANIMATION", False),
            tui_skip_vision=_env_bool("LL3M_TUI_SKIP_VISION", False),
            tui_skip_video=_env_bool("LL3M_TUI_SKIP_VIDEO", False),
        )


def _agent_config(name: str, default_model: str, default_temperature: float) -> AgentModelConfig:
    prefix = f"LL3M_{name.upper()}"
    global_api_base = _env_optional("LITELLM_API_BASE")
    global_api_key = _env_optional("LITELLM_API_KEY")
    return AgentModelConfig(
        name=name,
        model=_env(f"{prefix}_MODEL", default_model),
        temperature=_env_float(f"{prefix}_TEMPERATURE", default_temperature),
        max_tokens=_env_int(f"{prefix}_MAX_TOKENS", 0) or None,
        timeout_seconds=_env_int(f"{prefix}_TIMEOUT_SECONDS", _env_int("LL3M_LLM_TIMEOUT_SECONDS", 90)),
        api_base=_env_optional(f"{prefix}_API_BASE") or global_api_base,
        api_key=_env_optional(f"{prefix}_API_KEY") or global_api_key,
        api_version=_env_optional(f"{prefix}_API_VERSION"),
        custom_llm_provider=_env_optional(f"{prefix}_PROVIDER"),
    )
