"""Run artifact management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json


@dataclass(slots=True)
class ArtifactStore:
    root: Path

    @classmethod
    def create(cls, runs_dir: Path, *, label: str = "run") -> "ArtifactStore":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = runs_dir / f"{label}_{timestamp}"
        path.mkdir(parents=True, exist_ok=True)
        for child in ("code", "reports", "screenshots", "animation", "logs"):
            (path / child).mkdir(exist_ok=True)
        return cls(path)

    def write_text(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, relative: str, data: object) -> Path:
        return self.write_text(relative, json.dumps(data, indent=2, ensure_ascii=False, default=str))

