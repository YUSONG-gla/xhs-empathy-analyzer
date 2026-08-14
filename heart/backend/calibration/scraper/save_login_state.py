"""
交互式登录态保存脚本。

只需运行一次：打开真实浏览器窗口，手动扫码/输入账号密码登录小红书，
登录完成后回到终端按回车，脚本会把登录后的 cookie / localStorage
保存到 LOGIN_STATE_PATH，后续 xhs_scraper.py 会自动加载这个登录态，
不再需要每次手动登录。

用法：
    python -m calibration.scraper.save_login_state
"""

from __future__ import annotations

import time

from playwright.sync_api import sync_playwright

from .config import LOGIN_STATE_PATH


def goto_with_retry(page, url: str, retries: int = 3) -> bool:
    """
    带重试的页面跳转。

    XHS 页面持续有后台请求（埋点/心跳），等待默认的 "load" 事件经常会超时，
    用 "domcontentloaded" 作为成功标准即可（能看到页面、能登录就够了）。
    """
    for attempt in range(1, retries + 1):
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] 打开页面失败（第 {attempt}/{retries} 次）：{exc}")
            time.sleep(5 * attempt)
    return False


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        if not goto_with_retry(page, "https://www.xiaohongshu.com"):
            print("[错误] 多次重试后仍无法打开小红书首页，请检查网络后重新运行本脚本。")
            browser.close()
            return

        print("\n" + "=" * 60)
        print("浏览器窗口已打开，请在窗口中完成登录（扫码或账号密码）。")
        print("登录成功、能看到正常首页内容后，回到这个终端按【回车】继续。")
        print("=" * 60 + "\n")
        input("登录完成后按回车保存登录态...")

        context.storage_state(path=LOGIN_STATE_PATH)
        print(f"登录态已保存到：{LOGIN_STATE_PATH}")

        browser.close()


if __name__ == "__main__":
    main()
