"""Agent implementations for planning, coding, refinement, and model verifiers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import HarnessConfig
from .ir import GenerationIR, Severity, ValidationIssue, ValidationReport, VerificationMode, report_to_json
from .llm import LLMClient, extract_code_block
from .rag import LocalRAG
from .serde import from_dict


class PlannerAgent:
    def __init__(self, config: HarnessConfig, rag: LocalRAG):
        self.llm = LLMClient(config.planner)
        self.rag = rag

    def plan(self, prompt: str, *, include_animation: bool = False) -> GenerationIR:
        context = self.rag.format_context("IR SceneSpec AnimationSpec ScreenshotPlan VideoVerifierSpec", limit=5)
        ir_reference = _load_ir_reference()
        system = (
            "You are the planner for a Blender 4.5.4 code-generation harness. "
            "Return only a JSON object matching the GenerationIR schema. "
            "Use stable machine ids. Include screenshot views for visual validation. "
            "When animation is requested, include AnimationSpec and video verifier settings. "
            "Do not invent fields outside the schema. If you create a relation, it must include id, relation_type, subject_id, and object_id."
        )
        user = f"""
User prompt:
{prompt}

Animation requested: {include_animation}

Complete IR definition:
{ir_reference}

Strict JSON skeleton:
{_planner_json_skeleton(include_animation)}

Relevant IR documentation:
{context}

Return a JSON object with keys: prompt, scene, optional animation, version, notes.
Use Blender's Z-up coordinate system and meters.
"""
        return self._generate_valid_ir(system, user)

    def revise(self, ir: GenerationIR, user_request: str, *, include_animation: bool | None = None) -> GenerationIR:
        context = self.rag.format_context("IR revision SceneSpec AnimationSpec user refinement", limit=5)
        ir_reference = _load_ir_reference()
        system = (
            "You revise an existing GenerationIR for a Blender 4.5.4 harness. "
            "Return only the complete revised GenerationIR JSON. Preserve stable ids where possible. "
            "Add new ids only for new objects, relations, cameras, screenshots, or animation events. "
            "Do not invent fields outside the schema. If you create a relation, it must include id, relation_type, subject_id, and object_id."
        )
        user = f"""
Current GenerationIR:
{ir.to_json()}

User change request:
{user_request}

Animation requested override:
{include_animation}

Complete IR definition:
{ir_reference}

Strict JSON skeleton:
{_planner_json_skeleton(include_animation if include_animation is not None else bool(ir.animation))}

Relevant IR documentation:
{context}

Return the full revised GenerationIR JSON.
"""
        return self._generate_valid_ir(system, user)

    def _generate_valid_ir(self, system: str, user: str) -> GenerationIR:
        last_error = ""
        for attempt in range(2):
            request = user
            if attempt:
                request += f"""

The previous JSON failed to decode or validate:
{last_error}

Return corrected complete GenerationIR JSON only. Every relation must include
relation_type, subject_id, and object_id. Every object must include id and description.
"""
            data = self.llm.json_text(system, request)
            try:
                ir = from_dict(GenerationIR, data)
                report = ir.validate()
                if report.passed:
                    return ir
                last_error = report_to_json(report)
            except Exception as exc:
                last_error = str(exc)
        raise ValueError(f"Planner produced invalid IR after retry:\n{last_error}")


class CoderAgent:
    def __init__(self, config: HarnessConfig, rag: LocalRAG):
        self.llm = LLMClient(config.coder)
        self.rag = rag

    def generate(self, ir: GenerationIR) -> str:
        query = "Blender 4.5 bpy data API mesh from_pydata material camera light render keyframe_insert"
        context = self.rag.format_context(query, limit=8)
        system = (
            "You are a senior Blender 4.5.4 Python coder. "
            "Generate one complete Python script that creates the requested scene and optional animation. "
            "Use data API where possible, stable ll3m custom properties, modular factory functions, and explicit collections. "
            "Do not use unavailable third-party Blender add-ons. Return only Python code."
        )
        user = f"""
GenerationIR JSON:
{ir.to_json()}

Blender 4.5.4 RAG context:
{context}

Script requirements:
- Clear the current scene safely at the start.
- Create all objects, materials, cameras, lights, and environment from the IR.
- Assign custom properties: ll3m_id, ll3m_role, ll3m_part where appropriate.
- Keep object names stable and human-readable.
- Set frame_start/frame_end/fps if animation exists.
- Insert keyframes for AnimationSpec events when present.
- Define a final variable named LL3M_METADATA with object ids and created object names.
"""
        return extract_code_block(self.llm.complete_text(system, user, max_tokens=12000))


class RefinerAgent:
    def __init__(self, config: HarnessConfig, rag: LocalRAG):
        self.llm = LLMClient(config.refiner)
        self.rag = rag

    def refine(
        self,
        *,
        ir: GenerationIR,
        code: str,
        reports: list[ValidationReport],
        execution_error: str | None = None,
    ) -> str:
        context = self.rag.format_context(_issue_query(reports, execution_error), limit=8)
        report_json = json.dumps([report.to_dict() if hasattr(report, "to_dict") else _report_dict(report) for report in reports], indent=2)
        system = (
            "You are a Blender 4.5.4 refiner. Repair the Python script locally. "
            "Keep correct existing structure. Return only the full corrected Python script."
        )
        user = f"""
GenerationIR:
{ir.to_json()}

Execution error:
{execution_error or "None"}

Validation reports:
{report_json}

Relevant Blender 4.5.4 notes:
{context}

Current script:
```python
{code}
```
"""
        return extract_code_block(self.llm.complete_text(system, user, max_tokens=12000))

    def apply_user_request(
        self,
        *,
        ir: GenerationIR,
        code: str,
        user_request: str,
        scene_graph: dict[str, Any] | None = None,
    ) -> str:
        context = self.rag.format_context(user_request + " Blender 4.5 bpy scene update", limit=8)
        system = (
            "You are an interactive Blender 4.5.4 code refiner. "
            "Update the existing full Python script to satisfy the user's new request. "
            "Preserve working code and object ids where possible. "
            "Return only the full corrected Python script."
        )
        user = f"""
Revised GenerationIR:
{ir.to_json()}

User change request:
{user_request}

Current Blender scene graph:
{json.dumps(scene_graph or {}, indent=2, default=str)[:16000]}

Relevant Blender 4.5.4 notes:
{context}

Current script:
```python
{code}
```
"""
        return extract_code_block(self.llm.complete_text(system, user, max_tokens=12000))


class VisionVerifierAgent:
    def __init__(self, config: HarnessConfig):
        self.llm = LLMClient(config.vision)

    def verify(self, ir: GenerationIR, screenshot_paths: list[Path], deterministic_report: ValidationReport) -> ValidationReport:
        if not screenshot_paths:
            return ValidationReport.failed(
                VerificationMode.VISION,
                [
                    ValidationIssue(
                        code="NO_SCREENSHOTS",
                        message="Vision verification could not run because no screenshots were produced.",
                    )
                ],
            )

        system = (
            "You are a visual verifier for Blender-generated scenes. "
            "Return only JSON with keys: passed, summary, issues. "
            "Each issue must include code, message, severity, optional target_id, relation_id, frame, suggested_fix, evidence."
        )
        user = f"""
Original prompt:
{ir.prompt.text}

SceneSpec excerpt:
{json.dumps(ir.to_dict().get("scene", {}), indent=2)[:12000]}

Deterministic report:
{report_to_json(deterministic_report)}

Evaluate the screenshots for semantic correctness, missing objects, wrong spatial relationships,
bad camera angle, occlusion, poor lighting, and visible geometry defects.
"""
        data = self.llm.json_multimodal(system, user, screenshot_paths, max_tokens=4000)
        return _report_from_model(data, VerificationMode.VISION)


class VideoVerifierAgent:
    def __init__(self, config: HarnessConfig):
        self.llm = LLMClient(config.video)

    def verify(
        self,
        ir: GenerationIR,
        sampled_frame_paths: list[Path],
        preview_video_path: Path | None,
        deterministic_report: ValidationReport,
        transform_trace: dict[str, Any] | None = None,
    ) -> ValidationReport:
        if not ir.animation:
            return ValidationReport.ok(VerificationMode.VIDEO, "No animation requested.")
        if not sampled_frame_paths:
            return ValidationReport.failed(
                VerificationMode.VIDEO,
                [ValidationIssue(code="NO_SAMPLED_FRAMES", message="No sampled animation frames were produced.")],
            )

        system = (
            "You are a temporal verifier for Blender animations. "
            "Return only JSON with keys: passed, summary, issues. "
            "Judge action order, motion path, smoothness, object interactions, and camera visibility."
        )
        user = f"""
Original prompt:
{ir.prompt.text}

AnimationSpec:
{json.dumps(ir.to_dict().get("animation", {}), indent=2)[:12000]}

Preview video path for metadata only:
{preview_video_path or "None"}

Transform trace:
{json.dumps(transform_trace or {}, indent=2)[:12000]}

Deterministic animation report:
{report_to_json(deterministic_report)}

The attached images are ordered sampled frames. Verify whether the requested animation is visually and temporally correct.
"""
        data = self.llm.json_multimodal(system, user, sampled_frame_paths, max_tokens=4000)
        return _report_from_model(data, VerificationMode.VIDEO)


def _issue_query(reports: list[ValidationReport], execution_error: str | None) -> str:
    parts = [execution_error or ""]
    for report in reports:
        for issue in report.issues:
            parts.extend([issue.code, issue.message, issue.suggested_fix or ""])
    return " ".join(parts) or "Blender 4.5 bpy gotchas"


def _load_ir_reference() -> str:
    path = Path("docs/ir.md")
    if not path.exists():
        return "IR reference file docs/ir.md is unavailable. Use harness.ir dataclass field names."
    text = path.read_text(encoding="utf-8")
    # Keep the whole practical definition. This is more reliable than retrieval
    # for planner correctness and still small enough for the configured models.
    return text[:24000]


def _planner_json_skeleton(include_animation: bool | None) -> str:
    animation_block = """
  "animation": {
    "duration_frames": 120,
    "fps": 24,
    "events": [
      {
        "id": "event_id",
        "action": "translate",
        "subject_ids": ["object_id"],
        "start_frame": 1,
        "end_frame": 120,
        "description": "what happens",
        "target_ids": [],
        "interpolation": "ease_in_out",
        "required": true,
        "expected_visual_result": "what the video verifier should see",
        "constraints": []
      }
    ],
    "camera_events": [],
    "loop": false,
    "verifier": {
      "enabled": true,
      "model_hint": "qwen3.5-omni",
      "sampled_frames": [1, 60, 120],
      "require_preview_video": true,
      "questions": [],
      "pass_criteria": [],
      "max_rounds": 2
    }
  },""" if include_animation else ""
    return f"""{{
  "prompt": {{
    "text": "original user prompt",
    "negative_text": null,
    "image_paths": [],
    "user_constraints": []
  }},
  "scene": {{
    "objects": [
      {{
        "id": "stable_object_id",
        "description": "semantic description",
        "label": "Human Label",
        "category": "generic",
        "role": "primary",
        "importance": "required",
        "parts": [
          {{
            "id": "part_id",
            "description": "part description",
            "required": true,
            "material_id": "material_id",
            "expected_count": 1
          }}
        ],
        "required_features": [],
        "optional_features": [],
        "forbidden_features": [],
        "material_ids": ["material_id"],
        "visual_check_prompts": []
      }}
    ],
    "relations": [
      {{
        "id": "relation_id",
        "relation_type": "on_top_of",
        "subject_id": "stable_object_id",
        "object_id": "other_object_id",
        "description": "semantic relation",
        "required": true,
        "tolerance": 0.05,
        "visual_priority": "required"
      }}
    ],
    "materials": [
      {{
        "id": "material_id",
        "description": "material description",
        "base_color": [0.5, 0.5, 0.5, 1.0],
        "texture_hints": []
      }}
    ],
    "environment": {{
      "environment_type": "studio",
      "description": "environment description",
      "floor": "floor description",
      "lights": [
        {{
          "id": "key_light",
          "light_type": "area",
          "description": "soft key light",
          "location": [2.0, -3.0, 4.0],
          "energy": 500,
          "size": 4.0
        }}
      ],
      "ambient_occlusion": true
    }},
    "cameras": [
      {{
        "id": "camera_main",
        "view_type": "three_quarter",
        "description": "main inspection view",
        "target_object_ids": ["stable_object_id"],
        "coverage": "all primary objects visible"
      }}
    ],
    "style": {{
      "description": "style description",
      "detail_level": "medium",
      "color_palette": []
    }},
    "verifier": {{
      "deterministic_checks": [],
      "screenshot_plan": {{
        "views": [
          {{
            "id": "three_quarter",
            "view_type": "three_quarter",
            "description": "overall scene view",
            "target_object_ids": ["stable_object_id"],
            "relation_ids": [],
            "required": true
          }}
        ],
        "min_required_views": 3
      }},
      "visual": {{
        "enabled": true,
        "model_hint": null,
        "required_view_ids": ["three_quarter"],
        "questions": [],
        "pass_criteria": [],
        "max_rounds": 2
      }},
      "video": {{
        "enabled": false,
        "model_hint": "qwen3.5-omni",
        "sampled_frames": [],
        "require_preview_video": true,
        "questions": [],
        "pass_criteria": [],
        "max_rounds": 2
      }}
    }},
    "coordinate_system": "Blender default: Z up, right-handed",
    "units": "meters"
  }},
{animation_block}
  "version": "0.1",
  "project_id": null,
  "notes": "planner notes"
}}"""


def _report_dict(report: ValidationReport) -> dict[str, Any]:
    from .ir import report_to_dict

    return report_to_dict(report)


def _report_from_model(data: dict[str, Any], mode: VerificationMode) -> ValidationReport:
    issues = []
    for item in data.get("issues", []) or []:
        issues.append(
            ValidationIssue(
                code=str(item.get("code") or "MODEL_REPORTED_ISSUE"),
                message=str(item.get("message") or item.get("problem") or "Model reported an issue."),
                severity=_severity(item.get("severity", "major")),
                target_id=item.get("target_id") or item.get("object"),
                relation_id=item.get("relation_id"),
                frame=item.get("frame"),
                suggested_fix=item.get("suggested_fix"),
                evidence=item.get("evidence") or {},
            )
        )
    return ValidationReport(
        mode=mode,
        passed=bool(data.get("passed", data.get("pass", not issues))),
        issues=issues,
        summary=data.get("summary"),
    )


def _severity(value: Any) -> Severity:
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value))
    except ValueError:
        return Severity.MAJOR
