#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from trader_state.models import SharedStateBundle


def main() -> None:
    output_dir = PROJECT_ROOT / "schemas"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / "shared-state-bundle.schema.json"
    output_path.write_text(json.dumps(SharedStateBundle.model_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
