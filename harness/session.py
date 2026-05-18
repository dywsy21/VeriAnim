"""Reusable interactive harness session for CLI and TUI frontends."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable

from .agents import CoderAgent, MaterialAgent, PlannerAgent, RefinerAgent, VideoVerifierAgent, VisionVerifierAgent
from .artifacts import ArtifactStore
from .blender_runtime import BlenderRuntime
from .config import HarnessConfig
from .ir import GenerationIR, Severity, ValidationIssue, ValidationReport, VerificationMode, report_to_dict
from .rag import LocalRAG


@dataclass(slots=True)
class HarnessEvent:
    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


EventCallback = Callable[[HarnessEvent], None]


class InteractiveHarnessSession:
    """Stateful local harness session with multi-turn user changes."""

    def __init__(
        self,
        config: HarnessConfig,
        *,
        include_animation: bool = False,
        skip_vision: bool = False,
        skip_video: bool = False,
        callback: EventCallback | None = None,
    ):
        self.config = config
        self.include_animation = include_animation
        self.skip_vision = skip_vision
        self.skip_video = skip_video
        self.callback = callback
        self.rag = LocalRAG(config.rag_docs)
        self.planner = PlannerAgent(config, self.rag)
        self.materials = MaterialAgent(config)
        self.coder = CoderAgent(config, self.rag)
        self.refiner = RefinerAgent(config, self.rag)
        self.vision = VisionVerifierAgent(config)
        self.video = VideoVerifierAgent(config)
        self.blender = BlenderRuntime(config)
        self.store: ArtifactStore | None = None
        self.ir: GenerationIR | None = None
        self.code: str | None = None
        self._last_executed_code: str | None = None
        self._frozen_scene_graph: dict[str, Any] | None = None
        self.turn_index = 0
        self._latest_screenshots: list[Path] = []

    @property
    def has_scene(self) -> bool:
        return self.ir is not None and self.code is not None

    def start(self, prompt: str) -> Path:
        self.store = ArtifactStore.create(self.config.runs_dir)
        self.turn_index = 0
        self._last_executed_code = None
        self._emit("session", f"Run directory: {self.store.root}", path=str(self.store.root))

        self._emit("planner", "Planning structured IR")
        planned_ir = self.planner.plan(prompt, include_animation=self.include_animation)
        self.store.write_json("ir_planned.json", planned_ir.to_dict())
        planned_ir = self._resolve_material_textures(planned_ir)
        self.store.write_json("ir.json", planned_ir.to_dict())
        self._emit("planner", f"Planned {len(planned_ir.scene.objects)} objects")

        if self.include_animation and planned_ir.animation:
            self._run_two_stage_animation_start(planned_ir)
            return self.store.root

        self.ir = planned_ir
        self._emit("coder", "Generating Blender script")
        self.code = self.coder.generate(self.ir)
        self.store.write_text("code/generated_scene.py", self.code)

        self._execute_validate_refine(reason="initial")
        return self.store.root

    def start_from_ir(self, ir: GenerationIR) -> Path:
        self.store = ArtifactStore.create(self.config.runs_dir)
        self.turn_index = 0
        self._last_executed_code = None
        self._emit("session", f"Run directory: {self.store.root}", path=str(self.store.root))
        ir = self._resolve_material_textures(ir)
        self.store.write_json("ir.json", ir.to_dict())
        if self.include_animation and ir.animation:
            self._run_two_stage_animation_start(ir)
            return self.store.root
        self.ir = ir
        self._emit("coder", "Generating Blender script from provided IR")
        self.code = self.coder.generate(self.ir)
        self.store.write_text("code/generated_scene.py", self.code)
        self._execute_validate_refine(reason="initial")
        return self.store.root

    def _run_two_stage_animation_start(self, full_ir: GenerationIR) -> None:
        assert self.store is not None
        self._emit("stage", "Stage 1/2: generating and validating static scene before animation")
        full_ir.ensure_progressive_stages()
        scene_ir = full_ir.static_scene_projection()
        self.ir = scene_ir
        self.store.write_json("ir_scene_stage.json", scene_ir.to_dict())
        self._emit("coder", "Generating static Blender scene script")
        self.code = self.coder.generate(scene_ir, static_only=True)
        self.store.write_text("code/generated_scene_stage.py", self.code)
        scene_passed = self._execute_validate_refine(reason="scene_stage")
        if not scene_passed:
            self._emit("warn", "Static scene stage did not pass; animation stage skipped")
            return

        self._emit("stage", "Stage 2/2: adding animation to validated scene")
        self.ir = full_ir
        self.store.write_json("ir_animation_stage.json", full_ir.to_dict())
        scene_graph = self.blender.get_scene_graph()
        self.store.write_json("scene_stage_graph.json", scene_graph)
        self._frozen_scene_graph = scene_graph
        base_code = self._last_executed_code or self.code or ""
        self.code = self.refiner.add_animation(ir=full_ir, code=base_code, scene_graph=scene_graph)
        self.store.write_text("code/generated_animation_stage.py", self.code)
        self._execute_validate_refine(reason="animation_stage")

    def apply_user_request(self, request: str) -> Path:
        if not self.has_scene or not self.store or not self.ir or not self.code:
            return self.start(request)

        self.turn_index += 1
        self._emit("user", request)

        self._emit("planner", "Revising IR from user request")
        self.ir = self.planner.revise(self.ir, request, include_animation=self.include_animation)
        self.ir = self._resolve_material_textures(self.ir)
        self.store.write_json(f"turns/turn_{self.turn_index:03d}_ir.json", self.ir.to_dict())

        self._emit("scene", "Reading current Blender scene graph")
        scene_graph = self.blender.get_scene_graph()
        self.store.write_json(f"turns/turn_{self.turn_index:03d}_scene_graph.json", scene_graph)

        self._emit("refiner", "Applying user request to current script")
        self.code = self.refiner.apply_user_request(
            ir=self.ir,
            code=self.code,
            user_request=request,
            scene_graph=scene_graph,
        )
        self.store.write_text(f"code/user_turn_{self.turn_index:03d}.py", self.code)

        self._execute_validate_refine(reason=f"user_turn_{self.turn_index:03d}")
        return self.store.root

    def _resolve_material_textures(self, ir: GenerationIR) -> GenerationIR:
        if not self.store:
            return ir
        wanted = [
            material.id
            for material in ir.scene.materials
            if material.needs_texture or material.texture_query
        ]
        if not wanted or not self.config.texture_search_enabled:
            return ir
        self._emit("materials", "Resolving external material textures", material_ids=wanted)
        resolved = self.materials.resolve(ir, self.store.root / "textures")
        self.store.write_json("materials/texture_search_results.json", {"results": self.materials.last_results})
        selected = [item for item in self.materials.last_results if item.get("selected")]
        if selected:
            self._emit("materials", f"Selected {len(selected)} vision-approved texture assets", results=selected)
        elif self.materials.last_results:
            self._emit("materials", "No external texture candidates passed vision approval", results=self.materials.last_results)
        return resolved

    def _execute_validate_refine(self, *, reason: str) -> bool:
        if not self.store or not self.ir or self.code is None:
            raise RuntimeError("Session has not been initialized.")

        reports: list[ValidationReport] = []
        execution_error: str | None = None
        max_rounds = self._max_refinement_rounds()
        last_failure_signature: str | None = None
        stagnant_rounds = 0
        failure_counts: dict[str, int] = {}
        self._emit("validate", f"Verifier-gated loop enabled: up to {max_rounds + 1} validation passes")

        for round_index in range(max_rounds + 1):
            label = f"{reason}_round_{round_index}"
            static_report = self._static_code_report()
            if not static_report.passed:
                reports = [static_report]
                execution_error = "Generated script failed static completeness checks before Blender execution."
                self.store.write_json(f"reports/{label}_code_static.json", report_to_dict(static_report))
                self._emit_report(static_report)
            else:
                reports = []
                execution_error = None

            if reports:
                if round_index >= max_rounds:
                    self._emit("warn", "Verifier loop stopped at safety cap before all stages passed")
                    self.store.write_text("code/final_scene.py", self._last_executed_code or self.code)
                    return False
                refine_code = self._last_executed_code or self.code
                if self._last_executed_code and self.code != self._last_executed_code:
                    execution_error = (
                        (execution_error or "")
                        + "\nThe latest refiner output was rejected before execution. "
                        + "Continue from the last executable script and return a complete full script."
                    ).strip()
                failed_modes = ", ".join(report.mode.value for report in reports if not report.passed) or "unknown"
                self._emit("refiner", f"Refining script from failed verifier feedback: {failed_modes}")
                self.code = self.refiner.refine(
                    ir=self.ir,
                    code=refine_code,
                    reports=reports,
                    execution_error=execution_error,
                    screenshot_paths=self._latest_screenshots,
                )
                self.store.write_text(f"code/{label}_refined.py", self.code)
                continue

            self._emit("execute", f"Executing Blender script: {label}")
            execution = self.blender.execute_scene_code(self.code)
            self.store.write_text(f"logs/{label}_execution.txt", execution.stdout)
            if not execution.ok:
                execution_error = execution.message or execution.stdout
                self._emit("error", "Blender execution failed", error=execution_error)
                reports = [
                    ValidationReport.failed(
                        VerificationMode.DETERMINISTIC,
                        [ValidationIssue(code="BLENDER_EXEC_FAILED", message=execution_error)],
                    )
                ]
            else:
                execution_error = None
                self._emit("execute", "Blender scene updated")
                self._last_executed_code = self.code
                reports = self._run_validation_pass(label)

            if all(report.passed for report in reports):
                self._emit("pass", "All enabled validation stages passed")
                self.store.write_text("code/final_scene.py", self.code)
                self._render_final_animation_gif()
                return True

            if round_index >= max_rounds:
                self._emit("warn", "Verifier loop stopped at safety cap before all stages passed")
                self.store.write_text("code/final_scene.py", self._last_executed_code or self.code)
                return False

            signature = _failure_signature(reports, execution_error)
            if signature == last_failure_signature:
                stagnant_rounds += 1
            else:
                last_failure_signature = signature
                stagnant_rounds = 1
            failure_counts[signature] = failure_counts.get(signature, 0) + 1
            if stagnant_rounds >= self.config.max_stagnant_refinement_rounds:
                self._emit(
                    "warn",
                    "Verifier loop stopped because the same failure repeated without progress",
                    stagnant_rounds=stagnant_rounds,
                )
                self.store.write_text("code/final_scene.py", self._last_executed_code or self.code)
                return False
            if failure_counts[signature] >= self.config.max_stagnant_refinement_rounds:
                self._emit(
                    "warn",
                    "Verifier loop stopped because the same failure recurred across refinement attempts",
                    repeated_failures=failure_counts[signature],
                )
                self.store.write_text("code/final_scene.py", self._last_executed_code or self.code)
                return False

            failed_modes = ", ".join(report.mode.value for report in reports if not report.passed) or "unknown"
            self._emit("refiner", f"Refining script from failed verifier feedback: {failed_modes}")
            self.code = self.refiner.refine(
                ir=self.ir,
                code=self.code,
                reports=reports,
                execution_error=execution_error,
                screenshot_paths=self._latest_screenshots,
            )
            self.store.write_text(f"code/{label}_refined.py", self.code)
        return False

    def _static_code_report(self) -> ValidationReport:
        assert self.ir is not None
        assert self.code is not None
        issues: list[ValidationIssue] = []
        try:
            tree = ast.parse(self.code)
        except SyntaxError as exc:
            issues.append(
                ValidationIssue(
                    code="CODE_SYNTAX_ERROR",
                    message=f"Generated Python does not parse: {exc.msg} at line {exc.lineno}.",
                    severity=Severity.CRITICAL,
                )
            )
            return ValidationReport.failed(VerificationMode.DETERMINISTIC, issues, "Generated code is incomplete or invalid.")

        assigned_names = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if "LL3M_METADATA" not in assigned_names:
            issues.append(
                ValidationIssue(
                    code="CODE_MISSING_METADATA",
                    message="Generated script must finish with LL3M_METADATA so the harness can tell it is complete.",
                    severity=Severity.MAJOR,
                )
            )
        if "main" in {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)} and "main" not in called_names:
            issues.append(
                ValidationIssue(
                    code="CODE_ENTRYPOINT_NOT_CALLED",
                    message="Generated script defines main() but never calls it, so no fresh scene may be created.",
                    severity=Severity.CRITICAL,
                )
            )
        keyframe_calls = _count_effective_keyframe_calls(tree)
        if self.ir.animation:
            if len(keyframe_calls) < max(2, len(self.ir.animation.events)):
                issues.append(
                    ValidationIssue(
                        code="CODE_MISSING_ANIMATION_KEYFRAMES",
                        message="Animation script has too few actual keyframe_insert calls for the planned events.",
                        severity=Severity.CRITICAL,
                    )
                )
        elif keyframe_calls:
            issues.append(
                ValidationIssue(
                    code="CODE_STATIC_STAGE_HAS_ANIMATION",
                    message="Static scene stage must not create keyframes or animation data.",
                    severity=Severity.MAJOR,
                )
            )
        if issues:
            return ValidationReport.failed(VerificationMode.DETERMINISTIC, issues, "Generated code failed static completeness checks.")
        return ValidationReport.ok(VerificationMode.DETERMINISTIC, "Generated code passed static completeness checks.")

    def _max_refinement_rounds(self) -> int:
        if not self.ir:
            return self.config.max_refinement_rounds
        max_rounds = self.config.max_refinement_rounds
        visual = self.ir.scene.verifier.visual
        if not self.skip_vision and visual.enabled:
            max_rounds = max(max_rounds, visual.max_rounds, self.config.max_visual_refinement_rounds)
        if self.ir.animation and not self.skip_video and self.ir.animation.verifier.enabled:
            max_rounds = max(max_rounds, self.ir.animation.verifier.max_rounds, self.config.max_video_refinement_rounds)
        return max_rounds

    def _run_validation_pass(self, label: str) -> list[ValidationReport]:
        assert self.store is not None
        assert self.ir is not None
        reports: list[ValidationReport] = []

        if self.ir.animation and self._frozen_scene_graph:
            self.blender.execute_code("import bpy\nbpy.context.scene.frame_set(bpy.context.scene.frame_start)")
            current_graph = self.blender.get_scene_graph()
            preservation_report = _scene_preservation_report(self.ir, self._frozen_scene_graph, current_graph)
            reports.append(preservation_report)
            self.store.write_json(f"reports/{label}_scene_preservation.json", report_to_dict(preservation_report))
            self._emit_report(preservation_report)

        self._emit("validate", "Running deterministic scene validation")
        scene_report = self.blender.validate_scene(self.ir)
        reports.append(scene_report)
        self.store.write_json(f"reports/{label}_scene_deterministic.json", report_to_dict(scene_report))
        self._emit_report(scene_report)

        self._emit("render", "Rendering screenshot plan")
        screenshots = self.blender.render_screenshots(self.ir, self.store.root / "screenshots" / label)
        self._latest_screenshots = screenshots
        self._emit("render", f"Rendered {len(screenshots)} screenshots", paths=[str(path) for path in screenshots])

        if screenshots and not self.skip_vision and self.ir.scene.verifier.visual.enabled:
            self._emit("vision", "Running visual model verification")
            vision_report = self.vision.verify(self.ir, screenshots, scene_report)
            reports.append(vision_report)
            self.store.write_json(f"reports/{label}_scene_vision.json", report_to_dict(vision_report))
            self._emit_report(vision_report)

        if self.ir.animation:
            self._emit("validate", "Running deterministic animation validation")
            anim_report, transform_trace = self.blender.validate_animation(self.ir)
            reports.append(anim_report)
            self.store.write_json(f"reports/{label}_animation_deterministic.json", report_to_dict(anim_report))
            self.store.write_json(f"reports/{label}_animation_trace.json", transform_trace)
            self._emit_report(anim_report)

            if not self.skip_video and self.ir.animation.verifier.enabled:
                self._emit("render", "Rendering animation sampled frames and GIF")
                sampled_frames, preview_video = self.blender.render_animation_samples(
                    self.ir,
                    self.store.root / "animation" / label,
                    render_gif=self.config.render_gif_each_round,
                )
                self._latest_screenshots = [*self._latest_screenshots, *sampled_frames]
                self._emit(
                    "render",
                    f"Rendered {len(sampled_frames)} animation frames"
                    + (f" and GIF {preview_video}" if preview_video else ""),
                    paths=[str(path) for path in sampled_frames],
                    preview=str(preview_video) if preview_video else None,
                )
                self._emit("video", f"Running video verifier on GIF/video plus {len(sampled_frames)} sampled frames")
                video_report = self.video.verify(self.ir, sampled_frames, preview_video, anim_report, transform_trace)
                reports.append(video_report)
                self.store.write_json(f"reports/{label}_animation_video.json", report_to_dict(video_report))
                self._emit_report(video_report)

        return reports

    def _render_final_animation_gif(self) -> None:
        if not self.store or not self.ir or not self.ir.animation:
            return
        self._emit("render", "Rendering final full animation GIF")
        sampled_frames, gif_path = self.blender.render_animation_samples(
            self.ir,
            self.store.root / "animation" / "final",
            render_gif=self.config.render_gif_each_round,
        )
        self._emit(
            "render",
            f"Rendered final animation GIF {gif_path}" if gif_path else "Final animation GIF render produced no GIF",
            paths=[str(path) for path in sampled_frames],
            preview=str(gif_path) if gif_path else None,
        )

    def _emit_report(self, report: ValidationReport) -> None:
        status = "passed" if report.passed else "failed"
        self._emit("report", f"{report.mode.value} verification {status}: {report.summary or ''}", report=report_to_dict(report))
        for issue in report.issues:
            self._emit(
                "issue",
                f"{issue.severity.value}: {issue.code} - {issue.message}",
                target_id=issue.target_id,
                relation_id=issue.relation_id,
                suggested_fix=issue.suggested_fix,
            )

    def _emit(self, kind: str, message: str, **data: Any) -> None:
        if self.callback:
            self.callback(HarnessEvent(kind=kind, message=message, data=data))


def _failure_signature(reports: list[ValidationReport], execution_error: str | None) -> str:
    issues: list[dict[str, Any]] = []
    for report in reports:
        if report.passed:
            continue
        for issue in report.issues:
            issues.append(
                {
                    "mode": report.mode.value,
                    "code": issue.code,
                    "target_id": issue.target_id,
                    "relation_id": issue.relation_id,
                    "frame": issue.frame,
                }
            )
    return json.dumps(
        {
            "execution_error": execution_error or "",
            "issues": sorted(issues, key=lambda item: json.dumps(item, sort_keys=True)),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _count_effective_keyframe_calls(tree: ast.AST) -> list[ast.Call]:
    helper_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "keyframe_insert"
            for child in ast.walk(node)
        ):
            helper_names.add(node.name)
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "keyframe_insert":
            calls.append(node)
        elif isinstance(node.func, ast.Name) and node.func.id in helper_names:
            calls.append(node)
    return calls


def _scene_preservation_report(
    ir: GenerationIR,
    baseline_graph: dict[str, Any],
    current_graph: dict[str, Any],
) -> ValidationReport:
    animated_ids: set[str] = set()
    if ir.animation:
        for event in [*ir.animation.events, *ir.animation.camera_events]:
            animated_ids.update(event.subject_ids)
            animated_ids.update(event.target_ids)
    baseline = _objects_by_ll3m_id(baseline_graph)
    current = _objects_by_ll3m_id(current_graph)
    issues: list[ValidationIssue] = []
    for obj in ir.scene.objects:
        if obj.id in animated_ids:
            continue
        if obj.id not in baseline:
            continue
        if obj.id not in current:
            issues.append(
                ValidationIssue(
                    code="SCENE_BASELINE_OBJECT_REMOVED",
                    message=f"Animation stage removed static baseline object '{obj.id}'.",
                    severity=Severity.CRITICAL,
                    target_id=obj.id,
                )
            )
            continue
        before_size = _bbox_size(baseline[obj.id])
        after_size = _bbox_size(current[obj.id])
        if not before_size or not after_size:
            continue
        max_before = max(before_size)
        if max_before <= 0:
            continue
        delta = max(abs(after_size[index] - before_size[index]) for index in range(3)) / max_before
        if delta > 0.25:
            issues.append(
                ValidationIssue(
                    code="SCENE_BASELINE_GEOMETRY_CHANGED",
                    message=f"Animation stage changed static baseline geometry for '{obj.id}'.",
                    severity=Severity.MAJOR,
                    target_id=obj.id,
                    evidence={"before_size": before_size, "after_size": after_size, "relative_delta": delta},
                )
            )
    if issues:
        return ValidationReport.failed(
            VerificationMode.DETERMINISTIC,
            issues,
            "Animation stage changed the validated static scene baseline.",
        )
    return ValidationReport.ok(VerificationMode.DETERMINISTIC, "Animation stage preserved static scene baseline geometry.")


def _objects_by_ll3m_id(scene_graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for obj in scene_graph.get("objects", []) if isinstance(scene_graph, dict) else []:
        if not isinstance(obj, dict):
            continue
        ll3m_id = obj.get("ll3m_id")
        if isinstance(ll3m_id, str) and ll3m_id and ll3m_id not in result:
            result[ll3m_id] = obj
    return result


def _bbox_size(obj: dict[str, Any]) -> list[float] | None:
    bbox = obj.get("bbox")
    if not isinstance(bbox, list) or len(bbox) < 2:
        return None
    try:
        xs = [float(point[0]) for point in bbox]
        ys = [float(point[1]) for point in bbox]
        zs = [float(point[2]) for point in bbox]
    except (TypeError, ValueError, IndexError):
        return None
    return [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)]
