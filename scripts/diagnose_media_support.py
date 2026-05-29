"""Run vision/video media capability diagnostics through existing verifier agents."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.config import HarnessConfig
from harness.media_diagnostics import run_media_capability_diagnostics, write_probe_assets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs/media_capability_probe"),
        help="Directory for probe assets and media_capability_report.json.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    assets = write_probe_assets(output_dir)
    report = {
        "assets": assets,
        "diagnostics": run_media_capability_diagnostics(
            HarnessConfig.from_env(),
            image_path=Path(assets["image_path"]),
            video_path=Path(assets["video_path"]),
            sampled_frame_paths=[Path(path) for path in assets["sampled_frame_paths"]],
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "media_capability_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), **report}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
