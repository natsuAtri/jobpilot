"""调试：规则抽取结果。

    python tools/dump_rules.py samples/简历.pdf
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parse import extract_pages, extract_rules  # noqa: E402


def main(path: str) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    pages = extract_pages(path)
    doc = extract_rules(pages)
    print(json.dumps(json.loads(doc.model_dump_json()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
