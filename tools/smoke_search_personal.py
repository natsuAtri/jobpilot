"""搜索解析与个人数据库冒烟测试（不依赖网络、不碰真实数据）。

    python tools/smoke_search_personal.py

个人数据库部分用临时 DB 验证，不动 data/app.db。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.models import Bullet, Item, ResumeDoc, Section  # noqa: E402
from app.search.fiftyone import parse_item  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def test_parse_item() -> None:
    """search-pc 接口的一条原始记录 → SearchJob，重点验证 JD 与链接。"""
    raw = {
        "jobId": "174032320",
        "jobName": "上海-python开发",
        "companyName": "某科技",
        "provideSalaryString": "1.5-2万·14薪",
        "jobAreaString": "上海·黄浦区",
        "workYearString": "3年及以上",
        "degreeString": "本科",
        "jobHref": "https://jobs.51job.com/shanghai-hpq/174032320.html",
        "jobDescribe": "【工作职责】负责后端开发【任职要求】熟悉 Python",
        "jobTags": ["五险一金", "本科"],
    }
    job = parse_item(raw)
    check("标题解析", job.title == "上海-python开发")
    check("JD 正文解析", "负责后端开发" in job.description, job.description[:20])
    check("详情链接解析", "jobs.51job.com" in job.url, job.url)
    check("source_id 解析", job.source_id == "174032320")
    check("薪资/地点/年限/学历", job.salary == "1.5-2万·14薪" and job.location == "上海·黄浦区"
          and job.experience == "3年及以上" and job.education == "本科")


def test_personal_db() -> None:
    """个人数据库：用临时 DB 验证从简历构建 + 缺失检测 + 回写。"""
    import app.db as db
    from app.repo import personal

    tmp = Path(tempfile.mkdtemp()) / "test.db"
    original = db.DB_PATH
    db.DB_PATH = tmp
    try:
        db.migrate(db_path=tmp)

        doc = ResumeDoc(
            name="测试",
            phone="13800138000",
            email="t@x.com",
            sections=[
                Section(type="education", items=[
                    Item(org="某大学", role="本科", start="2020.9", end="2024.6")
                ]),
                Section(type="skill", items=[Item(org="Python"), Item(org="MySQL")]),
            ],
        )
        n = personal.build_from_resume(doc)
        check("从简历构建字段", n >= 7, f"{n} 个字段")
        check("姓名/电话/邮箱", personal.get("name") == "测试"
              and personal.get("phone") == "13800138000" and personal.get("email") == "t@x.com")
        check("技能汇总", personal.get("skills") == "Python、MySQL", str(personal.get("skills")))
        check("学历识别", personal.get("edu_degree") == "本科", str(personal.get("edu_degree")))
        missing = personal.missing_keys()
        check("缺失字段可枚举", "city" in missing, str(missing[:5]))
        personal.set_value("city", "西安")
        check("回写后缺失减少", "city" not in personal.missing_keys())
    finally:
        db.DB_PATH = original


def test_job_import_dedupe() -> None:
    """重复导入同一岗位不能崩（UNIQUE dedupe_key 约束）。"""
    import app.db as db
    from app.repo import jobs as job_repo

    tmp = Path(tempfile.mkdtemp()) / "test.db"
    original = db.DB_PATH
    db.DB_PATH = tmp
    try:
        db.migrate(db_path=tmp)
        first = job_repo.create_job(
            title="重复岗位", company="某公司", description="【任职要求】Python",
            external_id="111", dedupe_key="51job:111",
        )
        second = job_repo.create_job(
            title="重复岗位", company="某公司", description="【任职要求】Python",
            external_id="111", dedupe_key="51job:111",
        )
        check("首次导入成功", first > 0, str(first))
        check("重复导入复用同一 id、不崩溃", second == first, f"{first} vs {second}")
        check("库中只有一条", len(job_repo.list_jobs()) == 1)
    finally:
        db.DB_PATH = original


def test_jd_requirement_filter() -> None:
    """「岗位职责/福利」等非要求段落要过滤，且不依赖【】标记。"""
    from app.rag.chunk import requirement_text

    jd = """一、岗位职责
1、参与AI大模型与AI算法在业务场景中的落地。
2、解决产品实现中的技术难题。

二、任职要求
1、熟悉Python，掌握Django/Flask。
2、熟悉MySQL数据库。

三、福利待遇
五险一金、年终奖。"""
    req = requirement_text(jd)
    check("编号标题：岗位职责被过滤", "参与AI大模型" not in req, req[:30])
    check("编号标题：任职要求保留", "熟悉Python" in req)
    check("编号标题：福利被过滤", "五险一金" not in req)

    jd2 = "【团队与项目介绍】\n我们是一个年轻团队。\n【技术栈】\n1、掌握 Python。"
    req2 = requirement_text(jd2)
    check("【】标记：介绍被过滤", "年轻团队" not in req2)
    check("【】标记：技术栈保留", "掌握 Python" in req2)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    test_parse_item()
    test_jd_requirement_filter()
    test_personal_db()
    test_job_import_dedupe()
    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
