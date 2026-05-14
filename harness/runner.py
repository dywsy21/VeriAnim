"""CLI runner for the local LL3M scene/animation harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .agents import CoderAgent, PlannerAgent, RefinerAgent, VideoVerifierAgent, VisionVerifierAgent
from .artifacts import ArtifactStore
from .blender_runtime import BlenderRuntime
from .config import HarnessConfig
from .ir import GenerationIR, ValidationIssue, ValidationReport, VerificationMode, report_to_dict
from .rag import LocalRAG
from .serde import from_dict


class HarnessRunner:
    def __init__(self, config: HarnessConfig):
        self.config = config
        self.rag = LocalRAG(config.rag_docs)
        self.planner = PlannerAgent(config, self.rag)
        self.coder = CoderAgent(config, self.rag)
        self.refiner = RefinerAgent(config, self.rag)
        self.vision = VisionVerifierAgent(config)
        self.video = VideoVerifierAgent(config)
        self.blender = BlenderRuntime(config)

    def run(
        self,
        *,
        prompt: str | None,
        ir_path: Path | None = None,
        include_animation: bool = False,
        skip_vision: bool = False,
        skip_video: bool = False,
    ) -> Path:
        store = ArtifactStore.create(self.config.runs_dir)
        print(f"[Harness] Run directory: {store.root}")

        ir = self._load_or_plan_ir(prompt, ir_path, include_animation)
        store.write_json("ir.json", ir.to_dict())

        print("[Harness] Generating Blender script...")
        code = self.coder.generate(ir)
        store.write_text("code/generated_scene.py", code)

        reports: list[ValidationReport] = []
        execution_error: str | None = None

        for round_index in range(self.config.max_refinement_rounds + 1):
            print(f"[Harness] Executing Blender script (round {round_index})...")
            execution = self.blender.execute_code(code)
            store.write_text(f"logs/execution_round_{round_index}.txt", execution.stdout)
            if not execution.ok:
                execution_error = execution.message or execution.stdout
                reports = [
                    ValidationReport.failed(
                        VerificationMode.DETERMINISTIC,
                        [ValidationIssue(code="BLENDER_EXEC_FAILED", message=execution_error)],
                    )
                ]
            else:
                execution_error = None
                print("[Harness] Running deterministic scene validation...")
                scene_report = self.blender.validate_scene(ir)
                reports = [scene_report]
                store.write_json(f"reports/scene_deterministic_round_{round_index}.json", report_to_dict(scene_report))

                screenshots = self.blender.render_screenshots(ir, store.root / "screenshots" / f"round_{round_index}")
                if screenshots and not skip_vision and ir.scene.verifier.visual.enabled:
                    print(f"[Harness] Running vision verification on {len(screenshots)} screenshots...")
                    vision_report = self.vision.verify(ir, screenshots, scene_report)
                    reports.append(vision_report)
                    store.write_json(f"reports/scene_vision_round_{round_index}.json", report_to_dict(vision_report))

                if ir.animation:
                    print("[Harness] Running deterministic animation validation...")
                    anim_report, transform_trace = self.blender.validate_animation(ir)
                    reports.append(anim_report)
                    store.write_json(f"reports/animation_deterministic_round_{round_index}.json", report_to_dict(anim_report))
                    store.write_json(f"reports/animation_trace_round_{round_index}.json", transform_trace)

                    if not skip_video and ir.animation.verifier.enabled:
                        sampled_frames, preview_video = self.blender.render_animation_samples(
                            ir, store.root / "animation" / f"round_{round_index}"
                        )
                        print(f"[Harness] Running video verifier on {len(sampled_frames)} sampled frames...")
                        video_report = self.video.verify(ir, sampled_frames, preview_video, anim_report, transform_trace)
                        reports.append(video_report)
                        store.write_json(f"reports/animation_video_round_{round_index}.json", report_to_dict(video_report))

            if all(report.passed for report in reports):
                print("[Harness] All enabled validation stages passed.")
                store.write_text("code/final_scene.py", code)
                return store.root

            if round_index >= self.config.max_refinement_rounds:
                print("[Harness] Max refinement rounds reached.")
                store.write_text("code/final_scene.py", code)
                return store.root

            print("[Harness] Refining script from validation feedback...")
            code = self.refiner.refine(ir=ir, code=code, reports=reports, execution_error=execution_error)
            store.write_text(f"code/refined_round_{round_index + 1}.py", code)

        return store.root

    def _load_or_plan_ir(self, prompt: str | None, ir_path: Path | None, include_animation: bool) -> GenerationIR:
        if ir_path:
            print(f"[Harness] Loading IR: {ir_path}")
            data = json.loads(ir_path.read_text(encoding="utf-8"))
            ir = from_dict(GenerationIR, data)
            report = ir.validate()
            if not report.passed:
                raise ValueError(f"Invalid IR file: {report_to_dict(report)}")
            return ir
        if not prompt:
            raise ValueError("Either --text or --ir must be provided.")
        print("[Harness] Planning structured IR...")
        return self.planner.plan(prompt, include_animation=include_animation)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local LL3M Blender scene/animation harness")
    parser.add_argument("--text", type=str, help="User prompt for scene or animation generation")
    parser.add_argument("--ir", type=Path, help="Path to an existing GenerationIR JSON file")
    parser.add_argument("--animation", action="store_true", help="Ask planner to include animation")
    parser.add_argument("--skip-vision", action="store_true", help="Disable visual model verification")
    parser.add_argument("--skip-video", action="store_true", help="Disable video/temporal model verification")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = HarnessConfig.from_env()
    runner = HarnessRunner(config)
    try:
        output_dir = runner.run(
            prompt=args.text,
            ir_path=args.ir,
            include_animation=args.animation,
            skip_vision=args.skip_vision,
            skip_video=args.skip_video,
        )
        print(f"[Harness] Finished. Artifacts: {output_dir}")
        return 0
    except Exception as exc:
        print(f"[Harness] Failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
