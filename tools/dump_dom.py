"""抓取已登录页面（BOSS直聘等）的表单控件结构，供开发真实选择器。

用法（先用「登录 BOSS直聘」完成登录，关掉登录窗口）：
    python tools/dump_dom.py "https://www.zhipin.com/job_detail/xxxx.html"

会打印页面标题和正文片段用于诊断：如果被反爬拦下（空页/登录墙），
能看到具体返回什么，再决定下一步。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.config import DATA_DIR  # noqa: E402

STEALTH = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""


def dump(url: str) -> None:
    from playwright.sync_api import sync_playwright

    profile = str(DATA_DIR / "browser-profile")
    out_path = DATA_DIR / "boss-dom.txt"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context.add_init_script(STEALTH)
        page = context.pages[0] if context.pages else context.new_page()

        print("正在打开…", flush=True)
        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)

        print(f"最终 URL: {page.url}")
        title = page.title()
        print(f"标题: {title[:80]}")
        body_text = page.inner_text("body")[:200].replace("\n", " ")
        print(f"正文片段: {body_text}")

        lines: list[str] = [f"# URL: {page.url}", f"# title: {title}"]
        for kind, nodes in (
            ("button", page.query_selector_all("button")),
            ("input", page.query_selector_all("input")),
            ("a", page.query_selector_all("a")),
            ("textarea", page.query_selector_all("textarea")),
        ):
            for node in nodes:
                try:
                    text = (node.inner_text() or "").strip().replace("\n", " ")
                    cls = (node.get_attribute("class") or "").strip()
                    name = (node.get_attribute("name") or "").strip()
                    href = (node.get_attribute("href") or "").strip()
                except Exception:  # noqa: BLE001
                    continue
                if text or cls or name:
                    tag = kind
                    if name:
                        tag += f" name={name}"
                    if text:
                        tag += f" text={text[:40]}"
                    if href:
                        tag += f" href={href[:60]}"
                    if cls:
                        tag += f" class={cls[:60]}"
                    lines.append(tag)

        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"控件数: {len(lines) - 2}，已写入 {out_path}")
        context.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python tools/dump_dom.py <URL>")
        raise SystemExit(1)
    dump(sys.argv[1])
