import asyncio
import os
import queue
import threading
import time
import re
import logging
from typing import List, Optional, Tuple
from urllib.parse import unquote

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from ToolPart.BrowserManager import BrowserManager

logger = logging.getLogger(__name__)


class VideoDownloader:
    def __init__(self, download_dir="./downloads", max_retries=3, headless=True):
        self.download_dir = download_dir
        self.max_retries = max_retries
        self.headless = headless
        os.makedirs(self.download_dir, exist_ok=True)
        self.progress_callback = None
        self._session = None

    def set_progress_callback(self, callback):
        self.progress_callback = callback

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
        return self._session

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        filename = unquote(filename)
        if len(filename) > 200:
            name, ext = os.path.splitext(filename)
            filename = name[:190] + ext
        return filename

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    async def check_pause(self, worker) -> bool:
        if worker and hasattr(worker, 'should_pause'):
            return worker.should_pause()
        return False

    async def check_stop(self, worker) -> bool:
        if worker and hasattr(worker, 'should_stop'):
            return worker.should_stop()
        return False

    async def extract_download_info(self, download_page_url: str, worker=None, browser: BrowserManager = None,
                                    use_bypass: bool = False) -> Tuple[Optional[str], Optional[str]]:
        """
        提取下载信息，可复用已存在的浏览器实例
        :param download_page_url: 下载页面URL
        :param worker: 工作线程实例
        :param browser: 可选的浏览器管理器
        :param use_bypass: 是否启用验证码绕过
        """
        if await self.check_stop(worker):
            logger.warning("提取下载信息前检测到停止指令")
            return None, None
        if await self.check_pause(worker):
            logger.warning("浏览器操作被暂停")
            return None, None

        need_close = False
        if browser is None:
            browser = BrowserManager(headless=self.headless, download_dir=self.download_dir, use_bypass=use_bypass)
            await browser.start()
            need_close = True

        try:
            logger.info(f"访问下载页面: {download_page_url}")
            await browser.go_to(download_page_url, use_bypass=use_bypass)
            # 等待表格出现
            table_element = await browser.wait_for_element(
                '//*[@id="content-div"]/div[1]/div[4]/div/div/table',
                timeout=10
            )
            if not table_element:
                logger.warning("未找到下载链接列表表格")
                return None, None

            logger.info("找到下载链接列表表格")

            # 查找下载按钮
            first_download_btn = await browser.wait_for_element(
                '//*[@id="content-div"]/div[1]/div[4]/div/div/table/tbody/tr[2]/td[5]/a',
                timeout=5
            )
            if not first_download_btn:
                logger.warning("未找到第一个下载按钮")
                return None, None

            download_url = first_download_btn.get_attribute('data-url')
            filename = first_download_btn.get_attribute('download')
            if not filename:
                filename = await first_download_btn.text

            if filename:
                filename = filename.strip()
                if not filename.endswith('.mp4'):
                    filename += '.mp4'

            logger.info(f"提取到下载链接: {download_url}")
            logger.info(f"提取到文件名: {filename}")

            return download_url, filename

        except Exception as e:
            logger.exception("提取下载信息时出错")
            return None, None
        finally:
            if need_close and browser:
                await browser.close()

    def download_video(self, video_url: str, filename: str, worker=None, task_logger=None, task_id=None) -> bool:
        if not video_url or not filename:
            logger.warning("下载链接或文件名为空，跳过下载")
            return False

        safe_filename = self._sanitize_filename(filename)
        downloading_filename = f"下载中_{safe_filename}"
        downloading_file_path = os.path.join(self.download_dir, downloading_filename)
        final_file_path = os.path.join(self.download_dir, safe_filename)

        if os.path.exists(final_file_path):
            logger.info(f"文件已存在，跳过下载: {final_file_path}")
            if task_logger and task_id:
                task_logger.add_downloaded_video(task_id, safe_filename)
                if self.progress_callback:
                    self.progress_callback(safe_filename, "已完成")
            return True

        if os.path.exists(downloading_file_path):
            logger.warning(f"发现之前的下载中文件: {downloading_file_path}")
            try:
                os.remove(downloading_file_path)
                logger.info("已删除之前的下载中文件")
            except Exception as e:
                logger.error(f"删除之前下载文件失败: {e}")
                return False

        logger.info(f"开始下载: {safe_filename} (临时文件: {downloading_filename})")

        if self.progress_callback:
            self.progress_callback(safe_filename, "0%")

        retry_count = 0
        CHECK_INTERVAL = 0.5  # 每0.5秒检查一次暂停/停止

        while retry_count < self.max_retries:
            try:
                response = self.session.get(video_url, stream=True, timeout=30)
                response.raise_for_status()

                total_size = int(response.headers.get('content-length', 0))
                downloaded_size = 0
                last_check_time = time.time()

                os.makedirs(self.download_dir, exist_ok=True)

                with open(downloading_file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)

                            # 基于时间检查暂停/停止
                            now = time.time()
                            if now - last_check_time >= CHECK_INTERVAL:
                                last_check_time = now
                                if worker and hasattr(worker, 'should_stop') and worker.should_stop():
                                    logger.warning("下载已被停止")
                                    response.close()
                                    if os.path.exists(downloading_file_path):
                                        try:
                                            os.remove(downloading_file_path)
                                            logger.info("已删除临时文件")
                                        except Exception as e:
                                            logger.error(f"删除临时文件失败: {e}")
                                    return False

                                if worker and hasattr(worker, 'should_pause') and worker.should_pause():
                                    logger.warning("下载已被暂停")
                                    while worker.should_pause() and not worker.should_stop():
                                        time.sleep(0.1)
                                    if worker.should_stop():
                                        return False

                            if total_size > 0:
                                progress = (downloaded_size / total_size) * 100
                                progress_str = f"{progress:.1f}% ({self._format_size(downloaded_size)}/{self._format_size(total_size)})"
                                if self.progress_callback:
                                    self.progress_callback(safe_filename, progress_str)
                            else:
                                if self.progress_callback:
                                    self.progress_callback(safe_filename, f"{self._format_size(downloaded_size)}")

                logger.info("下载完成，正在重命名文件...")

                try:
                    os.replace(downloading_file_path, final_file_path)
                    logger.info(f"文件重命名完成: {final_file_path}")
                    if self.progress_callback:
                        self.progress_callback(safe_filename, "100.0%")
                except Exception as e:
                    logger.error(f"文件重命名失败: {e}")
                    return False

                if task_logger and task_id:
                    # 传递 video_url，以便从 video_links 中移除
                    task_logger.add_downloaded_video(task_id, safe_filename, video_url=video_url)

                return True

            except Exception as e:
                retry_count += 1
                wait_time = min(2 ** retry_count, 30)  # 指数退避
                logger.warning(f"下载失败 (重试 {retry_count}/{self.max_retries})，等待 {wait_time} 秒: {e}")
                if retry_count >= self.max_retries:
                    logger.error("下载最终失败")
                    return False
                else:
                    time.sleep(wait_time)

        return False

    async def download_from_page(self, download_page_url: str, worker=None, task_logger=None, task_id=None,
                                 use_bypass: bool = False) -> bool:
        video_url, filename = await self.extract_download_info(download_page_url, worker, use_bypass=use_bypass)
        if video_url and filename:
            return self.download_video(video_url, filename, worker, task_logger, task_id)
        else:
            logger.warning("无法提取下载信息")
            return False


class HanimeScraper:
    def __init__(self, max_workers=2, headless=True, download_dir="./downloads", task_logger=None, task_id=None, config_manager=None, use_bypass=False):
        self.all_video_links = set()
        self.download_links = []
        self.downloader = VideoDownloader(download_dir=download_dir, headless=headless)
        self.max_workers = max_workers
        self.task_logger = task_logger
        self.task_id = task_id
        self.headless = headless
        self.config_manager = config_manager
        self.use_bypass = use_bypass  # 新增：是否使用Bypass模式

        self.link_queue = queue.Queue()
        self.results = []
        self.active_threads = 0
        self.lock = threading.Lock()

        self.downloading_files = {}
        self.downloading_files_lock = threading.Lock()

        self.processing_links = {}
        self.processing_links_lock = threading.Lock()

        self.failed_links = []

        # 使用普通 set 管理浏览器实例，关闭后移除
        self._active_browsers = set()
        self._browsers_lock = threading.Lock()

    def _track_browser(self, browser: BrowserManager):
        """注册浏览器管理器"""
        with self._browsers_lock:
            self._active_browsers.add(browser)

    def _untrack_browser(self, browser: BrowserManager):
        """注销浏览器管理器"""
        with self._browsers_lock:
            self._active_browsers.discard(browser)

    async def close_all_browsers(self):
        """强制关闭所有被追踪的浏览器实例（线程安全）"""
        browsers_to_close = []
        with self._browsers_lock:
            browsers_to_close = list(self._active_browsers)
            self._active_browsers.clear()

        for browser in browsers_to_close:
            try:
                await browser.close()
            except Exception as e:
                logger.error(f"关闭浏览器失败: {e}")

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

    async def get_video_links(self, start_url: str, worker=None) -> List[str]:
        if await self.check_stop(worker):
            logger.warning("获取视频链接前检测到停止指令")
            return []
        if await self.check_pause(worker):
            logger.warning("获取视频链接前检测到暂停指令")
            return []

        async with BrowserManager(headless=self.headless, download_dir=self.downloader.download_dir,
                                 config_manager=self.config_manager, use_bypass=False) as browser:
            self._track_browser(browser)
            try:
                # 抓取链接时始终不使用 bypass
                await browser.go_to(start_url, use_bypass=False)
                # 等待 playlist-scroll 出现
                playlist_container = await browser.wait_for_element('//*[@id="playlist-scroll"]', timeout=10)
                if playlist_container:
                    link_elements = await browser.find_element(
                        xpath='//*[@id="playlist-scroll"]//a[@href]',
                        find_all=True,
                        timeout=10,
                        raise_exc=False
                    )

                    if link_elements:
                        for element in link_elements:
                            if await self.check_stop(worker) or await self.check_pause(worker):
                                return list(self.all_video_links)

                            try:
                                href = await element.get_attribute('href')
                            except:
                                href = element.get_attribute('href')

                            if href and isinstance(href, str) and href.startswith('https://hanime1.me/watch?v='):
                                self.all_video_links.add(href)
                else:
                    logger.warning("未找到playlist-scroll容器")

                video_links_list = list(self.all_video_links)
                logger.info(f"获取到 {len(video_links_list)} 个视频链接")

                if self.task_logger and self.task_id:
                    self.task_logger.add_video_links(self.task_id, video_links_list)

                return video_links_list

            finally:
                self._untrack_browser(browser)

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
                # 下载成功后，从 failed_links 和 video_links 中移除
                if self.task_logger and self.task_id:
                    # 从 failed_links 中移除（如果存在）
                    self.task_logger.remove_failed_link(self.task_id, original_url)
                    # 注意：add_downloaded_video 已经会从 video_links 中移除
                with self.lock:
                    if original_url in self.failed_links:
                        self.failed_links.remove(original_url)
                return True
            else:
                self.remove_progress(safe_filename)
                # 下载失败，添加到 failed_links（会自动从 video_links 移到 failed_links）
                if self.task_logger and self.task_id:
                    self.task_logger.add_failed_link(self.task_id, original_url, "download_failed")
                with self.lock:
                    if original_url not in self.failed_links:
                        self.failed_links.append(original_url)
                return False
        except Exception as e:
            logger.exception(f"下载视频时发生异常")
            self.remove_progress(safe_filename)
            # 异常失败，也添加到 failed_links
            if self.task_logger and self.task_id:
                self.task_logger.add_failed_link(self.task_id, original_url, f"exception:{str(e)}")
            with self.lock:
                if original_url not in self.failed_links:
                    self.failed_links.append(original_url)
            return False

    async def _process_single_link_with_browser(self, video_url: str, browser: BrowserManager,
                                                worker) -> bool:
        original_watch_url = video_url
        if '/watch?' in video_url:
            download_url = video_url.replace('/watch?', '/download?')
        else:
            download_url = video_url

        logger.info(f"处理链接: {download_url}")

        video_id_match = re.search(r'download\?v=([^&]+)', download_url)
        if video_id_match:
            display_text = f"正在处理: {video_id_match.group(1)}"
        else:
            display_text = f"正在处理: {download_url[:30]}..."
        self.add_processing(original_watch_url, display_text)

        if await self.check_stop(worker):
            logger.warning("处理链接前检测到停止指令")
            self.remove_processing(original_watch_url)
            return False
        if await self.check_pause(worker):
            logger.warning("处理链接前检测到暂停指令")
            self.remove_processing(original_watch_url)
            return False

        try:
            # 根据 use_bypass 决定是否启用绕过
            video_url_real, filename = await self.downloader.extract_download_info(
                download_url, worker, browser=browser, use_bypass=self.use_bypass
            )
            if not video_url_real or not filename:
                self.remove_processing(original_watch_url)
                return False

            return self._handle_video_download(video_url_real, filename, worker, original_watch_url)

        except Exception as e:
            logger.exception(f"处理链接 {download_url} 时出错")
            self.remove_processing(original_watch_url)
            if self.task_logger and self.task_id:
                self.task_logger.add_failed_link(self.task_id, original_watch_url, f"browser_error:{str(e)}")
            with self.lock:
                self.failed_links.append(original_watch_url)
            return False

    def _process_worker(self, worker):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            async def run():
                async with BrowserManager(headless=self.headless,
                                         download_dir=self.downloader.download_dir,
                                         config_manager=self.config_manager,
                                         use_bypass=self.use_bypass) as browser:
                    self._track_browser(browser)
                    try:
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
                                logger.exception(f"线程处理链接失败: {video_url}")
                                with self.lock:
                                    self.results.append(False)
                            finally:
                                self.link_queue.task_done()
                    finally:
                        self._untrack_browser(browser)

            loop.run_until_complete(run())
        finally:
            loop.close()

        with self.lock:
            self.active_threads -= 1

    async def process_links_batch(self, links_batch: List[str], worker=None) -> None:
        logger.info(f"开始批量处理 {len(links_batch)} 个链接，使用 {self.max_workers} 个工作线程")

        while not self.link_queue.empty():
            self.link_queue.get_nowait()
            self.link_queue.task_done()

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
        logger.info(f"处理完成: 成功 {success_count}/{len(links_batch)} 个链接")


class DownloadWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)
    progress_signal = pyqtSignal(str)
    file_progress_signal = pyqtSignal(str, str)
    count_updated_signal = pyqtSignal()

    # 进度节流相关
    _last_progress_time = {}
    _progress_lock = threading.Lock()

    def __init__(self, url: str, download_dir: str, headless: bool, use_bypass: bool, task_logger=None, task_id=None, config_manager=None, is_restored=False):
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.headless = headless
        self.use_bypass = use_bypass  # 新增：是否使用Bypass
        self._is_running = True
        self._is_paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self.task_logger = task_logger
        self.task_id = task_id
        self.config_manager = config_manager
        self.last_error = ""
        self.current_filename = ""
        self.scraper = None
        self.retry_failed_links = False
        self.failed_links_to_retry = []
        self.is_restored = is_restored
        self._loop = None  # 新增：保存事件循环引用

        if task_logger and task_id:
            self.failed_links_to_retry = task_logger.get_task_failed_links(task_id)
            self.retry_failed_links = bool(self.failed_links_to_retry)

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop  # 保存事件循环
        try:
            self.log_signal.emit(f"开始处理链接: {self.url}")
            self._detect_headless_setting()
            self.log_signal.emit(f"当前无头模式设置: {'启用' if self.headless else '禁用'}")
            self.log_signal.emit(f"当前Bypass模式设置: {'启用' if self.use_bypass else '禁用'}")

            self.scraper = HanimeScraper(
                max_workers=2,
                headless=self.headless,
                download_dir=self.download_dir,
                task_logger=self.task_logger,
                task_id=self.task_id,
                config_manager=self.config_manager,
                use_bypass=self.use_bypass  # 传递Bypass标志
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
            logger.exception("下载任务异常")
            if self.task_logger and self.task_id:
                self.task_logger.add_failed_link(self.task_id, self.url, "download_error")
            self.finished_signal.emit(False)
        finally:
            # 确保所有浏览器已关闭（同步等待）
            if self.scraper:
                try:
                    loop.run_until_complete(self.scraper.close_all_browsers())
                except Exception as e:
                    logger.error(f"关闭浏览器时出错: {e}")
            loop.close()
            self._loop = None  # 清除引用

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

        # 优先使用剩余的链接（包括 video_links 和 failed_links）
        remaining_links = []
        if self.is_restored and self.task_logger and self.task_id:
            all_tasks = self.task_logger.get_all_tasks()
            task_data = all_tasks.get(self.task_id, {})
            stored_video_links = task_data.get('video_links', [])
            stored_failed_links = task_data.get('failed_links', [])
            
            # 合并剩余的视频链接和失败链接
            if stored_video_links or stored_failed_links:
                remaining_links = list(set(stored_video_links + stored_failed_links))
                self.log_signal.emit(f"恢复任务，使用已存储的 {len(remaining_links)} 个剩余链接")
            else:
                self.log_signal.emit("未找到已存储的剩余链接，尝试重新抓取")
                video_links = await self.scraper.get_video_links(self.url, self)
                remaining_links = video_links
        else:
            # 新任务，需要抓取链接
            video_links = await self.scraper.get_video_links(self.url, self)
            remaining_links = video_links

        if self.should_stop() or self.should_pause():
            return False

        if not remaining_links:
            self.log_signal.emit("没有找到任何视频链接")
            return False

        # 将剩余链接转换为 download 链接格式（不重新获取视频列表）
        download_links_to_process = []
        for link in remaining_links:
            if '/watch?' in link:
                download_link = link.replace('/watch?', '/download?')
            else:
                download_link = link
            download_links_to_process.append(download_link)

        self.log_signal.emit(f"开始处理 {len(download_links_to_process)} 个剩余视频链接...")
        
        # 批量处理所有链接
        await self.scraper.process_links_batch(download_links_to_process, self)

        # 处理完成后，检查是否有失败链接
        failed_links = self.scraper.get_failed_links()
        if failed_links:
            self.log_signal.emit(f"发现 {len(failed_links)} 个失败链接，任务将暂停以便重试")
            if self.task_logger and self.task_id:
                # 将失败链接记录到 logger，失败链接会自动从 video_links 中移除
                for link in failed_links:
                    self.task_logger.add_failed_link(self.task_id, link, "initial_failed")
            self.failed_links_to_retry = failed_links
            # 任务暂停，等待用户手动继续
            return False
        else:
            self.log_signal.emit(f"处理完成！共下载 {len(self.scraper.download_links)} 个视频")
            return True

    def stop(self):
        self._is_running = False
        self._pause_event.set()
        # 向工作线程的事件循环投递关闭浏览器的任务，立即中断阻塞操作
        if self.scraper and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.scraper.close_all_browsers(), self._loop)

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