"""
浏览器管理模块
负责浏览器实例的创建、配置和管理
使用 pyppeteer 作为浏览器自动化工具
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from pathlib import Path
import sys

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    import pyppeteer
    from pyppeteer import launch
    from pyppeteer.browser import Browser
    from pyppeteer.page import Page
except ImportError:
    pyppeteer = None
    logging.warning("pyppeteer 未安装，请运行: pip install pyppeteer")




class BrowserManager:
    """浏览器管理器类"""
    
    def __init__(self, headless: bool = True, timeout: int = 30000):
        """
        初始化浏览器管理器
        
        Args:
            headless: 是否启用无头模式
            timeout: 页面加载超时时间(毫秒)
        """

        self.headless = headless
        self.timeout = timeout
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._is_running = False
        
        # 配置日志
        self.logger = logging.getLogger(__name__)
        
    async def start(self) -> bool:
        """
        启动浏览器实例
        
        Returns:
            bool: 启动是否成功
        """
        try:
            if not pyppeteer:
                self.logger.error("pyppeteer 未安装，无法启动浏览器")
                return False
                
            if self._is_running:
                self.logger.warning("浏览器已在运行中")
                return True
            
            # 创建浏览器选项
            browser_options = self._create_browser_options()
            
            # 启动浏览器
            self.browser = await launch(**browser_options)
            self.page = await self.browser.newPage()
            
            # 设置页面超时
            await self.page.setDefaultNavigationTimeout(self.timeout)
            await self.page.setDefaultTimeout(self.timeout)
            
            self._is_running = True
            self.logger.info("浏览器启动成功")
            return True
            
        except Exception as e:
            self.logger.error(f"启动浏览器失败: {e}")
            await self.close()
            return False
    
    def _create_browser_options(self) -> Dict[str, Any]:
        """
        创建浏览器启动选项
        
        Returns:
            Dict[str, Any]: 浏览器选项字典
        """
        options = {
            'headless': self.headless,
            'args': [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                '--allow-running-insecure-content',
            ]
        }
        
        # 如果不是无头模式，添加窗口大小参数
        if not self.headless:
            options['args'].extend([
                '--window-size=1920,1080'
            ])
        
        # 添加用户数据目录（可选）
        user_data_dir = self.config.get('browser.user_data_dir')
        if user_data_dir:
            options['userDataDir'] = user_data_dir
            
        # 添加代理设置（可选）
        proxy = self.config.get('browser.proxy')
        if proxy:
            options['args'].append(f'--proxy-server={proxy}')
            
        return options
    
    async def navigate_to(self, url: str) -> bool:
        """
        导航到指定URL
        
        Args:
            url: 目标URL
            
        Returns:
            bool: 导航是否成功
        """
        try:
            if not self._is_running or not self.page:
                self.logger.error("浏览器未启动")
                return False
                
            await self.page.goto(url, {'waitUntil': 'networkidle2'})
            self.logger.info(f"成功导航到: {url}")
            return True
            
        except Exception as e:
            self.logger.error(f"导航失败 {url}: {e}")
            return False
    
    async def wait_for_selector(self, selector: str, timeout: int = 30000) -> bool:
        """
        等待元素出现
        
        Args:
            selector: CSS选择器
            timeout: 超时时间(毫秒)
            
        Returns:
            bool: 元素是否出现
        """
        try:
            if not self.page:
                return False
                
            await self.page.waitForSelector(selector, {'timeout': timeout})
            return True
            
        except Exception as e:
            self.logger.debug(f"等待元素超时 {selector}: {e}")
            return False
    
    async def click_element(self, selector: str) -> bool:
        """
        点击元素
        
        Args:
            selector: CSS选择器
            
        Returns:
            bool: 点击是否成功
        """
        try:
            if not self.page:
                return False
                
            await self.page.click(selector)
            self.logger.debug(f"点击元素: {selector}")
            return True
            
        except Exception as e:
            self.logger.error(f"点击元素失败 {selector}: {e}")
            return False
    
    async def get_page_content(self) -> Optional[str]:
        """
        获取页面HTML内容
        
        Returns:
            Optional[str]: 页面HTML内容
        """
        try:
            if not self.page:
                return None
                
            content = await self.page.content()
            return content
            
        except Exception as e:
            self.logger.error(f"获取页面内容失败: {e}")
            return None
    
    async def evaluate_js(self, script: str) -> Any:
        """
        执行JavaScript代码
        
        Args:
            script: JavaScript代码
            
        Returns:
            Any: 执行结果
        """
        try:
            if not self.page:
                return None
                
            result = await self.page.evaluate(script)
            return result
            
        except Exception as e:
            self.logger.error(f"执行JavaScript失败: {e}")
            return None
    
    async def take_screenshot(self, filepath: str) -> bool:
        """
        截取屏幕截图
        
        Args:
            filepath: 截图保存路径
            
        Returns:
            bool: 截图是否成功
        """
        try:
            if not self.page:
                return False
                
            await self.page.screenshot({'path': filepath})
            self.logger.info(f"截图已保存: {filepath}")
            return True
            
        except Exception as e:
            self.logger.error(f"截图失败 {filepath}: {e}")
            return False
    
    async def close(self):
        """关闭浏览器实例"""
        try:
            if self.page:
                await self.page.close()
                self.page = None
                
            if self.browser:
                await self.browser.close()
                self.browser = None
                
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
async def create_browser(headless: bool = True, timeout: int = 30000) -> BrowserManager:
    """
    创建并启动浏览器实例的便捷函数
    
    Args:
        headless: 是否无头模式
        timeout: 超时时间
        
    Returns:
        BrowserManager: 浏览器管理器实例
    """
    browser_manager = BrowserManager(headless=headless, timeout=timeout)
    if await browser_manager.start():
        return browser_manager
    else:
        raise RuntimeError("无法启动浏览器")


if __name__ == "__main__":
    # 测试代码
    async def test_browser():
        async with BrowserManager(headless=False) as browser:
            await browser.navigate_to("https://www.example.com")
            content = await browser.get_page_content()
            if content:
                print(f"页面标题: {content[:100]}...")
    
    # 运行测试
    asyncio.run(test_browser())