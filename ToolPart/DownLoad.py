import asyncio
import os
import queue
import threading
import time
import re
from typing import List, Optional, Tuple
from urllib.parse import unquote
import weakref

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from ToolPart.BrowserManager import BrowserManager


class VideoDownloader:
    def __init__(self, download_dir="./downloads", max_retries=3, headless=True):
        self.download_dir = download_dir
        self.max_retries = max_retries
        self.headless = headless
        os.makedirs(self.download_dir, exist_ok=True)
        self.progress_callback = None

    def set_progress_callback(self, callback):
        self.progress_callback = callback

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        filename = unquote(filename)
        # 限制长度，防止文件名过长
        if len(filename) > 200:
            name, ext = os.path.splitext(filename)
            filename = name[:190] + ext
        return filename

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    def standard_print(self, level: str, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {level.upper()} - {message}")

    async def check_pause(self, worker) -> bool:
        if worker and hasattr(worker, 'should_pause'):
            return worker.should_pause()
        return False

    async def check_stop(self, worker) -> bool:
        if worker and hasattr(worker, 'should_stop'):
            return worker.should_stop()
        return False

    async def extract_download_info(self, download_page_url: str, worker=None, browser: BrowserManager = None) -> Tuple[Optional[str], Optional[str]]:
        """
        提取下载信息，可复用已存在的浏览器实例
        :param download_page_url: 下载页面URL
        :param worker: 工作线程实例（用于检查暂停/停止）
        :param browser: 可选的浏览器管理器，如果提供则使用该实例，否则新建
        """
        if await self.check_stop(worker):
            print("提取下载信息前检测到停止指令")
            return None, None
        if await self.check_pause(worker):
            print("浏览器操作被暂停")
            return None, None

        # 如果未提供浏览器，则新建一个（后续会关闭）
        need_close = False
        if browser is None:
            browser = BrowserManager(headless=self.headless, download_dir=self.download_dir)
            await browser.start()
            need_close = True

        try:
            self.standard_print("INFO", f"访问下载页面: {download_page_url}")
            await browser.go_to(download_page_url, use_bypass=False)
            await asyncio.sleep(3)

            if await self.check_stop(worker):
                print("页面加载后检测到停止指令")
                return None, None
            if await self.check_pause(worker):
                print("页面加载后检测到暂停指令")
                return None, None

            try:
                table_element = None
                for attempt in range(3):
                    if await self.check_stop(worker):
                        print("查找表格时检测到停止指令")
                        return None, None
                    if await self.check_pause(worker):
                        print("查找表格时检测到暂停指令")
                        return None, None
                    table_element = await browser.query_element(
                        '//*[@id="content-div"]/div[1]/div[4]/div/div/table',
                        timeout=5, raise_exc=False
                    )
                    if table_element:
                        break
                    await asyncio.sleep(1)

                if not table_element:
                    print("未找到下载链接列表表格")
                    return None, None

                self.standard_print("INFO", "找到下载链接列表表格")

                first_download_btn = None
                for attempt in range(3):
                    if await self.check_stop(worker):
                        print("查找下载按钮时检测到停止指令")
                        return None, None
                    if await self.check_pause(worker):
                        print("查找下载按钮时检测到暂停指令")
                        return None, None
                    first_download_btn = await browser.query_element(
                        '//*[@id="content-div"]/div[1]/div[4]/div/div/table/tbody/tr[2]/td[5]/a',
                        timeout=5, raise_exc=False
                    )
                    if first_download_btn:
                        break
                    await asyncio.sleep(1)

                if not first_download_btn:
                    print("未找到第一个下载按钮")
                    return None, None

                download_url = first_download_btn.get_attribute('data-url')
                filename = first_download_btn.get_attribute('download')
                if not filename:
                    filename = await first_download_btn.text

                if filename:
                    filename = filename.strip()
                    if not filename.endswith('.mp4'):
                        filename += '.mp4'

                self.standard_print("SUCCESS", f"提取到下载链接: {download_url}")
                self.standard_print("INFO", f"提取到文件名: {filename}")

                return download_url, filename

            except Exception as e:
                self.standard_print("ERROR", f"提取下载信息时出错: {e}")
                import traceback
                traceback.print_exc()
                return None, None

        finally:
            if need_close and browser:
                await browser.close()

    def download_video(self, video_url: str, filename: str, worker=None, task_logger=None, task_id=None) -> bool:
        if not video_url or not filename:
            self.standard_print("WARNING", "下载链接或文件名为空，跳过下载")
            return False

        safe_filename = self._sanitize_filename(filename)
        downloading_filename = f"下载中_{safe_filename}"
        downloading_file_path = os.path.join(self.download_dir, downloading_filename)
        final_file_path = os.path.join(self.download_dir, safe_filename)

        if os.path.exists(final_file_path):
            self.standard_print("INFO", f"文件已存在，跳过下载: {final_file_path}")
            if task_logger and task_id:
                task_logger.add_downloaded_video(task_id, safe_filename)
                if self.progress_callback:
                    self.progress_callback(safe_filename, "已完成")
            return True

        if os.path.exists(downloading_file_path):
            self.standard_print("WARNING", f"发现之前的下载中文件: {downloading_file_path}")
            try:
                os.remove(downloading_file_path)
                self.standard_print("INFO", "已删除之前的下载中文件")
            except Exception as e:
                self.standard_print("ERROR", f"删除之前下载文件失败: {e}")
                return False

        self.standard_print("INFO", f"开始下载: {safe_filename} (临时文件: {downloading_filename})")

        if self.progress_callback:
            self.progress_callback(safe_filename, "0%")

        retry_count = 0
        CHECK_INTERVAL = 10  # 每10个块检查一次暂停/停止

        while retry_count < self.max_retries:
            try:
                response = requests.get(video_url, stream=True, timeout=30)
                response.raise_for_status()

                total_size = int(response.headers.get('content-length', 0))
                downloaded_size = 0
                chunk_counter = 0

                os.makedirs(self.download_dir, exist_ok=True)

                with open(downloading_file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            chunk_counter += 1

                            if chunk_counter % CHECK_INTERVAL == 0:
                                if worker and hasattr(worker, 'should_stop') and worker.should_stop():
                                    self.standard_print("WARNING", "\n下载已被停止")
                                    response.close()
                                    if os.path.exists(downloading_file_path):
                                        try:
                                            os.remove(downloading_file_path)
                                            self.standard_print("INFO", "已删除临时文件")
                                        except Exception as e:
                                            self.standard_print("ERROR", f"删除临时文件失败: {e}")
                                    return False

                                if worker and hasattr(worker, 'should_pause') and worker.should_pause():
                                    self.standard_print("WARNING", "\n下载已被暂停")
                                    while worker.should_pause() and not worker.should_stop():
                                        time.sleep(0.1)
                                    if worker.should_stop():
                                        return False

                            if total_size > 0:
                                progress = (downloaded_size / total_size) * 100
                                progress_str = f"{progress:.1f}% ({self._format_size(downloaded_size)}/{self._format_size(total_size)})"
                                if self.progress_callback:
                                    self.progress_callback(safe_filename, progress_str)
                                print(f"\r{safe_filename}: {progress_str}", end='')
                            else:
                                if self.progress_callback:
                                    self.progress_callback(safe_filename, f"{self._format_size(downloaded_size)}")
                                print(f"\r{safe_filename}: {self._format_size(downloaded_size)}", end='')

                self.standard_print("INFO", "\n下载完成，正在重命名文件...")

                try:
                    os.replace(downloading_file_path, final_file_path)  # 原子操作
                    self.standard_print("SUCCESS", f"文件重命名完成: {final_file_path}")
                    if self.progress_callback:
                        self.progress_callback(safe_filename, "100.0%")
                except Exception as e:
                    self.standard_print("ERROR", f"文件重命名失败: {e}")
                    return False

                if task_logger and task_id:
                    task_logger.add_downloaded_video(task_id, safe_filename)

                return True

            except Exception as e:
                retry_count += 1
                self.standard_print("WARNING", f"下载失败 (重试 {retry_count}/{self.max_retries}): {e}")
                if retry_count >= self.max_retries:
                    self.standard_print("ERROR", f"下载最终失败: {e}")
                    import traceback
                    traceback.print_exc()
                    return False
                else:
                    time.sleep(2)

        return False

    async def download_from_page(self, download_page_url: str, worker=None, task_logger=None, task_id=None) -> bool:
        video_url, filename = await self.extract_download_info(download_page_url, worker)
        if video_url and filename:
            success = self.download_video(video_url, filename, worker, task_logger, task_id)
            return success
        else:
            self.standard_print("WARNING", "无法提取下载信息")
            return False


class HanimeScraper:
    def __init__(self, max_workers=2, headless=True, download_dir="./downloads", task_logger=None, task_id=None, config_manager=None):
        self.all_video_links = set()
        self.download_links = []
        self.downloader = VideoDownloader(download_dir=download_dir, headless=headless)
        self.max_workers = max_workers
        self.task_logger = task_logger
        self.task_id = task_id
        self.headless = headless
        self.config_manager = config_manager

        self.link_queue = queue.Queue()
        self.results = []
        self.active_threads = 0
        self.lock = threading.Lock()

        self.downloading_files = {}
        self.downloading_files_lock = threading.Lock()

        self.processing_links = {}
        self.processing_links_lock = threading.Lock()

        self.failed_links = []

        # 使用 weakref.WeakSet 管理浏览器，避免手动追踪
        self._active_browsers = weakref.WeakSet()
        self._browsers_lock = threading.Lock()

    async def _track_browser(self, browser: BrowserManager):
        """注册浏览器管理器"""
        with self._browsers_lock:
            self._active_browsers.add(browser)

    async def close_all_browsers(self):
        """强制关闭所有被追踪的浏览器实例"""
        browsers_to_close = []
        with self._browsers_lock:
            browsers_to_close = list(self._active_browsers)
            self._active_browsers.clear()

        for browser in browsers_to_close:
            try:
                await browser.close()
            except Exception as e:
                print(f"关闭浏览器失败: {e}")

    def get_failed_links(self):
        with self.lock:
            return self.failed_links.copy()

    def reset_link_tracking(self):
        with self.lock:
            self.failed_links = []
            self.download_links = []
            self.all_video_links = set()

    def add_processing(self, link: str, progress: str):
        with self.processing_links_lock:
            self.processing_links[link] = progress

    def remove_processing(self, link: str):
        with self.processing_links_lock:
            if link in self.processing_links:
                del self.processing_links[link]

    def promote_to_downloading(self, link: str, filename: str, initial_progress: str = "0%"):
        with self.processing_links_lock, self.downloading_files_lock:
            if link in self.processing_links:
                del self.processing_links[link]
            self.downloading_files[filename] = initial_progress

    async def check_pause(self, worker) -> bool:
        if worker and hasattr(worker, 'should_pause'):
            return worker.should_pause()
        return False

    async def check_stop(self, worker) -> bool:
        if worker and hasattr(worker, 'should_stop'):
            return worker.should_stop()
        return False

    def standard_print(self, level: str, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {level.upper()} - {message}")

    async def get_video_links(self, start_url: str, worker=None) -> List[str]:
        if await self.check_stop(worker):
            self.standard_print("WARNING", "获取视频链接前检测到停止指令")
            return []
        if await self.check_pause(worker):
            self.standard_print("WARNING", "获取视频链接前检测到暂停指令")
            return []

        async with BrowserManager(headless=self.headless, download_dir=self.downloader.download_dir,
                                 config_manager=self.config_manager) as browser:
            await self._track_browser(browser)
            try:
                # 首次尝试不使用 bypass
                await browser.go_to(start_url, use_bypass=False)
                await asyncio.sleep(3)

                if await self.check_stop(worker):
                    self.standard_print("WARNING", "页面加载后检测到停止指令")
                    return []
                if await self.check_pause(worker):
                    self.standard_print("WARNING", "页面加载后检测到暂停指令")
                    return []

                playlist_container = await browser.query_element('//*[@id="playlist-scroll"]', timeout=10, raise_exc=False)

                if playlist_container:
                    link_elements = await browser.find_element(
                        xpath='//*[@id="playlist-scroll"]//a[@href]',
                        find_all=True,
                        timeout=10,
                        raise_exc=False
                    )

                    if link_elements:
                        for element in link_elements:
                            if await self.check_stop(worker):
                                self.standard_print("WARNING", "处理视频链接时检测到停止指令")
                                return list(self.all_video_links)
                            if await self.check_pause(worker):
                                self.standard_print("WARNING", "处理视频链接时检测到暂停指令")
                                return list(self.all_video_links)

                            try:
                                href = await element.get_attribute('href')
                            except:
                                href = element.get_attribute('href')

                            if href and isinstance(href, str) and href.startswith('https://hanime1.me/watch?v='):
                                self.all_video_links.add(href)
                else:
                    self.standard_print("WARNING", "未找到playlist-scroll容器")

                video_links_list = list(self.all_video_links)
                if video_links_list:
                    self.standard_print("INFO", f"获取到 {len(video_links_list)} 个视频链接:")
                    for i, link in enumerate(video_links_list, 1):
                        self.standard_print("INFO", f"  {i}. {link}")
                else:
                    self.standard_print("WARNING", "未获取到任何视频链接")

                if self.task_logger and self.task_id:
                    self.task_logger.add_video_links(self.task_id, video_links_list)

                return video_links_list

            finally:
                pass  # 浏览器由上下文管理器自动关闭

    def update_progress(self, filename: str, progress: str):
        with self.downloading_files_lock:
            self.downloading_files[filename] = progress

    def remove_progress(self, filename: str):
        with self.downloading_files_lock:
            if filename in self.downloading_files:
                del self.downloading_files[filename]

    def get_progress_text(self) -> str:
        with self.downloading_files_lock, self.processing_links_lock:
            if not self.downloading_files and not self.processing_links:
                return "等待开始..."

            progress_lines = []
            for filename, progress in self.downloading_files.items():
                display_name = filename[:20] + "..." if len(filename) > 20 else filename
                progress_lines.append(f"{display_name}: {progress}")

            for link, progress in self.processing_links.items():
                progress_lines.append(progress)

            return "\n".join(progress_lines)

    def _handle_video_download(self, video_url: str, filename: str, worker, original_url: str) -> bool:
        safe_filename = self.downloader._sanitize_filename(filename)
        self.promote_to_downloading(original_url, safe_filename, "0%")

        try:
            success = self.downloader.download_video(video_url, safe_filename, worker,
                                                     self.task_logger, self.task_id)
            if success:
                if self.task_logger and self.task_id:
                    self.task_logger.remove_failed_link(self.task_id, original_url)
                    self.task_logger.remove_video_link(self.task_id, original_url)
                with self.lock:
                    if original_url in self.failed_links:
                        self.failed_links.remove(original_url)
                return True
            else:
                self.remove_progress(safe_filename)
                if self.task_logger and self.task_id:
                    self.task_logger.add_failed_link(self.task_id, original_url, "download_failed")
                with self.lock:
                    if original_url not in self.failed_links:
                        self.failed_links.append(original_url)
                return False
        except Exception as e:
            self.standard_print("ERROR", f"下载视频时发生异常: {e}")
            self.remove_progress(safe_filename)
            if self.task_logger and self.task_id:
                self.task_logger.add_failed_link(self.task_id, original_url, f"exception:{str(e)}")
            with self.lock:
                if original_url not in self.failed_links:
                    self.failed_links.append(original_url)
            return False

    async def _process_single_link_with_browser(self, video_url: str, browser: BrowserManager,
                                                worker) -> bool:
        """
        使用给定的浏览器实例处理单个链接，直接处理下载页面
        """
        original_watch_url = video_url  # 保存原始链接（可能是watch或download）
        if '/watch?' in video_url:
            download_url = video_url.replace('/watch?', '/download?')
        else:
            download_url = video_url

        self.standard_print("INFO", f"处理链接: {download_url}")

        # 提取视频ID用于显示
        video_id_match = re.search(r'download\?v=([^&]+)', download_url)
        if video_id_match:
            display_text = f"正在处理: {video_id_match.group(1)}"
        else:
            display_text = f"正在处理: {download_url[:30]}..."
        self.add_processing(original_watch_url, display_text)

        if await self.check_stop(worker):
            self.standard_print("WARNING", "处理链接前检测到停止指令")
            self.remove_processing(original_watch_url)
            return False
        if await self.check_pause(worker):
            self.standard_print("WARNING", "处理链接前检测到暂停指令")
            self.remove_processing(original_watch_url)
            return False

        # 最多尝试两次：第一次不使用 bypass，第二次使用 bypass
        for attempt, use_bypass in enumerate([False, True]):
            try:
                if await self.check_stop(worker) or await self.check_pause(worker):
                    self.remove_processing(original_watch_url)
                    return False

                # 提取下载信息（复用浏览器）
                video_url_real, filename = await self.downloader.extract_download_info(
                    download_url, worker, browser=browser
                )
                if not video_url_real or not filename:
                    if attempt == 0:
                        self.standard_print("WARNING", f"提取信息失败，可能遇到验证码，准备使用 bypass 重试: {download_url}")
                        continue
                    else:
                        self.standard_print("WARNING", f"✗ 提取下载信息失败: {download_url}")
                        self.remove_processing(original_watch_url)
                        if self.task_logger and self.task_id:
                            self.task_logger.add_failed_link(self.task_id, original_watch_url, "extract_info_failed")
                        with self.lock:
                            self.failed_links.append(original_watch_url)
                        return False

                # 下载视频
                return self._handle_video_download(video_url_real, filename, worker, original_watch_url)

            except Exception as e:
                self.standard_print("ERROR", f"处理链接 {download_url} 时出错: {e}")
                if attempt == 0:
                    self.standard_print("WARNING", "尝试启用 bypass 重试")
                    continue
                else:
                    self.remove_processing(original_watch_url)
                    if self.task_logger and self.task_id:
                        self.task_logger.add_failed_link(self.task_id, original_watch_url, f"browser_error:{str(e)}")
                    with self.lock:
                        self.failed_links.append(original_watch_url)
                    return False

        # 如果执行到这里，说明两次都失败了
        self.remove_processing(original_watch_url)
        return False

    def _process_worker(self, worker):
        """
        工作线程函数，每个线程持有一个浏览器实例，循环处理多个链接
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            async def run():
                async with BrowserManager(headless=self.headless,
                                         download_dir=self.downloader.download_dir,
                                         config_manager=self.config_manager) as browser:
                    await self._track_browser(browser)
                    while True:
                        try:
                            video_url = self.link_queue.get(timeout=1)
                        except queue.Empty:
                            if self.link_queue.unfinished_tasks == 0:
                                break
                            continue

                        try:
                            success = await self._process_single_link_with_browser(
                                video_url, browser, worker
                            )
                            with self.lock:
                                self.results.append(success)
                        except Exception as e:
                            self.standard_print("ERROR", f"线程处理链接失败: {video_url}, 错误: {e}")
                            with self.lock:
                                self.results.append(False)
                        finally:
                            self.link_queue.task_done()

            loop.run_until_complete(run())
        finally:
            loop.close()

        with self.lock:
            self.active_threads -= 1

    async def process_links_batch(self, links_batch: List[str], worker=None) -> None:
        self.standard_print("INFO", f"开始批量处理 {len(links_batch)} 个链接，使用 {self.max_workers} 个工作线程（复用浏览器）")

        # 清空队列
        while not self.link_queue.empty():
            self.link_queue.get_nowait()
            self.link_queue.task_done()

        # 将所有链接转换为下载页面链接后放入队列
        for link in links_batch:
            if '/watch?' in link:
                download_link = link.replace('/watch?', '/download?')
            else:
                download_link = link
            self.link_queue.put(download_link)

        self.active_threads = self.max_workers
        threads = []

        for i in range(self.max_workers):
            thread = threading.Thread(target=self._process_worker, args=(worker,))
            thread.daemon = True
            thread.start()
            threads.append(thread)

        self.link_queue.join()

        for thread in threads:
            thread.join()

        success_count = sum(1 for r in self.results if r)
        self.standard_print("INFO", f"处理完成: 成功 {success_count}/{len(links_batch)} 个链接")


class DownloadWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)
    progress_signal = pyqtSignal(str)
    file_progress_signal = pyqtSignal(str, str)
    count_updated_signal = pyqtSignal()

    # 进度节流相关
    _last_progress_time = {}  # 文件名 -> 上次发送时间
    _progress_lock = threading.Lock()

    def __init__(self, url: str, download_dir: str, headless: bool, task_logger=None, task_id=None, config_manager=None, is_restored=False):
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.headless = headless
        self._is_running = True
        self._is_paused = False
        self._pause_event = threading.Event()      # 用于暂停等待
        self._pause_event.set()                     # 初始为未暂停
        self.task_logger = task_logger
        self.task_id = task_id
        self.config_manager = config_manager
        self.last_error = ""
        self.current_filename = ""
        self.scraper = None
        self.retry_failed_links = False
        self.failed_links_to_retry = []
        self.is_restored = is_restored

        if task_logger and task_id:
            self.failed_links_to_retry = task_logger.get_task_failed_links(task_id)
            self.retry_failed_links = bool(self.failed_links_to_retry)

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self.log_signal.emit(f"开始处理链接: {self.url}")
            self._detect_headless_setting()
            self.log_signal.emit(f"当前无头模式设置: {'启用' if self.headless else '禁用'}")

            self.scraper = HanimeScraper(
                max_workers=2,
                headless=self.headless,
                download_dir=self.download_dir,
                task_logger=self.task_logger,
                task_id=self.task_id,
                config_manager=self.config_manager
            )

            self.scraper.downloader.set_progress_callback(self.on_progress_update)

            success = loop.run_until_complete(self._async_run())

            if not self._is_running or self._is_paused:
                self.log_signal.emit(f"任务被停止或暂停: {self.url}")
                self.finished_signal.emit(False)
                return

            self.finished_signal.emit(success)

        except Exception as e:
            error_msg = f"下载任务出错: {str(e)}"
            self.log_signal.emit(error_msg)
            self.last_error = error_msg

            if self.task_logger and self.task_id:
                self.task_logger.add_failed_link(self.task_id, self.url, "download_error")

            import traceback
            traceback.print_exc()
            self.finished_signal.emit(False)
        finally:
            loop.close()
            if self.scraper:
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.scraper.close_all_browsers())
                    loop.close()
                except Exception as e:
                    print(f"关闭浏览器时出错: {e}")

    def on_progress_update(self, filename: str, progress: str):
        current_time = time.time()
        with self._progress_lock:
            last_time = self._last_progress_time.get(filename, 0)
            if current_time - last_time < 0.2 and progress != "100.0%" and progress != "已完成":
                return
            self._last_progress_time[filename] = current_time

        self.current_filename = filename
        if self.scraper:
            if progress == "已完成" or progress.startswith("100.0%"):
                self.scraper.remove_progress(filename)
                self.count_updated_signal.emit()
            else:
                self.scraper.update_progress(filename, progress)

            progress_text = self.scraper.get_progress_text()
            self.progress_signal.emit(progress_text)
            self.file_progress_signal.emit(filename, progress)

    def get_current_filename(self) -> str:
        return self.current_filename

    async def _async_run(self):
        if self.should_stop():
            return False
        if self.should_pause():
            return False

        if self.is_restored and self.task_logger and self.task_id:
            all_tasks = self.task_logger.get_all_tasks()
            task_data = all_tasks.get(self.task_id, {})
            stored_video_links = task_data.get('video_links', [])
            if stored_video_links:
                self.log_signal.emit(f"恢复任务，使用已存储的 {len(stored_video_links)} 个视频链接")
                video_links = stored_video_links
            else:
                self.log_signal.emit("未找到已存储的视频链接，尝试重新抓取")
                video_links = await self.scraper.get_video_links(self.url, self)
        else:
            video_links = await self.scraper.get_video_links(self.url, self)

        if self.should_stop():
            return False
        if self.should_pause():
            return False

        if not video_links:
            self.log_signal.emit("没有找到任何视频链接")
            return False

        if self.retry_failed_links and self.failed_links_to_retry:
            self.log_signal.emit(f"开始重试 {len(self.failed_links_to_retry)} 个失败的链接...")
            # 失败链接为原始 watch 链接，process_links_batch 内部会转换
            await self.scraper.process_links_batch(self.failed_links_to_retry, self)

            remaining_failed = self.scraper.get_failed_links()
            if remaining_failed:
                self.log_signal.emit(f"仍有 {len(remaining_failed)} 个链接失败，任务将暂停")
                if self.task_logger and self.task_id:
                    self.task_logger.clear_failed_links(self.task_id)
                    for link in remaining_failed:
                        self.task_logger.add_failed_link(self.task_id, link, "retry_failed")
                self.failed_links_to_retry = remaining_failed
                return False
            else:
                self.log_signal.emit("所有链接重试成功！")
                if self.task_logger and self.task_id:
                    self.task_logger.clear_failed_links(self.task_id)
                return True

        self.count_updated_signal.emit()

        self.log_signal.emit(f"开始处理 {len(video_links)} 个视频链接...")
        await self.scraper.process_links_batch(video_links, self)

        failed_links = self.scraper.get_failed_links()
        if failed_links:
            self.log_signal.emit(f"发现 {len(failed_links)} 个失败链接，任务将暂停以便重试")
            if self.task_logger and self.task_id:
                for link in failed_links:
                    self.task_logger.add_failed_link(self.task_id, link, "initial_failed")
            self.failed_links_to_retry = failed_links
            return False
        else:
            self.log_signal.emit(f"处理完成！共找到 {len(self.scraper.download_links)} 个下载链接")
            return True

    def stop(self):
        self._is_running = False
        self._pause_event.set()
        if self.scraper:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.scraper.close_all_browsers())
                loop.close()
            except Exception as e:
                print(f"停止时关闭浏览器出错: {e}")

    def pause(self):
        self._is_paused = True
        self._pause_event.clear()

    def resume(self):
        self._is_paused = False
        self._pause_event.set()

    def reload_failed_links_from_logger(self):
        if self.task_logger and self.task_id:
            self.failed_links_to_retry = self.task_logger.get_task_failed_links(self.task_id)
            self.retry_failed_links = bool(self.failed_links_to_retry)

    def should_pause(self) -> bool:
        if not self._is_paused:
            return False
        while self._is_paused and self._is_running:
            self._pause_event.wait(0.1)
        return False

    def should_stop(self) -> bool:
        return not self._is_running

    def is_running(self) -> bool:
        return self._is_running

    def _detect_headless_setting(self) -> None:
        try:
            from ToolPart.Config import ConfigManager
            config_manager = ConfigManager()
            latest_headless = config_manager.get("headless_mode", True)
            if self.headless != latest_headless:
                self.log_signal.emit(f"检测到无头模式设置变更: {self.headless} -> {latest_headless}")
                self.headless = latest_headless
        except Exception as e:
            self.log_signal.emit(f"检测无头模式设置时出错: {str(e)}，使用原有设置: {self.headless}")