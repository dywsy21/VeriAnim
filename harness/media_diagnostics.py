"""Media capability diagnostics built on the existing verifier agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .agents import VideoVerifierAgent, VisionVerifierAgent
from .config import AgentModelConfig, HarnessConfig
from .ir import (
    AnimationSpec,
    CameraSpec,
    GenerationIR,
    ObjectSpec,
    SceneSpec,
    SourcePrompt,
    ValidationReport,
    VerificationMode,
    VideoVerifierSpec,
    report_to_dict,
)


def write_probe_assets(output_dir: Path) -> dict[str, Any]:
    """Write deterministic tiny image/GIF assets for media capability probes."""

    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "vision_probe.png"
    frame_1 = output_dir / "video_probe_frame_0001.png"
    frame_2 = output_dir / "video_probe_frame_0002.png"
    video_path = output_dir / "video_probe.gif"

    first = Image.new("RGB", (96, 64), (245, 245, 245))
    draw = ImageDraw.Draw(first)
    draw.rectangle((8, 18, 36, 46), fill=(220, 40, 40))
    draw.rectangle((60, 18, 88, 46), fill=(40, 90, 220))
    draw.line((0, 54, 96, 54), fill=(40, 40, 40), width=2)
    first.save(image_path)
    first.save(frame_1)

    second = Image.new("RGB", (96, 64), (245, 245, 245))
    draw = ImageDraw.Draw(second)
    draw.rectangle((28, 18, 56, 46), fill=(220, 40, 40))
    draw.rectangle((60, 18, 88, 46), fill=(40, 90, 220))
    draw.line((0, 54, 96, 54), fill=(40, 40, 40), width=2)
    second.save(frame_2)
    first.save(video_path, save_all=True, append_images=[second], duration=250, loop=0)

    return {
        "image_path": str(image_path),
        "video_path": str(video_path),
        "sampled_frame_paths": [str(frame_1), str(frame_2)],
    }


def run_media_capability_diagnostics(
    config: HarnessConfig,
    *,
    image_path: Path,
    video_path: Path,
    sampled_frame_paths: list[Path],
) -> dict[str, Any]:
    """Run vision and video diagnostics through the normal verifier agents."""

    vision_report = VisionVerifierAgent(config).verify(
        _probe_ir(animation=False),
        [image_path],
        ValidationReport.ok(VerificationMode.DETERMINISTIC, "Probe deterministic report passed."),
    )
    video_report = VideoVerifierAgent(config).verify(
        _probe_ir(animation=True),
        sampled_frame_paths,
        video_path,
        ValidationReport.ok(VerificationMode.DETERMINISTIC, "Probe deterministic animation report passed."),
        transform_trace={
            "probe_square": [
                {"frame": 1, "location": [0.0, 0.0, 0.0]},
                {"frame": 2, "location": [0.25, 0.0, 0.0]},
            ]
        },
    )
    return {
        "vision": _diagnostic_record(
            kind="vision",
            agent=config.vision,
            report=vision_report,
            expected_media_parts=["image_url"],
            artifact_paths=[str(image_path)],
        ),
        "video": _diagnostic_record(
            kind="video",
            agent=config.video,
            report=video_report,
            expected_media_parts=["video_url", "image_url"],
            artifact_paths=[str(video_path), *[str(path) for path in sampled_frame_paths]],
        ),
    }


def _probe_ir(*, animation: bool) -> GenerationIR:
    ir = GenerationIR(
        prompt=SourcePrompt(text="media capability probe"),
        scene=SceneSpec(
            objects=[ObjectSpec(id="probe_square", description="colored square media probe")],
            cameras=[CameraSpec(id="camera_main", target_object_ids=["probe_square"])],
        ),
    )
    if animation:
        ir.animation = AnimationSpec(
            duration_frames=2,
            verifier=VideoVerifierSpec(
                enabled=True,
                sampled_frames=[1, 2],
                require_preview_video=True,
                questions=["Can the verifier read the attached GIF/video and sampled frames?"],
                pass_criteria=["The attached GIF/video is readable as temporal media."],
            ),
        )
    return ir


def _diagnostic_record(
    *,
    kind: str,
    agent: AgentModelConfig,
    report: ValidationReport,
    expected_media_parts: list[str],
    artifact_paths: list[str],
) -> dict[str, Any]:
    issue_codes = [issue.code for issue in report.issues]
    unsupported_code = "VISION_INPUT_UNSUPPORTED" if kind == "vision" else "VIDEO_INPUT_UNSUPPORTED"
    if report.passed:
        status = "supported"
    elif unsupported_code in issue_codes:
        status = "unsupported"
    elif issue_codes:
        status = "blocked_or_inconclusive"
    else:
        status = "unknown"
    return {
        "status": status,
        "agent": _agent_record(agent),
        "expected_media_parts": expected_media_parts,
        "artifact_paths": artifact_paths,
        "report": report_to_dict(report),
    }


def _agent_record(agent: AgentModelConfig) -> dict[str, Any]:
    return {
        "name": agent.name,
        "model": agent.model,
        "api_base_configured": bool(agent.api_base),
        "api_key_configured": bool(agent.api_key),
        "api_version_configured": bool(agent.api_version),
        "custom_llm_provider": agent.custom_llm_provider,
        "supports_images_flag": agent.supports_images,
        "stream": agent.stream,
        "timeout_seconds": agent.timeout_seconds,
    }
