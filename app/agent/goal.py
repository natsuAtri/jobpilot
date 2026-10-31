"""目标解析：把「找北京的 AI 应用岗位，匹配 80 以上，准备 5 个」拆成结构化参数。

规则解析为主（确定、免费、快），解析失败才不强求——用户可以在过程里看到
解析结果，不对就改一句话重跑。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CITIES = (
    "北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安", "苏州",
    "长沙", "重庆", "天津", "合肥", "厦门", "青岛", "郑州", "济南", "大连", "东莞",
    "佛山", "宁波", "无锡", "福州", "昆明", "南昌", "贵阳", "太原", "石家庄",
    "哈尔滨", "长春", "沈阳", "兰州", "乌鲁木齐", "呼和浩特", "南宁", "海口", "三亚",
)

_STOP_WORDS = (
    "帮我", "找", "搜索", "搜", "岗位", "职位", "匹配", "投递", "投", "准备", "要", "前",
    "个", "的", "以上", "分", "%", "，", ",", "。", "、", "和", "与", "请", "一下",
)


@dataclass
class AgentGoal:
    text: str
    keyword: str
    city: str = ""
    min_score: int = 0     # 0 表示不设分数门槛，由用户显式指定
    count: int = 5
    page: int = 1


# 用户说「和我简历匹配 / 适合我的 / 对口的」时，关键词不该从这句话里抠，
# 而应该从简历内容推导——否则会拿「我简历」三个字去搜招聘网站。
_RESUME_MATCH = re.compile(r"(和.*简历.*(匹配|符合|对口)|简历.*匹配|适合我|符合我|对口|匹配我)")


def wants_resume_match(text: str) -> bool:
    return bool(_RESUME_MATCH.search(text or ""))


def rule_parse(text: str) -> AgentGoal:
    t = (text or "").strip()
    city = next((c for c in CITIES if c in t), "")

    score_match = re.search(r"(\d{2,3})\s*(?:分|以上|%)", t)
    min_score = int(score_match.group(1)) if score_match else 0
    min_score = max(0, min(100, min_score))

    count_match = re.search(r"(?:找|投|准备|要|前|搜|看)\s*(\d{1,2})\s*个", t)
    count = int(count_match.group(1)) if count_match else 5
    count = max(1, min(20, count))

    keyword = t
    for c in CITIES:
        keyword = keyword.replace(c, " ")
    keyword = re.sub(r"\d{2,3}\s*(?:分|以上|%)?", " ", keyword)
    keyword = re.sub(r"\d{1,2}\s*个", " ", keyword)
    for w in _STOP_WORDS:
        keyword = keyword.replace(w, " ")
    keyword = re.sub(r"\s+", " ", keyword).strip()

    return AgentGoal(text=t, keyword=keyword or t, city=city,
                     min_score=min_score, count=count)
