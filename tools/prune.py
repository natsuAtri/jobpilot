"""手动清理老数据。

    python tools/prune.py            # 实际删除
    python tools/prune.py --dry-run  # 只看会删多少

策略见 app/repo/maintenance.py：trace 留 7 天 / 5000 行，每会话留 200 条消息。
启动时也会自动跑一次（后台线程）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.db import get_conn  # noqa: E402
from app.repo import maintenance  # noqa: E402


def counts() -> dict[str, int]:
    with get_conn() as conn:
        return {
            "agent_trace": conn.execute("SELECT COUNT(*) n FROM agent_trace").fetchone()["n"],
            "conversation_message": conn.execute(
                "SELECT COUNT(*) n FROM conversation_message").fetchone()["n"],
            "generation_step": conn.execute(
                "SELECT COUNT(*) n FROM generation_step").fetchone()["n"],
        }


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    before = counts()
    print("清理前：", before)
    if dry:
        print("（--dry-run，不做修改）")
        return 0
    removed = maintenance.prune_all()
    after = counts()
    print("删除：", removed)
    print("清理后：", after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
