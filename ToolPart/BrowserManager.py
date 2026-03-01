import asyncio
import logging

from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions


class BrowserManager:
    """浏览器管理器，负责浏览器的启动、配置和操作"""

    def __init__(self, headless: bool = True, download_dir: str = "./downloads",
                 timeout: int = 30, page_load_timeout: int = 60,
                 config_manager=None):
        """
        初始化浏览器管理器

        Args:
            headless: 是否使用无头模式
            download_dir: 下载目录
            timeout: 操作超时时间(秒)   # 统一单位为秒
            page_load_timeout: 页面加载超时时间(秒)
            config_manager: 配置管理器实例
        """
        self.headless = headless
        self.download_dir = download_dir
        self.timeout = timeout          # 秒
        self.page_load_timeout = page_load_timeout
        self.config_manager = config_manager
        self.browser = None
        self.tab = None
        self._is_running = False
        self.logger = logging.getLogger(__name__)

    async def start(self) -> bool:
        """
        启动浏览器

        Returns:
            bool: 启动是否成功
        """
        try:
            options = self._create_chrome_options()
            self.browser = Chrome(options=options)
            await self.browser.__aenter__()
            self.tab = await self.browser.start()
            await self.tab.enable_page_events()
            # 默认启用 Cloudflare 自动解决（可选，后续按需使用）
            await self.tab.enable_auto_solve_cloudflare_captcha()
            self._is_running = True
            self.logger.info("浏览器启动成功")
            return True
        except Exception as e:
            self.logger.error(f"启动浏览器失败: {e}")
            return False

    def _create_chrome_options(self) -> ChromiumOptions:
        options = ChromiumOptions()
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')

        if self.headless:
            options.add_argument('--headless=new')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument(
                '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        else:
            options.add_argument('--window-size=1920,1080')

        options.block_notifications = True
        options.block_popups = True

        from pydoll.constants import PageLoadState
        options.page_load_state = PageLoadState.INTERACTIVE
        options.start_timeout = 15
        options.set_default_download_directory(self.download_dir)
        options.page_load_timeout = self.page_load_timeout  # 秒

        return options

    async def go_to(self, url: str, use_bypass: bool = False,
                    time_to_wait_captcha: int = None, max_retries: int = None):
        """
        访问URL，可选择是否启用Cloudflare验证码绕过

        Args:
            url: 目标URL
            use_bypass: 是否启用验证码绕过
            time_to_wait_captcha: 等待验证码处理的时间（秒），仅当use_bypass=True时有效
            max_retries: 最大重试次数，仅当use_bypass=True时有效

        Returns:
            tab: 浏览器标签页对象
        """
        if not self._is_running or not self.tab:
            raise RuntimeError("浏览器未启动")

        if use_bypass:
            return await self.go_to_with_captcha_bypass(url, time_to_wait_captcha, max_retries)
        else:
            self.logger.info(f"普通访问页面: {url}")
            await self.tab.go_to(url)
            await asyncio.sleep(3)  # 等待基础渲染
            return self.tab

    async def go_to_with_captcha_bypass(self, url: str, time_to_wait_captcha: int = None, max_retries: int = None):
        """
        访问URL并自动绕过验证码（带重试机制）

        Args:
            url: 目标URL
            time_to_wait_captcha: 等待验证码处理的时间（秒），None则使用配置值
            max_retries: 最大重试次数，None则使用配置值

        Returns:
            tab: 浏览器标签页对象
        """
        if not self._is_running or not self.tab:
            raise RuntimeError("浏览器未启动")

        if time_to_wait_captcha is None and self.config_manager:
            time_to_wait_captcha = self.config_manager.get("cloudflare_timeout", 15)
        elif time_to_wait_captcha is None:
            time_to_wait_captcha = 15

        if max_retries is None and self.config_manager:
            max_retries = self.config_manager.get("cloudflare_max_retries", 3)
        elif max_retries is None:
            max_retries = 3

        last_exception = None

        for attempt in range(max_retries):
            try:
                self.logger.info(f"第 {attempt + 1}/{max_retries} 次尝试访问页面并绕过验证码: {url}")
                self.logger.debug(f"Cloudflare超时设置: {time_to_wait_captcha}秒, 页面加载超时: {self.page_load_timeout}秒")

                async with self.tab.expect_and_bypass_cloudflare_captcha(
                    time_to_wait_captcha=time_to_wait_captcha
                ):
                    await self.tab.go_to(url)
                    await asyncio.sleep(3)

                    body_element = await self.query_element('//body', timeout=5, raise_exc=False)
                    if not body_element:
                        raise Exception("页面加载不完整，未找到body元素")

                self.logger.info(f"成功访问页面并绕过验证码: {url}")
                return self.tab

            except Exception as e:
                last_exception = e
                error_msg = str(e)
                self.logger.warning(f"第 {attempt + 1} 次尝试失败: {error_msg}")

                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 30)  # 最大等待30秒
                    self.logger.info(f"等待 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)

        self.logger.error(f"经过 {max_retries} 次尝试后仍无法绕过验证码: {url}")
        raise last_exception if last_exception else Exception("Cloudflare验证码绕过失败")

    async def query_element(self, xpath: str, timeout: int = 5, raise_exc: bool = True):
        """
        使用XPath查询元素

        Args:
            xpath: XPath表达式
            timeout: 超时时间(秒)
            raise_exc: 是否抛出异常

        Returns:
            元素对象或None
        """
        if not self.tab:
            if raise_exc:
                raise RuntimeError("浏览器标签页未初始化")
            return None

        try:
            element = await self.tab.find(xpath=xpath, timeout=timeout * 1000)
            return element
        except Exception as e:
            self.logger.debug(f"查找元素失败 {xpath}: {e}")
            if raise_exc:
                raise
            return None

    async def find_element(self, **kwargs):
        """
        查找元素，支持多种查找方式

        Args:
            **kwargs: 查找参数，如id='element_id', class_name=' class-name '等

        Returns:
            元素对象或None
        """
        if not self.tab:
            raise RuntimeError("浏览器标签页未初始化")

        try:
            element = await self.tab.find(**kwargs)
            return element
        except Exception as e:
            self.logger.debug(f"查找元素失败: {kwargs}, 错误: {e}")
            raise

    async def get_element_attribute(self, element, attribute: str):
        """
        获取元素属性值

        Args:
            element: 元素对象
            attribute: 属性名称

        Returns:
            属性值或None
        """
        try:
            if hasattr(element, 'get_attribute'):
                return element.get_attribute(attribute)
            else:
                return await element.get_attribute(attribute)
        except Exception as e:
            self.logger.debug(f"获取元素属性失败 {attribute}: {e}")
            return None

    async def get_element_text(self, element) -> str:
        """
        获取元素文本内容

        Args:
            element: 元素对象

        Returns:
            文本内容
        """
        try:
            return await element.text
        except Exception as e:
            self.logger.debug(f"获取元素文本失败: {e}")
            return ""

    async def wait_for_element(self, selector: str, timeout: int = 10):
        """
        等待元素出现

        Args:
            selector: 选择器(XPath或CSS)
            timeout: 超时时间(秒)

        Returns:
            元素对象或None
        """
        try:
            element = await self.query_element(selector, timeout=timeout, raise_exc=False)
            if element:
                return element

            return await self.find_element(css=selector, timeout=timeout*1000)
        except Exception as e:
            self.logger.debug(f"等待元素超时 {selector}: {e}")
            return None

    async def close(self):
        """关闭浏览器"""
        try:
            if self.browser:
                await self.browser.__aexit__(None, None, None)
                self.browser = None
                self.tab = None
                self._is_running = False
                self.logger.info("浏览器已关闭")
        except Exception as e:
            self.logger.error(f"关闭浏览器时出错: {e}")

    @property
    def is_running(self) -> bool:
        """检查浏览器是否正在运行"""
        return self._is_running

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()


# 便捷函数
async def create_browser(headless: bool = True, download_dir: str = "./downloads",
                         timeout: int = 30, page_load_timeout: int = 60) -> BrowserManager:
    """
    创建并启动浏览器实例的便捷函数

    Args:
        headless: 是否无头模式
        download_dir: 下载目录
        timeout: 超时时间(秒)
        page_load_timeout: 页面加载超时时间(秒)

    Returns:
        BrowserManager: 浏览器管理器实例
    """
    browser_manager = BrowserManager(
        headless=headless,
        download_dir=download_dir,
        timeout=timeout,
        page_load_timeout=page_load_timeout
    )
    if await browser_manager.start():
        return browser_manager
    else:
        raise RuntimeError("无法启动浏览器")


if __name__ == "__main__":
    # 测试代码
    async def test_browser():
        async with BrowserManager(headless=False) as browser:
            tab = await browser.go_to("https://www.example.com", use_bypass=False)
            content = await browser.get_element_text(tab)
            if content:
                print(f"页面内容: {content[:100]}...")

    asyncio.run(test_browser())