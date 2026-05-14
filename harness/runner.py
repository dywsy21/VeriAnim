"""CLI runner for the local LL3M scene/animation harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .config import HarnessConfig
from .ir import GenerationIR, report_to_dict
from .serde import from_dict
from .session import HarnessEvent, InteractiveHarnessSession


class HarnessRunner:
    def __init__(self, config: HarnessConfig):
        self.config = config

    def run(
        self,
        *,
        prompt: str | None,
        ir_path: Path | None = None,
        include_animation: bool = False,
        skip_vision: bool = False,
        skip_video: bool = False,
    ) -> Path:
        session = InteractiveHarnessSession(
            self.config,
            include_animation=include_animation,
            skip_vision=skip_vision,
            skip_video=skip_video,
            callback=_print_event,
        )
        if ir_path:
            ir = self._load_ir(ir_path)
            return session.start_from_ir(ir)
        if not prompt:
            raise ValueError("Either --text or --ir must be provided.")
        return session.start(prompt or "")

    def _load_ir(self, ir_path: Path) -> GenerationIR:
        print(f"[Harness] Loading IR: {ir_path}")
        data = json.loads(ir_path.read_text(encoding="utf-8"))
        ir = from_dict(GenerationIR, data)
        report = ir.validate()
        if not report.passed:
            raise ValueError(f"Invalid IR file: {report_to_dict(report)}")
        return ir


def _print_event(event: HarnessEvent) -> None:
    print(f"[{event.kind}] {event.message}")


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
