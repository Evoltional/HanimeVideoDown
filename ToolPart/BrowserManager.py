import asyncio
import logging

from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions


class BrowserManager:
    """浏览器管理器，负责浏览器的启动、配置和操作"""

    def __init__(self, headless: bool = True, download_dir: str = "./downloads",
                 timeout: int = 30000, page_load_timeout: int = 60,
                 config_manager=None):
        """
        初始化浏览器管理器

        Args:
            headless: 是否使用无头模式
            download_dir: 下载目录
            timeout: 操作超时时间(毫秒)
            page_load_timeout: 页面加载超时时间(秒)
            config_manager: 配置管理器实例
        """
        self.headless = headless
        self.download_dir = download_dir
        self.timeout = timeout
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
            # 创建浏览器选项
            options = self._create_chrome_options()

            # 启动浏览器
            self.browser = Chrome(options=options)
            await self.browser.__aenter__()

            # 启动标签页
            self.tab = await self.browser.start()

            # 启用页面事件，以便处理 Cloudflare 验证码
            await self.tab.enable_page_events()

            # 启用Cloudflare自动解决功能（可选，与 enable_page_events 配合使用）
            await self.tab.enable_auto_solve_cloudflare_captcha()

            self._is_running = True
            self.logger.info("浏览器启动成功")
            return True

        except Exception as e:
            self.logger.error(f"启动浏览器失败: {e}")
            return False

    def _create_chrome_options(self) -> ChromiumOptions:
        options = ChromiumOptions()

        # 基本反检测设置
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--no-sandbox')  # Linux 环境下可能需要
        options.add_argument('--disable-dev-shm-usage')  # 避免 /dev/shm 耗尽

        # 无头模式必须的参数
        if self.headless:
            options.add_argument('--headless=new')
            options.add_argument('--disable-gpu')  # 必须
            options.add_argument('--window-size=1920,1080')  # 设置合适分辨率
            # 可选：设置用户代理，伪装成普通 Chrome
            options.add_argument(
                '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        else:
            options.add_argument('--window-size=1920,1080')  # 有头模式也可设置

        # 阻止弹窗和通知
        options.block_notifications = True
        options.block_popups = True

        # 页面加载状态（参考示例）
        from pydoll.constants import PageLoadState
        options.page_load_state = PageLoadState.INTERACTIVE

        # 启动超时
        options.start_timeout = 15

        # 下载设置
        options.set_default_download_directory(self.download_dir)

        # 页面加载超时（秒）
        options.page_load_timeout = self.page_load_timeout

        return options

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

        # 使用配置中的超时设置（如果提供了的话）
        if time_to_wait_captcha is None and self.config_manager:
            time_to_wait_captcha = self.config_manager.get("cloudflare_timeout", 15)
        elif time_to_wait_captcha is None:
            time_to_wait_captcha = 15
            
        if max_retries is None and self.config_manager:
            max_retries = self.config_manager.get("cloudflare_max_retries", 3)
        elif max_retries is None:
            max_retries = 3

        last_exception = None
        
        # 尝试多次绕过验证码
        for attempt in range(max_retries):
            try:
                self.logger.info(f"第 {attempt + 1}/{max_retries} 次尝试访问页面并绕过验证码: {url}")
                self.logger.debug(f"Cloudflare超时设置: {time_to_wait_captcha}秒, 页面加载超时: {self.page_load_timeout}秒")
                
                # 使用上下文管理器自动处理验证码
                async with self.tab.expect_and_bypass_cloudflare_captcha(
                    time_to_wait_captcha=time_to_wait_captcha
                ):
                    await self.tab.go_to(url)
                    # 等待页面完全加载
                    await asyncio.sleep(3)
                    
                    # 验证页面是否成功加载（检查是否有body元素）
                    body_element = await self.query_element('//body', timeout=5, raise_exc=False)
                    if not body_element:
                        raise Exception("页面加载不完整，未找到body元素")
                
                self.logger.info(f"成功访问页面并绕过验证码: {url}")
                return self.tab
                
            except Exception as e:
                last_exception = e
                error_msg = str(e)
                self.logger.warning(f"第 {attempt + 1} 次尝试失败: {error_msg}")
                
                # 提供更详细的错误分类和建议
                if "Timed out" in error_msg and "Cloudflare" in error_msg:
                    self.logger.info("Cloudflare验证超时，可能是网络较慢或验证复杂度较高")
                elif "shadow root" in error_msg.lower():
                    self.logger.info("Shadow DOM元素查找失败，可能需要调整等待策略")
                elif "element" in error_msg.lower() and "not found" in error_msg.lower():
                    self.logger.info("页面元素未找到，可能是页面结构变化或加载不完整")
                
                # 如果不是最后一次尝试，等待一段时间再重试
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 指数退避: 1s, 2s, 4s...
                    self.logger.info(f"等待 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                
        # 所有重试都失败
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
            # pydoll中使用find方法查找元素，设置超时时间
            element = await self.tab.find(xpath=xpath, timeout=timeout * 1000)  # 转换为毫秒
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
            # 根据参数类型调用不同的查找方法
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
                # 如果元素对象没有get_attribute方法，尝试其他方式
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
            # 直接await element.text协程
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
            # 尝试使用XPath查找
            element = await self.query_element(selector, timeout=timeout, raise_exc=False)
            if element:
                return element

            # 如果XPath失败，尝试其他查找方式
            # 这里可以根据selector的格式判断是XPath还是CSS选择器
            if selector.startswith('/') or selector.startswith('./'):
                # XPath格式
                return await self.query_element(selector, timeout=timeout, raise_exc=False)
            else:
                # 可能是CSS选择器
                return await self.find_element(css=selector)

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
                         timeout: int = 30000, page_load_timeout: int = 60) -> BrowserManager:
    """
    创建并启动浏览器实例的便捷函数

    Args:
        headless: 是否无头模式
        download_dir: 下载目录
        timeout: 超时时间
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
            tab = await browser.go_to_with_captcha_bypass("https://www.example.com")
            content = await browser.get_element_text(tab)
            if content:
                print(f"页面内容: {content[:100]}...")

    # 运行测试
    asyncio.run(test_browser())