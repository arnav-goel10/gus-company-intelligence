from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "run"


class RunStorage:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def create_run_dir(self, company_name: str) -> Path:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
        base_name = f"{timestamp}-{slugify(company_name)}"
        for suffix in ["", "-1", "-2", "-3", "-4", "-5"]:
            run_dir = self.base_dir / f"{base_name}{suffix}"
            try:
                run_dir.mkdir(parents=True, exist_ok=False)
                return run_dir
            except FileExistsError:
                continue
        raise RuntimeError(
            f"Could not create a unique run directory for company '{company_name}'."
        )

    def write_json(self, run_dir: Path, relative_path: str, payload: Any) -> Path:
        target = run_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return target
