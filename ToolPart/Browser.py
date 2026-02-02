import random
import os
from DrissionPage import ChromiumPage, ChromiumOptions


def get_browser(headless: bool = True):
    """创建并配置浏览器实例"""
    options = ChromiumOptions().auto_port()

    # 基础配置
    options.set_argument('--no-sandbox')
    options.set_argument('--disable-gpu')
    options.set_argument('--disable-dev-shm-usage')
    options.set_argument('--disable-blink-features=AutomationControlled')
    options.set_argument('--disable-infobars')
    options.set_argument('--disable-extensions')
    options.set_argument('--disable-plugins')
    options.set_argument('--disable-background-timer-throttling')
    options.set_argument('--disable-backgrounding-occluded-windows')
    options.set_argument('--disable-renderer-backgrounding')
    options.set_argument('--memory-pressure-off')
    options.set_argument('--max_old_space_size=1024')

    # 绕过检测的配置
    options.set_argument('--disable-web-security')
    options.set_argument('--disable-site-isolation-trials')
    options.set_argument('--disable-features=IsolateOrigins,site-per-process')
    options.set_argument('--disable-features=AudioServiceOutOfProcess')
    options.set_argument('--disable-component-update')
    options.set_argument('--disable-default-apps')
    options.set_argument('--disable-features=Translate')
    options.set_argument('--disable-logging')
    options.set_argument('--disable-notifications')
    options.set_argument('--disable-popup-blocking')
    options.set_argument('--disable-sync')
    options.set_argument('--metrics-recording-only')
    options.set_argument('--no-first-run')
    options.set_argument('--safebrowsing-disable-auto-update')
    options.set_argument('--disable-client-side-phishing-detection')
    options.set_argument('--disable-component-extensions-with-background-pages')
    options.set_argument('--disable-breakpad')
    options.set_argument('--disable-crash-reporter')
    options.set_argument('--disable-domain-reliability')
    options.set_argument('--disable-features=LazyFrameLoading')
    options.set_argument('--disable-hang-monitor')
    options.set_argument('--disable-ipc-flooding-protection')
    options.set_argument('--disable-prompt-on-repost')
    options.set_argument('--disable-renderer-accessibility')
    options.set_argument('--force-color-profile=srgb')
    options.set_argument('--use-mock-keychain')

    # 隐藏自动化特征
    options.set_argument('--disable-blink-features=AutomationControlled')
    options.set_argument('--disable-features=UserAgentClientHint')

    # 根据参数启用或禁用无头模式
    if headless:
        options.set_argument('--headless=new')
        # 无头模式下的优化
        options.set_argument('--hide-scrollbars')
        options.set_argument('--mute-audio')
        options.set_argument('--disable-images')  # 可选，加速加载
    else:
        # 非无头模式下，可以添加一些优化参数
        options.set_argument('--start-maximized')
        options.set_argument('--window-size=1920,1080')

    # 设置用户代理
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    ]

    # 随机选择用户代理
    selected_ua = random.choice(user_agents)
    options.set_user_agent(selected_ua)

    # 设置语言和时区
    options.set_argument('--lang=en-US,en;q=0.9')

    # 设置视口大小
    options.set_argument('--window-size=1920,1080')

    # 创建浏览器实例
    browser = ChromiumPage(addr_or_opts=options)

    # 执行JavaScript来隐藏自动化特征
    try:
        # 移除webdriver属性
        browser.run_js("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // 修改navigator属性
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });

            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });

            // 修改屏幕属性
            Object.defineProperty(screen, 'width', {
                get: () => 1920
            });

            Object.defineProperty(screen, 'height', {
                get: () => 1080
            });

            // 修改chrome属性
            window.chrome = {
                runtime: {}
            };
        """)
    except:
        pass

    return browser