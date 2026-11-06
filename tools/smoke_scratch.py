"""验收测试：从零创建 → 纯对话补出内容 → 导出 PDF 并通过回读校验。

对应设计文档 M2 的验收条件：「清空简历后能纯靠对话补出完整一份并导出」。
用替身模型跑通链路；真实模型下的文案质量另说。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("RA_LLM_STUB", "1")

from app.db import migrate  # noqa: E402
from app.dialogue import engine, store  # noqa: E402
from app.models import ResumeDoc  # noqa: E402
from app.render import expected_fragments, render_html, verify_pdf  # noqa: E402
from app.render.browser import print_to_pdf  # noqa: E402
from app.repo import resumes as repo  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    migrate()

    with repo.get_conn() as conn:
        row = conn.execute("SELECT name, phone FROM profile ORDER BY id LIMIT 1").fetchone()
    before_name = row["name"] if row else ""
    before_phone = row["phone"] if row else None

    doc = ResumeDoc()
    resume_id = repo.create_resume(doc, kind="generated", title="从零创建的简历")
    check("能建出空简历", resume_id > 0, f"id={resume_id}")

    with repo.get_conn() as conn:
        row = conn.execute("SELECT name, phone FROM profile ORDER BY id LIMIT 1").fetchone()
    check("创建空简历不会抹掉主档案", row["name"] == before_name and row["phone"] == before_phone,
          f"{row['name']!r} / {row['phone']!r}")

    blank = repo.load_doc(resume_id)
    check("空简历确实没有任何章节", not blank.sections, f"{len(blank.sections)} 个章节")

    session = engine.ensure_session(resume_id, "scratch")
    turns = store.list_turns(int(session["id"]))
    check("从零模式有开场问题", len(turns) == 1, f"{len(turns)} 轮")
    check("开场问题指向教育背景或基本信息",
          any(k in turns[0]["content"] for k in ("教育", "称呼", "手机", "邮箱")),
          turns[0]["content"][:30])

    await engine.say(int(session["id"]), "我在北京找工作，负责后端服务开发")
    pending = engine.pending_turn(int(session["id"]))
    check("对话能产出内容", pending is not None and len(pending["patches"]) > 0,
          f"{len(pending['patches']) if pending else 0} 条 patch")

    if pending:
        applied, rejected = engine.accept(int(session["id"]), pending["id"])
        check("确认后写入成功", bool(applied), f"{applied} / {rejected}")

    filled = repo.load_doc(resume_id)
    check("空简历被对话填上了内容", filled.section("project") is not None,
          str([s.type for s in filled.sections]))
    check("基本信息也被写入", filled.location == "北京", str(filled.location))

    # 从零建出来的简历也必须能正常导出
    html = render_html(filled, theme="ink")
    out = BASE / "data" / "export" / "scratch.pdf"
    print_to_pdf(html, out)
    result = verify_pdf(out, expected_fragments(filled))
    check("从零简历可导出 PDF", result.ok, result.message)

    with repo.get_conn() as conn:
        conn.execute("DELETE FROM resume WHERE id = ?", (resume_id,))
        # 合并语义下 update_profile 清不掉 location，测试写进去的「北京」要用 SQL 还原
        conn.execute("UPDATE profile SET location = NULL WHERE location = '北京'")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
