"""调试工具：把 PDF 解析成视觉行并打印，用于对照原文件人工核对。

    python tools/dump_parse.py samples/简历.pdf
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parse.pdf import extract, to_prompt_text  # noqa: E402


def main(path: str) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    pages = extract(path)
    print(f"页数 {len(pages)}")
    for page in pages:
        print(f"===== page {page.index + 1} {page.width:.0f}x{page.height:.0f} =====")
        for line in page.lines:
            segs = "  ||  ".join(f"[x{s.x0:.0f}] {s.text}" for s in line.segments)
            print(f"y{line.y:6.1f} s{line.size:5.1f} | {segs}")
    if "--prompt" in sys.argv:
        print("\n===== LLM 输入 =====")
        print(to_prompt_text(pages))


if __name__ == "__main__":
    main(sys.argv[1])
