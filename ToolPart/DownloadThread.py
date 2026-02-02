import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Tuple, Dict
import requests
from PyQt5.QtCore import QThread, pyqtSignal
from ToolPart.Browser import get_browser
from ToolPart.ByPasser import CloudflareByPasser
from ToolPart.Logger import log_failure, TaskLogger


class VideoDownloadThread(QThread):
    # 定义信号
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(str, float)
    finished_signal = pyqtSignal(str, list)  # task_id, failed_urls

    # 新增视频进度信号
    video_start_signal = pyqtSignal(str, str)  # task_id, video_url
    video_progress_signal = pyqtSignal(str, str, float)  # task_id, video_url, progress
    video_complete_signal = pyqtSignal(str, str)  # task_id, video_url
    video_failed_signal = pyqtSignal(str, str, str)  # task_id, video_url, error

    def __init__(self, list_url: str, download_dir: str, task_id: str,
                 task_logger: Optional[TaskLogger] = None, is_retry: bool = False,
                 headless: bool = True):
        super().__init__()
        self.list_url = list_url
        self.download_dir = download_dir
        self.task_id = task_id
        self.task_logger = task_logger
        self.is_retry = is_retry
        self.headless = headless
        self.running = True
        self.paused = False
        self.pause_cond = threading.Condition(threading.Lock())
        self.max_workers = 1
        os.makedirs(self.download_dir, exist_ok=True)

        # 确保日志目录存在
        self.logger_dir = "./logger"
        os.makedirs(self.logger_dir, exist_ok=True)

        # 定义非法字符的正则表达式模式
        self.illegal_chars_pattern = re.compile(r'[\\/*?:"<>|]')

        # 失败重试配置
        self.max_download_retries = 3
        self.retry_delay = 5

        # 视频进度跟踪
        self.video_progress = {}  # {video_url: progress}

    def sanitize_filename(self, filename: str) -> str:
        """清洗文件名，移除非法字符"""
        clean_name = self.illegal_chars_pattern.sub('_', filename)
        clean_name = clean_name.strip()

        max_length = 200
        if len(clean_name) > max_length:
            name_part, ext = os.path.splitext(clean_name)
            name_part = name_part[:max_length - len(ext)]
            clean_name = name_part + ext

        if not clean_name:
            clean_name = "unnamed_video"

        return clean_name

    def log_message(self, message: str) -> None:
        """通过信号发送日志消息"""
        self.log_signal.emit(message)

    def pause(self) -> None:
        """暂停下载任务 - 优化：使用线程安全的方式"""
        with self.pause_cond:
            self.paused = True
            self.log_message(f"下载任务已暂停: {self.list_url}")

        # 更新任务状态
        if self.task_logger:
            self.task_logger.update_task_status(self.task_id, "paused")

    def resume(self) -> None:
        """继续下载任务 - 优化：使用线程安全的方式"""
        with self.pause_cond:
            self.paused = False
            self.pause_cond.notify_all()
        self.log_message(f"下载任务已继续: {self.list_url}")

        # 更新任务状态
        if self.task_logger:
            self.task_logger.update_task_status(self.task_id, "running")

    def stop(self) -> None:
        """停止下载任务"""
        self.running = False
        with self.pause_cond:
            self.paused = False
            self.pause_cond.notify_all()
        self.log_message(f"下载任务已停止: {self.list_url}")

        # 更新任务状态
        if self.task_logger:
            self.task_logger.update_task_status(self.task_id, "paused")

    def wait_if_paused(self) -> None:
        """如果任务被暂停，则等待直到继续 - 优化：使用条件变量"""
        with self.pause_cond:
            while self.paused and self.running:
                self.pause_cond.wait(0.5)  # 每0.5秒检查一次
                if not self.running:
                    break

    def handle_browser_navigation(self, browser, url: str, page_type: str = "list") -> bool:
        """处理浏览器导航，包括turnstile验证"""
        try:
            self.log_message(f"访问{page_type}页面: {url}")
            browser.get(url)

            # 如果不是无头模式，记录日志
            if not self.headless:
                self.log_message(f"非无头模式：{page_type}页面已打开")

            # 创建CloudflareByPasser实例
            cf_bypasser = CloudflareByPasser(
                driver=browser,
                max_retries=3,
                log_emitter=None
            )

            # 处理Cloudflare挑战
            self.log_message(f"处理{page_type}页面的Cloudflare验证...")
            if not cf_bypasser.bypass():
                self.log_message(f"{page_type}页面Cloudflare验证失败")
                return False

            self.log_message(f"{page_type}页面Cloudflare验证通过")

            # 等待页面完全加载
            time.sleep(2)

            return True

        except Exception as e:
            self.log_message(f"访问{page_type}页面时出错: {str(e)}")
            return False

    def get_video_links(self) -> Tuple[Optional[List[str]], Optional[str]]:
        """获取列表页中的所有视频链接和播放列表标题"""
        self.log_message(f"正在从 {self.list_url} 获取视频列表...")
        browser = None
        links: Optional[List[str]] = None
        playlist_title: Optional[str] = None

        try:
            browser = get_browser(self.headless)

            # 处理列表页导航和验证
            if not self.handle_browser_navigation(browser, self.list_url, "列表"):
                return None, None

            # 使用条件等待替代固定等待
            start_time = time.time()
            playlist = None
            while time.time() - start_time < 30:
                self.wait_if_paused()
                if not self.running:
                    return None, None

                playlist = browser.ele('#playlist-scroll', timeout=1)
                if playlist:
                    break
                time.sleep(1)
            else:
                self.log_message("等待播放列表加载超时")
                return None, None

            # 获取播放列表标题
            try:
                title_element = browser.ele('xpath://*[@id="video-playlist-wrapper"]/div[1]/h4[1]', timeout=5)
                if title_element:
                    playlist_title = title_element.text.strip()
                    self.log_message(f"播放列表标题: {playlist_title}")
                    # 发送标题更新信号
                    self.log_message(f"[TITLE_UPDATE]|||{self.task_id}|||{playlist_title}")
                else:
                    self.log_message("未找到播放列表标题")
            except Exception as e:
                self.log_message(f"获取播放列表标题时出错: {str(e)}")

            # 获取所有视频链接
            if playlist:
                link_elements = playlist.eles('tag:a', timeout=10)
                if link_elements:
                    found_links = [a.attr('href') for a in link_elements if a.attr('href')]
                    unique_links = list(set(found_links))
                    self.log_message(f"找到 {len(unique_links)} 个唯一视频")
                    links = unique_links

                    # 更新任务总视频数
                    if self.task_logger:
                        self.task_logger.update_task_total_videos(self.task_id, len(unique_links))

        except Exception as e:
            self.log_message(f"获取视频链接时出错: {str(e)}")
        finally:
            if browser:
                try:
                    browser.quit()
                except Exception as e:
                    self.log_message(f"关闭浏览器时出错: {str(e)}")

        return links, playlist_title

    def download_video(self, video_url: str) -> bool:
        """下载单个视频，增强失败重试机制"""
        if not self.running:
            return False

        # 发送视频开始信号
        self.video_start_signal.emit(self.task_id, video_url)

        # 记录视频任务开始
        if self.task_logger:
            self.task_logger.log_video_task_start(self.task_id, video_url)

        # 增强的重试配置
        max_retries = self.max_download_retries
        retry_count = 0
        success = False
        last_error = ""
        filename = None

        while retry_count <= max_retries and not success and self.running:
            retry_count += 1
            try:
                success, error, file = self._download_video_attempt(video_url)
                if not success:
                    last_error = error
                    if retry_count <= max_retries:
                        wait_time = self.retry_delay * retry_count
                        time.sleep(wait_time + random.random())
                else:
                    filename = file
            except Exception as e:
                last_error = str(e)
                if retry_count <= max_retries:
                    time.sleep(self.retry_delay)

        if success:
            # 发送视频完成信号
            self.video_complete_signal.emit(self.task_id, video_url)

            # 记录视频任务完成
            if self.task_logger:
                self.task_logger.log_video_task_complete(self.task_id, video_url)
            return True
        else:
            # 发送视频失败信号
            self.video_failed_signal.emit(self.task_id, video_url, last_error)

            if last_error:
                log_filename = filename if filename else video_url
                log_failure(self.logger_dir, log_filename, video_url, last_error)

            # 记录视频任务失败
            if self.task_logger:
                self.task_logger.log_video_task_failed(self.task_id, video_url, last_error)

            return False

    def _download_video_attempt(self, video_url: str) -> Tuple[bool, str, Optional[str]]:
        """单个视频下载尝试，增强错误处理"""
        browser = None
        filename = None

        try:
            browser = get_browser(self.headless)

            # 处理视频页导航和验证
            if not self.handle_browser_navigation(browser, video_url, "视频"):
                return False, "视频页面访问失败", None

            # 使用条件等待替代固定等待
            start_time = time.time()
            download_btn = None
            while time.time() - start_time < 30:
                self.wait_if_paused()
                if not self.running:
                    return False, "任务已停止", None

                download_btn = browser.ele('#downloadBtn', timeout=1)
                if download_btn:
                    break
                time.sleep(1)
            else:
                error_msg = "等待下载按钮加载超时"
                return False, error_msg, None

            download_page_url = download_btn.attr('href')
            if not download_page_url:
                error_msg = "下载按钮没有有效的链接"
                return False, error_msg, None

            # 处理下载页导航和验证
            if not self.handle_browser_navigation(browser, download_page_url, "下载"):
                return False, "下载页面访问失败", None

            # 定位下载链接
            download_table = browser.ele('#content-div', timeout=15)
            if not download_table:
                error_msg = "未找到下载表格"
                return False, error_msg, None

            download_link_ele = download_table.ele('xpath:.//tr[2]/td[5]/a', timeout=15)
            if not download_link_ele:
                error_msg = "未找到下载链接元素"
                return False, error_msg, None

            video_download_url = download_link_ele.attr('data-url')
            raw_filename = download_link_ele.attr('download')

            if not raw_filename.endswith('.mp4'):
                raw_filename += '.mp4'

            filename = self.sanitize_filename(raw_filename)

            # 检查文件是否已存在
            filepath = os.path.join(self.download_dir, filename)
            if os.path.exists(filepath) and os.path.isfile(filepath):
                file_size = os.path.getsize(filepath)
                return True, "文件已存在，跳过下载", filename

            if not video_download_url or not filename:
                error_msg = "未找到下载URL或文件名"
                return False, error_msg, None

            success, error = self.save_video(video_download_url, filename, video_url)
            return success, error, filename

        except Exception as e:
            error_msg = f"处理视频时出错: {str(e)}"
            return False, error_msg, filename
        finally:
            if browser:
                try:
                    browser.quit()
                except Exception as e:
                    pass

    def save_video(self, url: str, filename: str, video_url: str) -> Tuple[bool, str]:
        """保存视频文件，增强错误处理"""
        clean_filename = self.sanitize_filename(filename)
        filepath = os.path.join(self.download_dir, clean_filename)

        if not self.running:
            return False, "任务已停止"

        try:
            # 最终检查文件是否存在
            if os.path.exists(filepath) and os.path.isfile(filepath):
                return True, "文件已存在，跳过下载"

            headers = {
                'Referer': 'https://hanime1.me/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            }

            session = requests.Session()
            response = session.get(url, headers=headers, stream=True, timeout=(10, 300))
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            last_percent = -1

            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    self.wait_if_paused()

                    if not self.running:
                        if os.path.exists(filepath):
                            try:
                                os.remove(filepath)
                            except:
                                pass
                        return False, "任务已停止"

                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            # 更新进度
                            if abs(percent - last_percent) > 1 or percent == 100:
                                last_percent = percent
                                # 更新视频进度
                                self.video_progress[video_url] = percent
                                # 发送进度信号
                                self.video_progress_signal.emit(self.task_id, video_url, percent)

            # 验证文件大小
            if total_size > 0:
                actual_size = os.path.getsize(filepath)
                if actual_size < total_size * 0.9:
                    os.remove(filepath)
                    return False, f"文件大小不匹配"

            return True, ""

        except Exception as e:
            error_msg = str(e)
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            return False, error_msg

    def run(self) -> None:
        """运行下载任务，优化并发控制"""
        failed_downloads: List[str] = []

        try:
            self.log_message(f"开始下载任务: {self.list_url}")
            video_links, playlist_title = self.get_video_links()

            if not video_links:
                self.log_message("未找到视频链接，任务失败")
                # 标记任务失败
                if self.task_logger:
                    self.task_logger.mark_task_failed(self.task_id, "未找到视频链接")

                # 发送失败信号
                if self.running:
                    self.finished_signal.emit(self.task_id, [self.list_url])
                return

            self.log_message(f"找到 {len(video_links)} 个视频")

            # 优化并发控制
            actual_max_workers = min(self.max_workers, len(video_links))

            # 使用线程池并发下载视频
            with ThreadPoolExecutor(max_workers=actual_max_workers) as executor:
                futures = {}
                for i, link in enumerate(video_links):
                    if not self.running:
                        break

                    if 'search?query' in link:
                        continue

                    future = executor.submit(self.download_video, link)
                    futures[future] = link

                    wait_time = random.uniform(1.0, 2.0)
                    time.sleep(wait_time)

                # 等待所有任务完成
                for future in as_completed(futures):
                    if not self.running:
                        for f in futures.keys():
                            if not f.done():
                                f.cancel()
                        break

                    link = futures[future]
                    try:
                        success = future.result(timeout=3600)
                        if not success:
                            failed_downloads.append(link)
                    except Exception as e:
                        failed_downloads.append(link)

            # 更新任务状态
            if self.task_logger:
                if failed_downloads:
                    self.task_logger.update_task_status(self.task_id, "failed")
                else:
                    self.task_logger.update_task_status(self.task_id, "completed")

            # 发送完成信号
            if self.running:
                self.finished_signal.emit(self.task_id, failed_downloads)

        except Exception as e:
            self.log_message(f"下载任务异常: {str(e)}")
            # 发生异常时，标记任务失败
            if self.task_logger:
                self.task_logger.mark_task_failed(self.task_id, str(e))

            failed_downloads.append(self.list_url)
            self.finished_signal.emit(self.task_id, failed_downloads)