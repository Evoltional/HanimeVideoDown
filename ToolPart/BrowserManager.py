import asyncio
import contextlib
import logging

from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions

# 与 pydoll 3.0 内部一致的 Cloudflare 挑战域名标记，用于判断挑战 shadow root 是否仍在。
_CLOUDFLARE_CHALLENGE_DOMAIN = 'challenges.cloudflare.com'

logger = logging.getLogger(__name__)

# Cloudflare Turnstile 绕过：直接采用 pydoll 3.0 官方 API
#   async with tab.expect_cloudflare_turnstile(time_to_wait_captcha=...):
#       await tab.go_to(url)
# 该 API 会像真人一样定位 widget（穿透 shadow -> 跨源 iframe -> 内部 shadow）
# 并点击正确的 input[type="checkbox"]，且在 time_to_wait_captcha 内轮询/重试，
# 处理 Cloudflare 异步注入与重渲染。比手写遍历更稳，也是 3.0 修复 span.cb-i 缺陷后的正解。
# 如需进一步提升通过率，可在导航前调用 tab.apply_fingerprint(...) 并配合干净的 IP。


class BrowserManager:
    """浏览器管理器，负责浏览器的启动、配置和操作"""

    def __init__(self, headless: bool = True, download_dir: str = "./downloads",
                 timeout: int = 30, page_load_timeout: int = 60,
                 config_manager=None, use_bypass: bool = False):
        """
        初始化浏览器管理器
    
        Args:
            headless: 是否使用无头模式
            download_dir: 下载目录
            timeout: 操作超时时间 (秒)
            page_load_timeout: 页面加载超时时间 (秒)
            config_manager: 配置管理器实例
            use_bypass: 是否启用 Bypass模式（Cloudflare 验证码绕过）
        """
        self.headless = headless
        self.download_dir = download_dir
        self.timeout = timeout
        self.page_load_timeout = page_load_timeout
        self.config_manager = config_manager
        self.use_bypass = use_bypass  # 新增：记录是否启用 Bypass模式
        self.browser = None
        self.tab = None
        self._is_running = False

    async def start(self) -> bool:
        """启动浏览器"""
        try:
            options = self._create_chrome_options()
            self.browser = Chrome(options=options)
            await self.browser.__aenter__()
            self.tab = await self.browser.start()
            await self.tab.enable_page_events()

            # 反检测指纹（挑选不会造成"指纹不一致"的安全字段：屏幕/硬件/语言）。
            # 不伪装 WebGL：只给 vendor/renderer 而能力值回落真实软件渲染器会构成谎言信号。
            # cross_origin_iframes=True 让 Cloudflare 跨源挑战 iframe 也读到一致身份，提升无头通过率。
            # 失败不致命（不影响后续导航）。
            await self._apply_stealth_fingerprint()
            
            # Bypass 模式下，Cloudflare 求解由 go_to_with_captcha_bypass 内的
            # pydoll 3.0 官方 expect_cloudflare_turnstile() 负责（拟人化点击 widget）。
            if self.use_bypass:
                logger.info("Bypass 模式：Cloudflare 求解将使用 pydoll 官方 expect_cloudflare_turnstile")
            else:
                logger.info("普通模式：不启用 Cloudflare 求解")
            
            self._is_running = True
            logger.info("浏览器启动成功")
            return True
        except Exception as e:
            logger.exception(f"启动浏览器失败：{e}")
            return False

    async def _apply_stealth_fingerprint(self) -> None:
        """注入反检测指纹（安全子集），提升无头模式通过 Cloudflare 的概率。

        只覆盖不会与真实上下文矛盾的字段：
        - screen：视口尺寸（避免无头默认尺寸异常）
        - hardware：CPU 核数 / 内存（常见被比对的字段）
        - locale：语言列表（与 --accept-language 一致）
        不伪装 WebGL：pydoll 仅在我显式给出全套能力参数时才覆盖 WebGL，
        只给 vendor/renderer 而能力值回落真实软件渲染器会构成"谎言"信号，反而更易被拦。
        跨源 Cloudflare 挑战 iframe 也会重放同一身份（cross_origin_iframes=True）。
        """
        if not self.tab:
            return
        try:
            fingerprint = {
                'screen': {'width': 1920, 'height': 1080, 'device_pixel_ratio': 1.0},
                'hardware': {'hardware_concurrency': 8, 'device_memory': 8},
                'locale': {'languages': ['en-US', 'en']},
                # 权限通过 Browser.setPermission 原生设置（不是 JS 注入），避免无头默认
                # notifications='denied' 这一强 bot 信号；普通未配置用户应为 'prompt'。
                'permissions': {'overrides': {'notifications': 'prompt', 'geolocation': 'prompt'}},
            }
            await self.tab.apply_fingerprint(fingerprint, cross_origin_iframes=True)
            logger.info("已应用反检测指纹（屏幕/硬件/语言/权限，跨源 iframe 重放）")
        except Exception as e:
            logger.warning(f"应用反检测指纹失败（忽略，继续）: {e}")

    async def _is_cloudflare_blocked(self) -> bool:
        """检测是否仍停在 Cloudflare 拦截/错误页（如 #challenge-error-text）。

        无头模式常被 Cloudflare 风控直接返回错误挑战页（标题"请稍候…"但 DOM 是
        challenges.cloudflare.com 的拒绝页）。该页与"已绕过"的真实页不同，必须识别出来，
        否则会被误判为"清除成功"而继续在错误页上抓取，得到 0 条。
        """
        if not self.tab:
            return False
        try:
            # 仅在错误页才会渲染出带文本的错误元素；互动挑战页即使含同名 CSS，元素文本也为空，
            # 故用"元素存在且文本非空"作为精确判定，避免正常求解后的重定向瞬间误判。
            el = await self.tab.query('//*[@id="challenge-error-text"]', timeout=2, raise_exc=False)
            if el:
                txt = await self.get_element_text(el)
                if txt and txt.strip():
                    return True
        except Exception as exc:
            logger.debug(f"检测 Cloudflare 拦截页时异常（视作未拦截）: {exc}")
        return False

    def _create_chrome_options(self) -> ChromiumOptions:
        options = ChromiumOptions()
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-infobars')

        if self.headless:
            options.add_argument('--headless=new')
            # 关键修复（第十一轮）：--disable-gpu 会让 WebGL 完全不可用（软件渲染器也起不来），
            # 这是 Cloudflare 风控的强 bot 信号，导致无头模式直接被拦在"请稍候…"错误页。
            # 改为 --enable-unsafe-swiftshader + ANGLE/SwiftShader，让无头下 WebGL 以"一致"的
            # 软件渲染器正常工作，消除"无 WebGL / 硬件信息矛盾"被拦的风险。
            options.add_argument('--enable-unsafe-swiftshader')
            options.add_argument('--use-gl=angle')
            options.add_argument('--use-angle=swiftshader')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--lang=en-US')
            options.add_argument('--accept-language=en-US')
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
        # 注：pydoll 3.0 的 ChromiumOptions 已移除 page_load_timeout 属性，
        # 页面加载超时改由 tab.go_to(timeout=...) 控制。

        return options

    async def go_to(self, url: str, use_bypass: bool = False,
                    time_to_wait_captcha: int = None, max_retries: int = None):
        """
        访问URL，可选择是否启用Cloudflare验证码绕过

        Args:
            url: 目标URL
            use_bypass: 是否启用验证码绕过
            time_to_wait_captcha: 等待验证码处理的时间（秒）
            max_retries: 最大重试次数

        Returns:
            tab: 浏览器标签页对象
        """
        if not self._is_running or not self.tab:
            raise RuntimeError("浏览器未启动")

        if use_bypass:
            logger.info(f"使用 bypass 模式访问: {url}")
            return await self.go_to_with_captcha_bypass(url, time_to_wait_captcha, max_retries)
        else:
            logger.info(f"普通访问页面: {url}")
            await self.tab.go_to(url)
            # 等待页面基本加载完成
            await asyncio.sleep(2)
            return self.tab

    async def go_to_with_captcha_bypass(self, url: str, time_to_wait_captcha: int = None, max_retries: int = None):
        """
        访问URL并自动绕过 Cloudflare 验证码（Managed Challenge / Turnstile）。

        采用 pydoll 3.0 官方 API：expect_cloudflare_turnstile() 作为异步上下文管理器，
        在 go_to / refresh 期间像真人一样定位 widget 并做拟人化点击（已修正为
        input[type="checkbox"] 选择器），并在 time_to_wait_captcha 内轮询/重试。
        """
        if not self._is_running or not self.tab:
            raise RuntimeError("浏览器未启动")

        if time_to_wait_captcha is None and self.config_manager:
            time_to_wait_captcha = self.config_manager.get("cloudflare_timeout", 30)
        elif time_to_wait_captcha is None:
            time_to_wait_captcha = 30

        if max_retries is None and self.config_manager:
            max_retries = self.config_manager.get("cloudflare_max_retries", 3)
        elif max_retries is None:
            max_retries = 3

        last_exception = None

        for attempt in range(max_retries):
            try:
                logger.info(f"第 {attempt + 1}/{max_retries} 次尝试访问页面并绕过验证码: {url}")

                # 官方 API：在导航/刷新期间自动处理 Turnstile 复选框
                if attempt == 0:
                    async with self.tab.expect_cloudflare_turnstile(time_to_wait_captcha=time_to_wait_captcha):
                        await self.tab.go_to(url)
                else:
                    # 仅上一轮整体超时后才重新加载，避免反复重置 Managed Challenge
                    async with self.tab.expect_cloudflare_turnstile(time_to_wait_captcha=time_to_wait_captcha):
                        await self.tab.refresh()

                # 关键修复：pydoll 3.0 的 expect_cloudflare_turnstile 只在"复选框被点掉"后就返回，
                # 但 Cloudflare Managed Challenge 通常还有一段"验证中 → 重定向到真实页面"的尾程，
                # 且该重定向发生在上下文管理器退出、回调已移除之后。若不等待，调用方会在
                # 挑战页/重定向中间态上抓取元素，导致拿到 0 条（表现为"已通过却突然关闭"）。
                # 这里轮询直到 Cloudflare 挑战 shadow root 真正消失，确认已进入真实页面。
                cleared = await self._wait_for_cloudflare_cleared(
                    timeout=max(10, int(time_to_wait_captcha) + 10)
                )
                logger.info(
                    f"Cloudflare 挑战清除状态: {'已清除' if cleared else '超时未确认（继续交由调用方等待内容）'}"
                )

                body_element = await self.query_element('//body', timeout=5, raise_exc=False)
                if not body_element:
                    raise Exception("页面加载不完整，未找到body元素")

                # 诊断：记录最终 URL 与标题，便于 0 条时定位是"时机"还是"选择器"问题
                # 注意：pydoll 3.0 的 Tab 没有 .url 属性（那是 RequestHandle 的），正确用法是
                # await self.tab.current_url()；错误访问会被吞掉，这里改用正确 API 并打到 INFO。
                try:
                    final_url = await self.tab.current_url()
                    final_title = await self.tab.title()
                    logger.info(f"绕过完成后最终 URL: {final_url} | 标题: {final_title}")
                except Exception as e:
                    logger.info(f"读取最终 URL/标题失败: {e}")

                # 关键修复（第十一轮）：无头模式常被 Cloudflare 风控直接返回"拒绝/错误"挑战页
                # （标题仍是"请稍候…"，但 DOM 是 challenges.cloudflare.com 拦截页）。若此处不拦截，
                # 会误报"已清除"并继续在错误页抓元素 → 0 条。识别到拦截页则抛出异常，
                # 触发本方法的重试（refresh + 再次绕过）；多次仍失败则如实抛出。
                if await self._is_cloudflare_blocked():
                    raise Exception(
                        "仍停留在 Cloudflare 拦截/错误页（无头模式常被风控；"
                        "建议改用有头模式 headless=False，或使用干净代理/住宅 IP）"
                    )

                logger.info(f"成功访问页面并绕过验证码: {url}")
                return self.tab

            except Exception as e:
                last_exception = e
                logger.warning(f"第 {attempt + 1} 次尝试失败: {e}")

                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 30)  # 指数退避
                    logger.info(f"等待 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)

        logger.error(f"经过 {max_retries} 次尝试后仍无法绕过验证码: {url}")
        raise last_exception if last_exception else Exception("Cloudflare验证码绕过失败")

    async def _wait_for_cloudflare_cleared(self, timeout: int = 25) -> bool:
        """等待 Cloudflare 挑战真正清除（Turnstile 复选框点击之后的尾程）。

        pydoll 3.0 的 expect_cloudflare_turnstile 仅在"复选框被点掉"后就视为完成并退出，
        但 Cloudflare Managed Challenge 通常还有"验证中 → 重定向到真实页面"的过程，
        且该重定向发生在上下文管理器退出、回调已移除之后。若不等待，调用方会在
        挑战页/重定向中间态上抓取元素，导致拿到 0 条。

        实现：轮询页面里是否还存在 challenges.cloudflare.com 的 shadow root：
        存在=仍在挑战中；消失=挑战已清除（已进入真实页面或正在加载真实内容）。
        """
        if not self.tab:
            return False
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                roots = await self.tab.find_shadow_roots(deep=False)
                still_challenging = False
                for r in roots:
                    with contextlib.suppress(Exception):
                        if _CLOUDFLARE_CHALLENGE_DOMAIN in await r.inner_html():
                            still_challenging = True
                            break
                if not still_challenging:
                    return True
            except Exception as e:
                # 整个页面不稳定（正在重定向），稍后重试
                logger.debug(f"检测 Cloudflare 清除状态时页面不稳定: {e}")
            await asyncio.sleep(0.5)
        return False

    # 说明：自定义 Cloudflare 求解方法（_is_cloudflare_cleared / _attempt_cloudflare_click /
    # _solve_turnstile 及对应常量）已移除，统一改由 pydoll 3.0 官方 API
    # expect_cloudflare_turnstile() 处理。

    async def query_element(self, xpath: str, timeout: int = 5, raise_exc: bool = True):
        """使用 XPath/CSS 查询元素。

        pydoll 3.0 的 query() 同时支持 XPath 与 CSS，并自动识别类型；
        timeout 单位为秒。
        """
        if not self.tab:
            if raise_exc:
                raise RuntimeError("浏览器标签页未初始化")
            return None

        try:
            element = await self.tab.query(xpath, timeout=timeout, raise_exc=raise_exc)
            return element
        except Exception as e:
            logger.debug(f"查找元素失败 {xpath}: {e}")
            if raise_exc:
                raise
            return None

    async def find_element(self, **kwargs):
        """查找元素。

        支持 xpath=/css= 表达式（走 query），或语义化参数（id/name/tag_name/text/... 走 find）。
        """
        if not self.tab:
            raise RuntimeError("浏览器标签页未初始化")

        try:
            if 'xpath' in kwargs:
                expr = kwargs.pop('xpath')
            elif 'css' in kwargs:
                expr = kwargs.pop('css')
            else:
                # 语义化参数交给 v3 原生 find
                return await self.tab.find(**kwargs)

            timeout = kwargs.get('timeout', 0)
            find_all = kwargs.get('find_all', False)
            raise_exc = kwargs.get('raise_exc', True)
            return await self.tab.query(expr, timeout=timeout, find_all=find_all, raise_exc=raise_exc)
        except Exception as e:
            logger.debug(f"查找元素失败: {kwargs}, 错误: {e}")
            raise

    async def get_element_attribute(self, element, attribute: str):
        """获取元素属性值（pydoll 3.0：get_attribute 为同步方法，不可 await）"""
        try:
            if hasattr(element, 'get_attribute'):
                return element.get_attribute(attribute)
            # 兜底：非 pydoll 元素（理论上不会走到这里）
            return None
        except Exception as e:
            logger.debug(f"获取元素属性失败 {attribute}: {e}")
            return None

    async def get_element_text(self, element) -> str:
        """获取元素文本内容"""
        try:
            return await element.text
        except Exception as e:
            logger.debug(f"获取元素文本失败: {e}")
            return ""

    async def wait_for_element(self, selector: str, timeout: int = 10):
        """等待元素出现（selector 为 XPath 或 CSS，pydoll 3.0 query() 均支持）"""
        try:
            element = await self.query_element(selector, timeout=timeout, raise_exc=False)
            if element:
                return element
            # 兜底再试一次（query 已同时支持 XPath/CSS，这里仅为保险）
            return await self.tab.query(selector, timeout=timeout, raise_exc=False)
        except Exception as e:
            logger.debug(f"等待元素超时 {selector}: {e}")
            return None

    async def close(self):
        """关闭浏览器"""
        try:
            if self.browser:
                await self.browser.__aexit__(None, None, None)
                self.browser = None
                self.tab = None
                self._is_running = False
                logger.info("浏览器已关闭")
        except Exception as e:
            logger.error(f"关闭浏览器时出错: {e}")

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# 便捷函数
async def create_browser(headless: bool = True, download_dir: str = "./downloads",
                         timeout: int = 30, page_load_timeout: int = 60,
                         use_bypass: bool = False) -> BrowserManager:
    browser_manager = BrowserManager(
        headless=headless,
        download_dir=download_dir,
        timeout=timeout,
        page_load_timeout=page_load_timeout,
        use_bypass=use_bypass
    )
    if await browser_manager.start():
        return browser_manager
    else:
        raise RuntimeError("无法启动浏览器")
