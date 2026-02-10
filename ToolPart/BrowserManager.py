import asyncio
import logging

from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions


class BrowserManager:
    """浏览器管理器，负责浏览器的启动、配置和操作"""
    
    def __init__(self, headless: bool = True, download_dir: str = "./downloads", timeout: int = 30000):
        """
        初始化浏览器管理器
        
        Args:
            headless: 是否使用无头模式
            download_dir: 下载目录
            timeout: 操作超时时间(毫秒)
        """
        self.headless = headless
        self.download_dir = download_dir
        self.timeout = timeout
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
            
            self._is_running = True
            self.logger.info("浏览器启动成功")
            return True
            
        except Exception as e:
            self.logger.error(f"启动浏览器失败: {e}")
            return False
    
    def _create_chrome_options(self) -> ChromiumOptions:
        """
        创建Chrome浏览器选项
        
        Returns:
            ChromiumOptions: 浏览器选项对象
        """
        options = ChromiumOptions()
        
        # 基本设置
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        
        # 无头模式设置
        if self.headless:
            options.add_argument('--headless=new')
        
        # 下载设置
        options.set_default_download_directory(self.download_dir)
        
        # 其他优化设置
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-plugins')
        options.add_argument('--disable-images')  # 可选：禁用图片加载以提高速度
        
        return options
    
    async def go_to_with_captcha_bypass(self, url: str):
        """
        访问URL并自动绕过验证码
        
        Args:
            url: 目标URL
            
        Returns:
            tab: 浏览器标签页对象
        """
        if not self._is_running or not self.tab:
            raise RuntimeError("浏览器未启动")
        
        try:
            # 使用上下文管理器自动处理验证码
            async with self.tab.expect_and_bypass_cloudflare_captcha():
                await self.tab.go_to(url)
            
            self.logger.info(f"成功访问页面: {url}")
            return self.tab
            
        except Exception as e:
            self.logger.error(f"访问页面失败 {url}: {e}")
            raise
    
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
async def create_browser(headless: bool = True, download_dir: str = "./downloads", timeout: int = 30000) -> BrowserManager:
    """
    创建并启动浏览器实例的便捷函数
    
    Args:
        headless: 是否无头模式
        download_dir: 下载目录
        timeout: 超时时间
        
    Returns:
        BrowserManager: 浏览器管理器实例
    """
    browser_manager = BrowserManager(headless=headless, download_dir=download_dir, timeout=timeout)
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