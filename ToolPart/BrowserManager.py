import asyncio
from typing import Optional
from pydoll.browser import Chrome
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions
from pydoll.constants import PageLoadState


class BrowserManager:
    """浏览器管理器，处理所有浏览器相关操作，包括验证码绕过"""

    def __init__(self, headless: bool = True, download_dir: str = "./downloads"):
        self.headless = headless
        self.download_dir = download_dir
        self._browser: Optional[Chrome] = None
        self._tab = None

    def _create_chrome_options(self) -> ChromiumOptions:
        """创建最小化的Chrome配置选项"""
        options = ChromiumOptions()

        # 最小化配置，只保留必要的
        options.headless = self.headless
        options.start_timeout = 30

        # 使用最少的命令行参数
        options.add_argument('--disable-blink-features=AutomationControlled')

        # 如果有headless模式，添加窗口大小
        if self.headless:
            options.add_argument('--window-size=1920,1080')

        # 设置下载目录
        if self.download_dir:
            options.set_default_download_directory(self.download_dir)

        return options

    async def start_browser(self):
        """启动浏览器"""
        if self._browser is None:
            options = self._create_chrome_options()
            self._browser = Chrome(options=options)
            await self._browser.__aenter__()
            self._tab = await self._browser.start()
        return self._tab

    async def close_browser(self):
        """关闭浏览器"""
        if self._browser is not None:
            await self._browser.__aexit__(None, None, None)
            self._browser = None
            self._tab = None

    async def go_to_with_captcha_bypass(self, url: str):
        """访问URL并自动绕过Cloudflare Turnstile验证码"""
        if self._tab is None:
            await self.start_browser()

        # 使用验证码绕过上下文管理器
        async with self._tab.expect_and_bypass_cloudflare_captcha():
            await self._tab.go_to(url)
            print(f"已访问 {url}，Cloudflare Turnstile自动解决！")

        return self._tab

    async def go_to(self, url: str):
        """访问URL（不包含验证码绕过）"""
        if self._tab is None:
            await self.start_browser()

        await self._tab.go_to(url)
        return self._tab

    async def query_element(self, xpath: str, timeout: int = 5, raise_exc: bool = False):
        """查询元素"""
        if self._tab is None:
            await self.start_browser()

        return await self._tab.query(xpath, timeout=timeout, raise_exc=raise_exc)

    async def find_element(self, **kwargs):
        """查找元素"""
        if self._tab is None:
            await self.start_browser()

        return await self._tab.find(**kwargs)

    async def execute_script(self, script: str):
        """执行JavaScript"""
        if self._tab is None:
            await self.start_browser()

        return await self._tab.execute_script(script)

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.start_browser()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close_browser()