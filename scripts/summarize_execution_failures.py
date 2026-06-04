"""Summarize Blender execution failures from VeriAnim run artifacts."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize VeriAnim Blender execution failure reports.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"), help="Directory containing run_* folders.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of text.")
    parser.add_argument("--max-examples", type=int, default=5, help="Maximum examples per failure class.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = summarize_execution_failures(args.runs_dir, max_examples=args.max_examples)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(format_summary(summary))
    return 0


def summarize_execution_failures(runs_dir: Path, *, max_examples: int = 5) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    report_count = 0
    failed_count = 0
    for report_path in sorted(runs_dir.glob("run_*/reports/*_execution.json")):
        report_count += 1
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            category = "READ_ERROR"
            counts[category] += 1
            failed_count += 1
            _append_example(examples, category, max_examples, report_path, str(exc), None)
            continue
        if report.get("passed") is True:
            continue
        failed_count += 1
        issues = report.get("issues") or []
        if not issues:
            category = "UNKNOWN_EXECUTION_FAILURE"
            counts[category] += 1
            _append_example(examples, category, max_examples, report_path, "", _find_code_path(report_path))
            continue
        for issue in issues:
            if not isinstance(issue, dict):
                message = str(issue)
                category = classify_message(message)
            else:
                message = str(issue.get("message") or issue.get("details") or "")
                category = classify_message(message)
            counts[category] += 1
            _append_example(examples, category, max_examples, report_path, message, _find_code_path(report_path))
    return {
        "runs_dir": str(runs_dir),
        "execution_report_count": report_count,
        "failed_report_count": failed_count,
        "categories": [
            {"category": category, "count": count, "examples": examples.get(category, [])}
            for category, count in counts.most_common()
        ],
    }


def classify_message(message: str) -> str:
    text = message.lower()
    if "unexpected keyword argument 'scale'" in text and "add_cube" in text:
        return "HELPER_ADD_CUBE_SCALE_KEYWORD"
    if "unexpected keyword argument 'scale'" in text and "add_plane" in text:
        return "HELPER_ADD_PLANE_SCALE_KEYWORD"
    if "unexpected keyword argument 'rotation'" in text and "add_plane" in text:
        return "HELPER_ADD_PLANE_ROTATION_KEYWORD"
    if "unexpected keyword argument 'spec_dict'" in text and "make_material" in text:
        return "HELPER_MAKE_MATERIAL_SPEC_DICT_KEYWORD"
    if "cannot import name 'llm_utils'" in text:
        return "HELPER_LLM_UTILS_IMPORT_ALIAS"
    if "'action' object has no attribute 'fcurves'" in text:
        return "ACTION_FCURVES_LAYERED_API"
    if "enum \"ease_in_out\" not found" in text:
        return "INVALID_KEYFRAME_INTERPOLATION_ENUM"
    if "wave" in text and "falloff" in text:
        return "WAVE_MODIFIER_FALLOFF"
    if "mathutils.vector()" in text:
        return "MATHUTILS_VECTOR_CALL"
    if "only strings are allowed as keys of id properties" in text:
        return "OBJECT_AS_SEQUENCE_OR_ID_PROPERTY_KEY"
    return "BLENDER_EXEC_FAILED_OTHER"


def _append_example(
    examples: dict[str, list[dict[str, Any]]],
    category: str,
    max_examples: int,
    report_path: Path,
    message: str,
    code_path: Path | None,
) -> None:
    if len(examples[category]) >= max_examples:
        return
    examples[category].append(
        {
            "report": str(report_path),
            "message": message[:240],
            "code": str(code_path) if code_path else None,
        }
    )


def _find_code_path(report_path: Path) -> Path | None:
    run_dir = report_path.parents[1]
    stem = report_path.stem
    if stem.endswith("_execution"):
        stage = stem[: -len("_execution")]
    else:
        stage = stem
    candidates = []
    if stage.startswith("scene_stage_round_0"):
        candidates.append(run_dir / "code" / "generated_scene_stage.py")
    if stage.startswith("animation_stage_round_0"):
        candidates.append(run_dir / "code" / "generated_animation_stage.py")
    if stage.startswith("initial_round_0"):
        candidates.append(run_dir / "code" / "generated_scene.py")
    candidates.extend(
        [
            run_dir / "code" / f"{stage}.py",
            run_dir / "code" / f"{stage}_refined.py",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def format_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"runs_dir={summary['runs_dir']}",
        f"execution_report_count={summary['execution_report_count']}",
        f"failed_report_count={summary['failed_report_count']}",
        "",
    ]
    for item in summary["categories"]:
        lines.append(f"{item['category']}: {item['count']}")
        for example in item.get("examples", []):
            lines.append(f"  report: {example['report']}")
            if example.get("code"):
                lines.append(f"  code:   {example['code']}")
            if example.get("message"):
                lines.append(f"  msg:    {example['message']}")
        lines.append("")
    return "\n".join(lines).rstrip()


if __name__ == "__main__":
    raise SystemExit(main())
