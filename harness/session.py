"""Reusable interactive harness session for CLI and TUI frontends."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agents import CoderAgent, PlannerAgent, RefinerAgent, VideoVerifierAgent, VisionVerifierAgent
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
        self.coder = CoderAgent(config, self.rag)
        self.refiner = RefinerAgent(config, self.rag)
        self.vision = VisionVerifierAgent(config)
        self.video = VideoVerifierAgent(config)
        self.blender = BlenderRuntime(config)
        self.store: ArtifactStore | None = None
        self.ir: GenerationIR | None = None
        self.code: str | None = None
        self.turn_index = 0
        self._latest_screenshots: list[Path] = []

    @property
    def has_scene(self) -> bool:
        return self.ir is not None and self.code is not None

    def start(self, prompt: str) -> Path:
        self.store = ArtifactStore.create(self.config.runs_dir)
        self.turn_index = 0
        self._emit("session", f"Run directory: {self.store.root}", path=str(self.store.root))

        self._emit("planner", "Planning structured IR")
        self.ir = self.planner.plan(prompt, include_animation=self.include_animation)
        self.store.write_json("ir.json", self.ir.to_dict())
        self._emit("planner", f"Planned {len(self.ir.scene.objects)} objects")

        self._emit("coder", "Generating Blender script")
        self.code = self.coder.generate(self.ir)
        self.store.write_text("code/generated_scene.py", self.code)

        self._execute_validate_refine(reason="initial")
        return self.store.root

    def start_from_ir(self, ir: GenerationIR) -> Path:
        self.store = ArtifactStore.create(self.config.runs_dir)
        self.turn_index = 0
        self.ir = ir
        self._emit("session", f"Run directory: {self.store.root}", path=str(self.store.root))
        self.store.write_json("ir.json", self.ir.to_dict())
        self._emit("coder", "Generating Blender script from provided IR")
        self.code = self.coder.generate(self.ir)
        self.store.write_text("code/generated_scene.py", self.code)
        self._execute_validate_refine(reason="initial")
        return self.store.root

    def apply_user_request(self, request: str) -> Path:
        if not self.has_scene or not self.store or not self.ir or not self.code:
            return self.start(request)

        self.turn_index += 1
        self._emit("user", request)

        self._emit("planner", "Revising IR from user request")
        self.ir = self.planner.revise(self.ir, request, include_animation=self.include_animation)
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

    def _execute_validate_refine(self, *, reason: str) -> None:
        if not self.store or not self.ir or self.code is None:
            raise RuntimeError("Session has not been initialized.")

        reports: list[ValidationReport] = []
        execution_error: str | None = None
        max_rounds = self._max_refinement_rounds()
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
                    self.store.write_text("code/final_scene.py", self.code)
                    return
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
                reports = self._run_validation_pass(label)

            if all(report.passed for report in reports):
                self._emit("pass", "All enabled validation stages passed")
                self.store.write_text("code/final_scene.py", self.code)
                self._render_final_animation_gif()
                return

            if round_index >= max_rounds:
                self._emit("warn", "Verifier loop stopped at safety cap before all stages passed")
                self.store.write_text("code/final_scene.py", self.code)
                return

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
        if self.ir.animation:
            keyframe_calls = _count_effective_keyframe_calls(tree)
            if len(keyframe_calls) < max(2, len(self.ir.animation.events)):
                issues.append(
                    ValidationIssue(
                        code="CODE_MISSING_ANIMATION_KEYFRAMES",
                        message="Animation script has too few actual keyframe_insert calls for the planned events.",
                        severity=Severity.CRITICAL,
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
                self._emit("render", "Rendering animation sampled frames")
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
                self._emit("video", f"Running video verifier on {len(sampled_frames)} frames")
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
            render_gif=True,
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
