"""M2 对话补录冒烟测试（用替身模型，不需要 API Key）。

    RA_LLM_STUB=1 python tools/smoke_dialogue.py 3

验证：开场问题来自缺口 → 说一句话 → 产出 patch 但不落库 →
确认后写入 → 简历真的多了内容。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("RA_LLM_STUB", "1")

from app.db import get_conn, migrate  # noqa: E402
from app.dialogue import engine, store  # noqa: E402
from app.dialogue.gaps import analyze  # noqa: E402
from app.repo import resumes as repo  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


async def main(resume_id: int) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    migrate()

    # 每次从干净会话开始，保证可重复运行
    with get_conn() as conn:
        conn.execute("DELETE FROM dialogue_session WHERE resume_id = ?", (resume_id,))

    doc_before = repo.load_doc(resume_id)
    if doc_before is None:
        print("找不到简历")
        return 2

    gaps = analyze(doc_before)
    check("缺口分析能发现问题", len(gaps.gaps) > 0, f"{len(gaps.gaps)} 条")

    session = engine.ensure_session(resume_id)
    turns = store.list_turns(int(session["id"]))
    check("会话开场有提问", len(turns) == 1 and turns[0]["role"] == "assistant",
          f"{len(turns)} 轮")
    check("开场问题就是最优先的缺口",
          turns[0]["content"] == gaps.top.question if gaps.top else True,
          turns[0]["content"][:30])

    bullets_before = doc_before.bullet_count()

    # 说一句话：应产出 patch，但此时还没写入
    await engine.say(int(session["id"]), "手语项目里我负责数据集标注和模型训练")
    pending = engine.pending_turn(int(session["id"]))
    check("产出待确认的 patch", pending is not None,
          f"turn={pending['id'] if pending else 'none'}")
    if pending:
        check("patch 数量 >= 1", len(pending["patches"]) >= 1, str(len(pending["patches"])))
        doc_mid = repo.load_doc(resume_id)
        check("确认前文档未被修改", doc_mid.bullet_count() == bullets_before,
              f"{doc_mid.bullet_count()} vs {bullets_before}")

        applied, rejected = engine.accept(int(session["id"]), pending["id"])
        check("确认后写入成功", bool(applied), f"applied={applied} rejected={rejected}")
        doc_after = repo.load_doc(resume_id)
        check("确认后文档确有变化", doc_after.bullet_count() > bullets_before,
              f"{doc_after.bullet_count()} vs {bullets_before}")

        new_turn = store.get_turn(pending["id"])
        check("轮次被标记为已接受", new_turn["accepted"] == 1, str(new_turn["accepted"]))

    # 城市类信息走 set_profile
    await engine.say(int(session["id"]), "我在西安找工作")
    pending2 = engine.pending_turn(int(session["id"]))
    if pending2:
        engine.accept(int(session["id"]), pending2["id"])
        doc_city = repo.load_doc(resume_id)
        check("基本信息可通过对话写入", doc_city.location == "西安", str(doc_city.location))

    # 放弃路径：再产出一条建议，然后拒绝它
    await engine.say(int(session["id"]), "另外我还负责了模型部署和接口联调")
    pending3 = engine.pending_turn(int(session["id"]))
    check("放弃路径有可拒绝的建议", pending3 is not None,
          f"turn={pending3['id'] if pending3 else 'none'}")
    if pending3:
        before_reject = repo.load_doc(resume_id).bullet_count()
        engine.reject(pending3["id"])
        check("放弃后状态记为已拒绝", store.get_turn(pending3["id"])["accepted"] == 0)
        check("放弃后文档没有变化",
              repo.load_doc(resume_id).bullet_count() == before_reject,
              str(before_reject))

    # 收尾：清掉测试写入的对话内容与城市，保持数据干净
    final = repo.load_doc(resume_id)
    for section in final.sections:
        for item in section.items:
            item.bullets = [b for b in item.bullets if b.source != "chat"]
    final.location = None
    repo.replace_doc(resume_id, final)
    repo.update_profile(resume_id, final)
    with get_conn() as conn:
        conn.execute("DELETE FROM dialogue_session WHERE resume_id = ?", (resume_id,))

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)))
