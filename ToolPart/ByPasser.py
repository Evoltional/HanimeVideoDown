import time
import random
from typing import Optional, Tuple
from DrissionPage import ChromiumPage

from ToolPart.Logger import LogEmitter


class CloudflareByPasser:
    def __init__(self, driver: ChromiumPage, max_retries: int = 3, log_emitter: Optional[LogEmitter] = None):
        self.driver = driver
        self.max_retries = max_retries
        self.log_emitter = log_emitter
        self.timeout = 30

    def log_message(self, message: str) -> None:
        if self.log_emitter and hasattr(self.log_emitter, 'log_signal'):
            self.log_emitter.log_signal.emit(message)  # type: ignore

    def check_cloudflare_challenge(self) -> bool:
        """检查页面是否存在Cloudflare挑战"""
        try:
            # 检查常见Cloudflare标识
            if "just a moment" in self.driver.title.lower():
                return True

            if "checking your browser" in self.driver.title.lower():
                return True

            # 检查Cloudflare相关元素
            cf_selectors = [
                '.cf-browser-verification',
                '#cf-wrapper',
                '#challenge-form',
                '#cf-challenge-running'
            ]

            for selector in cf_selectors:
                if self.driver.ele(selector, timeout=0.5):
                    return True

            return False
        except Exception as e:
            self.log_message(f"检查Cloudflare挑战时出错: {e}")
            return False

    def find_turnstile_widget(self) -> Optional[Tuple[str, any]]:
        """查找turnstile小部件"""
        try:
            # 方法1: 查找iframe中的turnstile
            iframes = self.driver.eles('tag:iframe')
            for iframe in iframes:
                if not iframe:
                    continue

                src = iframe.attr('src', '')
                if src and 'challenges.cloudflare.com' in src:
                    self.log_message("找到Cloudflare挑战iframe")
                    return "iframe", iframe

                # 检查iframe内容
                try:
                    iframe.switch_to()
                    challenge_elements = self.driver.eles('.main-wrapper', timeout=1)
                    if challenge_elements:
                        return "iframe", iframe
                except:
                    pass
                finally:
                    self.driver.switch_to.main_frame()

            # 方法2: 查找turnstile容器
            turnstile_selectors = [
                '[data-sitekey]',
                '.cf-turnstile',
                '#cf-challenge-widget',
                'iframe[title*="challenge"]'
            ]

            for selector in turnstile_selectors:
                element = self.driver.ele(selector, timeout=0.5)
                if element:
                    self.log_message(f"找到turnstile元素: {selector}")
                    return "element", element

            return None
        except Exception as e:
            self.log_message(f"查找turnstile小部件时出错: {e}")
            return None

    def solve_turnstile_challenge(self) -> bool:
        """尝试解决turnstile挑战"""
        try:
            self.log_message("尝试解决turnstile挑战...")

            # 查找turnstile小部件
            result = self.find_turnstile_widget()
            if not result:
                self.log_message("未找到turnstile小部件")
                return False

            widget_type, widget = result

            if widget_type == "iframe":
                # 切换到iframe
                widget.switch_to()
                self.log_message("已切换到turnstile iframe")

                # 等待加载
                time.sleep(2)

                # 检查是否需要交互
                challenge_form = self.driver.ele('#challenge-form', timeout=2)
                if challenge_form:
                    # 尝试点击验证按钮
                    verify_button = self.driver.ele('.hcaptcha-box', timeout=2)
                    if verify_button:
                        verify_button.click()
                        self.log_message("已点击验证按钮")

                    # 或者尝试提交表单
                    submit_button = self.driver.ele('input[type="submit"]', timeout=2)
                    if submit_button:
                        submit_button.click()
                        self.log_message("已提交验证表单")

                # 切换回主框架
                self.driver.switch_to.main_frame()

            elif widget_type == "element":
                # 尝试点击验证区域
                try:
                    widget.click()
                    self.log_message("已点击turnstile验证区域")
                except:
                    pass

            # 等待验证完成
            self.log_message("等待验证完成...")
            time.sleep(3)

            return True
        except Exception as e:
            self.log_message(f"解决turnstile挑战时出错: {e}")
            return False
        finally:
            # 确保切换回主框架
            try:
                self.driver.switch_to.main_frame()
            except:
                pass

    def handle_cloudflare_challenge(self) -> bool:
        """处理Cloudflare挑战，包括turnstile"""
        try:
            self.log_message("开始处理Cloudflare挑战...")

            # 检查是否需要挑战
            if not self.check_cloudflare_challenge():
                self.log_message("未检测到Cloudflare挑战")
                return True

            self.log_message("检测到Cloudflare挑战")

            # 等待页面完全加载
            time.sleep(2)

            # 尝试自动解决挑战
            for attempt in range(self.max_retries):
                self.log_message(f"尝试解决挑战 (第 {attempt + 1}/{self.max_retries} 次)...")

                # 检查是否是turnstile挑战
                if "turnstile" in self.driver.html.lower():
                    self.log_message("检测到turnstile挑战")
                    if self.solve_turnstile_challenge():
                        self.log_message("turnstile挑战处理成功")
                else:
                    # 传统Cloudflare挑战
                    self.log_message("检测到传统Cloudflare挑战")
                    self.click_verification_button()

                # 等待挑战完成
                wait_time = 5
                for i in range(wait_time):
                    if not self.check_cloudflare_challenge():
                        self.log_message("Cloudflare挑战已通过")
                        return True
                    time.sleep(1)

                self.log_message("挑战未解决，等待后重试...")
                time.sleep(2)

            self.log_message("超过最大重试次数，挑战处理失败")
            return False

        except Exception as e:
            self.log_message(f"处理Cloudflare挑战时出错: {e}")
            return False

    def click_verification_button(self) -> None:
        """点击验证按钮（传统Cloudflare挑战）"""
        try:
            # 尝试多种方法查找验证按钮
            button_selectors = [
                '.hcaptcha-box',
                '#challenge-stage input[type="submit"]',
                '.button',
                'input[value="Verify"]'
            ]

            for selector in button_selectors:
                button = self.driver.ele(selector, timeout=1)
                if button:
                    self.log_message(f"找到验证按钮: {selector}")
                    button.click()
                    time.sleep(1)
                    return

            # 如果找不到按钮，尝试点击页面中心
            self.log_message("未找到验证按钮，尝试点击页面中心")
            self.driver.scroll.to_see('body')
            self.driver.click('body')

        except Exception as e:
            self.log_message(f"点击验证按钮时出错: {e}")

    def wait_for_challenge_completion(self, timeout: int = 30) -> bool:
        """等待挑战完成"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if not self.check_cloudflare_challenge():
                return True
            time.sleep(1)
        return False

    def bypass(self) -> bool:
        """执行绕过流程"""
        self.log_message("开始Cloudflare绕过流程...")

        # 处理Cloudflare挑战
        if not self.handle_cloudflare_challenge():
            self.log_message("Cloudflare挑战处理失败")
            return False

        # 等待挑战完成
        if not self.wait_for_challenge_completion(self.timeout):
            self.log_message("等待挑战完成超时")
            return False

        self.log_message("Cloudflare绕过成功")
        return True