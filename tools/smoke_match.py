"""匹配引擎测试（不需要模型）。

覆盖三块：学历判定的软硬区分、技能覆盖的类别兜底、加权分的归一化。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.match import (  # noqa: E402
    candidate_level,
    candidate_years,
    coverage,
    evaluate,
    extract,
    judge,
    parse_requirement,
    project_years,
    resume_pool,
)
from app.repo import resumes as repo  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def main(resume_id: int) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    doc = repo.load_doc(resume_id)
    if doc is None:
        print("找不到简历")
        return 2

    print("—— 学历判定 ——")
    level, label = candidate_level(doc)
    check("从简历识别出学历", level == 3 and "本科" in label, f"{level} {label}")

    cases = [
        ("学历要求：硕士及以上", 4, False),
        ("本科及以上学历，计算机相关专业", 3, False),
        ("硕士优先，本科亦可", 4, True),
        ("学历不限", None, False),
        ("大专以上", 2, False),
    ]
    for text, expect_level, expect_soft in cases:
        got_level, got_soft, snippet = parse_requirement(text)
        check(f"解析「{text}」", (got_level, got_soft) == (expect_level, expect_soft),
              f"得到 {got_level}/{got_soft}「{snippet}」")

    soft_jd = "任职要求：硕士优先，有大型项目经验者优先"
    hard_jd = "任职要求：硕士及以上学历"
    soft = judge(doc, soft_jd)
    hard = judge(doc, hard_jd)
    check("「硕士优先」判为 below 但软性", soft.status == "below" and soft.soft,
          f"{soft.status}/{soft.soft}")
    check("「硕士及以上」判为 below 且硬性", hard.status == "below" and not hard.soft,
          f"{hard.status}/{hard.soft}")
    check("软性不劝退、硬性劝退", "可以投" in soft.note and "大概率被筛" in hard.note,
          f"{soft.note} | {hard.note}")

    print("\n—— 技能覆盖 ——")
    pool = resume_pool(doc)
    check("简历技能池非空", len(pool) >= 5, str(sorted(pool)[:8]))
    check("识别出 Python", "python" in pool, str(sorted(pool)))
    check("识别出 TensorFlow", "tensorflow" in pool, str(sorted(pool)))

    required = extract("熟悉 Python，了解 TensorFlow 或 PyTorch，会用 Docker 与 Kubernetes")
    cov = coverage(required, pool)
    check("Python 命中", "python" in cov["hit"], str(cov))
    check("TensorFlow 命中", "tensorflow" in cov["hit"], str(cov))
    check("Kubernetes 缺失", "kubernetes" in cov["miss"], str(cov))
    check("PyTorch 记为部分命中（同类框架）", "pytorch" in cov["partial"], str(cov))

    print("\n—— 打分 ——")
    jd = """岗位职责：负责算法模型的训练与部署。
任职要求：本科及以上学历，熟悉 Python 与深度学习框架，了解 Docker，有 1 年以上经验。
具备良好的英语读写能力。"""
    result = evaluate(doc, jd)
    check("总分在 0..100", 0 <= result.total <= 100, f"{result.total:.1f}")
    check("维度权重归一化",
          abs(sum(d.weight for d in result.dimensions) - 1.0) < 1e-6,
          str([round(d.weight, 3) for d in result.dimensions]))
    check("学历判定进入结果", result.education is not None and result.education.status == "meet",
          result.education.status if result.education else "none")
    check("有技能维度", any(d.name == "skills" for d in result.dimensions),
          str([d.name for d in result.dimensions]))
    check("有年限维度", any(d.name == "experience" for d in result.dimensions),
          str([d.name for d in result.dimensions]))
    check("差距条目带类型", all(g["type"] in ("未体现", "不具备", "无法判断") for g in result.gaps),
          str([g["type"] for g in result.gaps]))

    # 分类口径必须和覆盖逻辑一致：会 Docker 不等于会 K8s，不能建议「补一下」
    tool_result = evaluate(doc, "任职要求：熟悉 Docker 与 Kubernetes。")
    k8s = next((g for g in tool_result.gaps if g["what"] == "kubernetes"), None)
    check("工具类缺失不误判为「未体现」", k8s is not None and k8s["type"] == "无法判断",
          k8s["type"] if k8s else "无该差距")

    print("\n—— 边界 ——")
    empty = evaluate(doc, "我们是一家有活力的公司，欢迎加入。")
    check("无可量化要求时不乱给分", empty.total == 0.0 and empty.notes, f"{empty.total} {empty.notes}")

    years = candidate_years(doc)
    projects = project_years(doc)
    check("工作年限不含教育经历", years == 0.0, f"{years:.2f} 年")
    check("项目跨度另行计算", projects > 0, f"{projects:.2f} 年")

    fresh = evaluate(doc, "任职要求：本科及以上学历，1 年以上后端开发经验。")
    exp_dim = next((d for d in fresh.dimensions if d.name == "experience"), None)
    check("应届生不会被判为满足 1 年经验", exp_dim is not None and exp_dim.value < 1.0,
          exp_dim.note if exp_dim else "无年限维度")
    check("估算方式在说明里写清楚", exp_dim is not None and "折半估算" in exp_dim.note,
          exp_dim.note if exp_dim else "")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
