"""CLI runner for the local LL3M scene/animation harness."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from typing import Iterator

from .config import HarnessConfig
from .ir import GenerationIR, report_to_dict
from .preflight import format_issue, has_errors, run_preflight
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
        ir.ensure_progressive_stages()
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
    parser.add_argument("--skip-preflight", action="store_true", help="Skip startup checks for Blender, paths, and API keys")
    parser.add_argument("--preflight-only", action="store_true", help="Run startup checks and exit without generating a scene")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = HarnessConfig.from_env()
    runner = HarnessRunner(config)
    try:
        if not args.skip_preflight:
            issues = run_preflight(
                config,
                prompt=args.text,
                ir_path=args.ir,
                include_animation=args.animation,
                skip_vision=args.skip_vision,
                skip_video=args.skip_video,
            )
            for issue in issues:
                stream = sys.stderr if issue.severity == "error" else sys.stdout
                print(format_issue(issue), file=stream)
            if has_errors(issues):
                return 2
            if args.preflight_only:
                print("[Preflight] OK")
                return 0
        elif args.preflight_only:
            print("[Preflight] Skipped")
            return 0
        with _runner_lock(config.runs_dir):
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


@contextmanager
def _runner_lock(runs_dir: Path) -> Iterator[None]:
    runs_dir.mkdir(parents=True, exist_ok=True)
    lock_path = runs_dir / ".harness_runner.lock"
    _clear_stale_lock(lock_path)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        detail = lock_path.read_text(encoding="utf-8", errors="replace") if lock_path.exists() else ""
        raise RuntimeError(
            "Another harness run is already active. Stop it or remove the stale lock before starting a new run. "
            f"Lock: {lock_path}\n{detail}"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "argv": sys.argv}, indent=2))
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _clear_stale_lock(lock_path: Path) -> None:
    if not lock_path.exists():
        return
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0))
    except Exception:
        return
    if pid and _pid_is_alive(pid):
        return
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
