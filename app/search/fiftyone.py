"""前程无忧（51job）搜索适配器 —— API 版。

搜索列表页 `we.51job.com/pc/search` 的列表数据其实来自
`we.51job.com/api/job/search-pc` 这个 JSON 接口，它直接返回每个岗位的
完整 JD（jobDescribe）和详情链接（jobHref）——比抓 DOM 或抓详情页
（详情页有滑动验证）都更稳、信息更全。

做法：用登录态的持久化 profile 打开搜索页，监听并截获 search-pc 响应，
解析 JSON。不抓 DOM、不碰详情页、不触发验证码。
"""
from __future__ import annotations

import json
from typing import Callable

from ..config import DATA_DIR
from .model import SearchJob

Progress = Callable[[str], None]

SEARCH_URL = "https://we.51job.com/pc/search?jobArea={city}&keyword={keyword}"
PROFILE_DIR = DATA_DIR / "browser-profile"


def _noop(_: str) -> None:
    pass


def _launch_context(p, *, headless: bool = True):
    """优先用登录态 profile（先登录再搜索，数据更全），没有则用普通上下文。"""
    if PROFILE_DIR.is_dir():
        return p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), channel="chrome", headless=headless
        )
    return p.chromium.launch(channel="chrome", headless=headless).new_context()


def parse_item(item: dict) -> SearchJob:
    """把 search-pc 接口返回的一条 job 记录转成 SearchJob。"""
    return SearchJob(
        title=item.get("jobName") or "",
        company=item.get("companyName") or "",
        salary=item.get("provideSalaryString") or "",
        location=item.get("jobAreaString") or "",
        experience=item.get("workYearString") or "",
        education=item.get("degreeString") or "",
        url=item.get("jobHref") or "",
        description=item.get("jobDescribe") or "",
        source_id=str(item.get("jobId") or ""),
        tags=item.get("jobTags") or [],
    )


def search(keyword: str, city_code: str = "000000", limit: int = 30,
           progress: Progress = _noop, headless: bool = True, pages: int = 1) -> list[SearchJob]:
    from playwright.sync_api import sync_playwright

    progress("启动浏览器")
    captured: dict = {}

    def on_response(resp):
        if "search-pc" in resp.url:
            try:
                captured["data"] = resp.json()
            except (json.JSONDecodeError, ValueError):
                pass

    def items_of(data: dict) -> list[dict]:
        return ((data or {}).get("resultbody") or {}).get("job", {}).get("items") or []

    jobs: list[SearchJob] = []
    with sync_playwright() as p:
        context = _launch_context(p, headless=headless)
        page = context.pages[0] if context.pages else context.new_page()
        page.on("response", on_response)
        progress("打开搜索页")
        page.goto(
            SEARCH_URL.format(city=city_code, keyword=keyword),
            timeout=40000,
            wait_until="domcontentloaded",
        )
        # 等 search-pc 响应返回
        for _ in range(30):
            if "data" in captured:
                break
            page.wait_for_timeout(500)

        collected = items_of(captured.get("data", {}))
        progress(f"第 1 页 {len(collected)} 个职位")

        # 翻页：点下一页会触发新的 search-pc 请求（pageNum 跟着变）
        for page_no in range(2, max(1, pages) + 1):
            try:
                with page.expect_response(
                    lambda r: "search-pc" in r.url, timeout=20000
                ) as info:
                    page.click(".btn-next")
                more = items_of(info.value.json())
            except Exception:  # noqa: BLE001 - 没有下一页/点击失败就停
                break
            if not more:
                break
            collected.extend(more)
            progress(f"第 {page_no} 页 {len(more)} 个职位")

        progress(f"共抓到 {len(collected)} 个职位（含完整 JD）")
        for item in collected[:limit]:
            jobs.append(parse_item(item))
        progress(f"解析完成，共 {len(jobs)} 个")
        context.close()
    return jobs
