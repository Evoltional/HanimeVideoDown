import asyncio
import os
import queue
import threading
import time
import re
from typing import List, Optional, Tuple
from urllib.parse import unquote

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
        """检查是否应该停止"""
        if worker and hasattr(worker, 'should_stop'):
            return worker.should_stop()
        return False

    async def extract_download_info(self, download_page_url: str, worker=None) -> Tuple[Optional[str], Optional[str]]:
        if await self.check_stop(worker):
            print("提取下载信息前检测到停止指令")
            return None, None
        if await self.check_pause(worker):
            print("浏览器操作被暂停")
            return None, None

        async with BrowserManager(headless=self.headless, download_dir=self.download_dir) as browser:
            self.standard_print("INFO", f"访问下载页面: {download_page_url}")
            await browser.go_to_with_captcha_bypass(download_page_url)
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
                            chunk_counter += 1

                            if chunk_counter % 1 == 0:
                                # 先检查是否停止
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

                                # 再检查是否暂停
                                if worker and hasattr(worker, 'should_pause') and worker.should_pause():
                                    self.standard_print("WARNING", "\n下载已被暂停")
                                    return False

                            f.write(chunk)
                            downloaded_size += len(chunk)

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
                    os.rename(downloading_file_path, final_file_path)
                    self.standard_print("SUCCESS", f"文件重命名完成: {final_file_path}")
                    if self.progress_callback:
                        # 发送100%完成信号
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

        # ----- 新增：浏览器实例追踪 -----
        self._active_browsers = []          # 当前活跃的浏览器管理器列表
        self._browsers_lock = threading.Lock()
        # -----------------------------

    async def _track_browser(self, browser: BrowserManager):
        """注册浏览器管理器，确保停止时可关闭"""
        with self._browsers_lock:
            self._active_browsers.append(browser)

    async def _untrack_browser(self, browser: BrowserManager):
        """浏览器正常退出时移除注册"""
        with self._browsers_lock:
            if browser in self._active_browsers:
                self._active_browsers.remove(browser)

    async def close_all_browsers(self):
        """强制关闭所有被追踪的浏览器实例"""
        with self._browsers_lock:
            for browser in self._active_browsers[:]:
                try:
                    await browser.close()
                except Exception as e:
                    print(f"关闭浏览器失败: {e}")
            self._active_browsers.clear()
    # -----------------------------

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
        """检查是否应该停止"""
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
            # 注册浏览器
            await self._track_browser(browser)
            try:
                await browser.go_to_with_captcha_bypass(start_url)
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
                # 无论成功失败，都从追踪列表中移除
                await self._untrack_browser(browser)

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
                progress_lines.append(progress)  # progress 已经是友好显示

            return "\n".join(progress_lines)

    def _handle_video_download(self, video_url: str, filename: str, worker, original_url: str) -> bool:
        """
        统一处理视频下载逻辑
        :param video_url: 实际的视频下载链接
        :param filename: 原始文件名
        :param worker: DownloadWorker 实例
        :param original_url: 原始链接（用于记录成功/失败）
        :return: 是否成功
        """
        safe_filename = self.downloader._sanitize_filename(filename)
        self.promote_to_downloading(original_url, safe_filename, "0%")

        try:
            success = self.downloader.download_video(video_url, safe_filename, worker,
                                                     self.task_logger, self.task_id)
            if success:
                # 下载成功，从失败列表中移除（如果存在）
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

    async def process_download_page(self, download_url: str, worker=None, original_watch_url: str = None) -> bool:
        """
        直接处理下载页面（无需经过 watch 页面）
        :param download_url: 形如 https://hanime1.me/download?v=xxx 的链接
        :param worker: DownloadWorker 实例
        :param original_watch_url: 原始的 watch 链接，用于记录失败/成功
        :return: 是否成功
        """
        self.standard_print("INFO", f"直接处理下载页面: {download_url}")
        if original_watch_url is None:
            original_watch_url = download_url.replace('/download?', '/watch?')

        # 提取下载信息
        video_url, filename = await self.downloader.extract_download_info(download_url, worker)
        if not video_url or not filename:
            self.standard_print("WARNING", f"从下载页面提取信息失败: {download_url}")
            if self.task_logger and self.task_id:
                self.task_logger.add_failed_link(self.task_id, original_watch_url, "extract_failed")
            with self.lock:
                self.failed_links.append(original_watch_url)
            return False

        return self._handle_video_download(video_url, filename, worker, original_watch_url)

    async def process_single_link(self, video_url: str, worker=None) -> bool:
        # 判断是否为下载页面
        if '/download?' in video_url:
            # 已经是下载页面，直接处理
            original_watch = video_url.replace('/download?', '/watch?')
            return await self.process_download_page(video_url, worker, original_watch)

        self.standard_print("INFO", f"处理链接: {video_url}")
        video_id_match = re.search(r'watch\?v=([^&]+)', video_url)
        if video_id_match:
            display_text = f"正在处理: {video_id_match.group(1)}"
        else:
            display_text = f"正在处理: {video_url[:30]}..."
        self.add_processing(video_url, display_text)

        if await self.check_stop(worker):
            self.standard_print("WARNING", "处理链接前检测到停止指令")
            self.remove_processing(video_url)
            return False

        if await self.check_pause(worker):
            self.standard_print("WARNING", "处理链接前检测到暂停指令")
            self.remove_processing(video_url)
            return False

        max_retries = 2
        for attempt in range(max_retries):
            try:
                # 创建浏览器并注册
                async with BrowserManager(headless=self.headless, download_dir=self.downloader.download_dir,
                                          config_manager=self.config_manager) as browser:
                    await self._track_browser(browser)
                    try:
                        if await self.check_stop(worker):
                            self.standard_print("WARNING", "浏览器启动后检测到停止指令")
                            return False
                        if await self.check_pause(worker):
                            self.standard_print("WARNING", "浏览器启动后检测到暂停指令")
                            return False

                        await browser.go_to_with_captcha_bypass(video_url)
                        await asyncio.sleep(3)

                        if await self.check_stop(worker):
                            self.standard_print("WARNING", "页面加载后检测到停止指令")
                            return False
                        if await self.check_pause(worker):
                            self.standard_print("WARNING", "页面加载后检测到暂停指令")
                            return False

                        download_btn = None
                        for btn_attempt in range(3):
                            if await self.check_stop(worker):
                                self.standard_print("WARNING", "查找下载按钮时检测到停止指令")
                                return False
                            if await self.check_pause(worker):
                                self.standard_print("WARNING", "查找下载按钮时检测到暂停指令")
                                return False

                            download_btn = await browser.find_element(id='downloadBtn', timeout=5, raise_exc=False)
                            if download_btn:
                                break
                            await asyncio.sleep(1)

                        if not download_btn:
                            self.standard_print("WARNING", f"✗ 未找到下载按钮: {video_url}")
                            self.remove_processing(video_url)
                            # 记录失败链接
                            if self.task_logger and self.task_id:
                                self.task_logger.add_failed_link(self.task_id, video_url, "no_download_btn")
                            with self.lock:
                                self.failed_links.append(video_url)
                            return False

                        download_href = download_btn.get_attribute('href')
                        if not download_href:
                            self.standard_print("WARNING", f"✗ 未找到下载链接: {video_url}")
                            self.remove_processing(video_url)
                            if self.task_logger and self.task_id:
                                self.task_logger.add_failed_link(self.task_id, video_url, "no_download_href")
                            with self.lock:
                                self.failed_links.append(video_url)
                            return False

                        self.download_links.append(download_href)
                        self.standard_print("SUCCESS", f"✓ 找到下载链接: {download_href}")

                        # 提取下载信息
                        video_url_real, filename = await self.downloader.extract_download_info(download_href, worker)
                        if not video_url_real or not filename:
                            self.standard_print("WARNING", f"✗ 提取下载信息失败: {video_url}")
                            self.remove_processing(video_url)
                            if self.task_logger and self.task_id:
                                self.task_logger.add_failed_link(self.task_id, video_url, "extract_info_failed")
                            with self.lock:
                                self.failed_links.append(video_url)
                            return False

                        return self._handle_video_download(video_url_real, filename, worker, video_url)

                    except Exception as e:
                        self.standard_print("ERROR", f"处理链接 {video_url} 时出错: {e}")
                        self.remove_processing(video_url)
                        if "browser" in str(e).lower() or "disconnected" in str(e).lower() or "target closed" in str(e).lower():
                            if attempt < max_retries - 1:
                                self.standard_print("WARNING",
                                                    f"浏览器异常，准备重试 (尝试 {attempt + 2}/{max_retries})")
                                await asyncio.sleep(2)
                                continue
                        if self.task_logger and self.task_id:
                            self.task_logger.add_failed_link(self.task_id, video_url, f"browser_error:{str(e)}")
                        with self.lock:
                            self.failed_links.append(video_url)
                        return False
                    finally:
                        # 从追踪列表中移除
                        await self._untrack_browser(browser)
            except Exception as e:
                self.standard_print("ERROR", f"处理链接 {video_url} 时发生严重错误: {e}")
                self.remove_processing(video_url)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                else:
                    if self.task_logger and self.task_id:
                        self.task_logger.add_failed_link(self.task_id, video_url, f"critical:{str(e)}")
                    with self.lock:
                        self.failed_links.append(video_url)
                    return False
        return False

    def _process_link_thread(self, worker):
        while True:
            try:
                video_url = self.link_queue.get_nowait()
            except queue.Empty:
                break

            try:
                result = asyncio.run(self.process_single_link(video_url, worker))
                with self.lock:
                    self.results.append(result)
            except Exception as e:
                self.standard_print("ERROR", f"线程处理链接失败: {video_url}, 错误: {e}")
                with self.lock:
                    self.results.append(False)
            finally:
                self.link_queue.task_done()

        with self.lock:
            self.active_threads -= 1

    async def process_links_batch(self, links_batch: List[str], worker=None) -> None:
        self.standard_print("INFO", f"开始批量处理 {len(links_batch)} 个链接，使用 {self.max_workers} 个工作线程")

        for link in links_batch:
            self.link_queue.put(link)

        self.active_threads = min(self.max_workers, len(links_batch))
        threads = []

        for _ in range(self.active_threads):
            thread = threading.Thread(target=self._process_link_thread, args=(worker,))
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

    def __init__(self, url: str, download_dir: str, headless: bool, task_logger=None, task_id=None, config_manager=None, is_restored=False):
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.headless = headless
        self._is_running = True
        self._is_paused = False
        self._pause_condition = threading.Condition(threading.Lock())
        self.task_logger = task_logger
        self.task_id = task_id
        self.config_manager = config_manager
        self.last_error = ""
        self.current_filename = ""
        self.scraper = None
        self.retry_failed_links = False
        self.failed_links_to_retry = []
        self.is_restored = is_restored  # 新增：是否从存储恢复的任务

        # 从 logger 加载该任务的失败链接（如果有）
        if task_logger and task_id:
            self.failed_links_to_retry = task_logger.get_task_failed_links(task_id)
            self.retry_failed_links = bool(self.failed_links_to_retry)

    def run(self):
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

            success = asyncio.run(self._process_link(self.scraper))

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
            # 确保所有浏览器被关闭
            if self.scraper:
                try:
                    asyncio.run(self.scraper.close_all_browsers())
                except Exception as e:
                    print(f"关闭浏览器时出错: {e}")

    def on_progress_update(self, filename: str, progress: str):
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

    async def _process_link(self, scraper: HanimeScraper):
        if self.should_stop():
            return False
        if self.should_pause():
            return False

        # 如果是恢复的任务，直接使用存储的 video_links
        if self.is_restored and self.task_logger and self.task_id:
            all_tasks = self.task_logger.get_all_tasks()
            task_data = all_tasks.get(self.task_id, {})
            stored_video_links = task_data.get('video_links', [])
            if stored_video_links:
                self.log_signal.emit(f"恢复任务，使用已存储的 {len(stored_video_links)} 个视频链接")
                video_links = stored_video_links
            else:
                self.log_signal.emit("未找到已存储的视频链接，尝试重新抓取")
                video_links = await scraper.get_video_links(self.url, self)
        else:
            # 正常流程：获取视频链接列表
            video_links = await scraper.get_video_links(self.url, self)

        if self.should_stop():
            return False
        if self.should_pause():
            return False

        if not video_links:
            self.log_signal.emit("没有找到任何视频链接")
            return False

        # 重试失败链接（如果存在）
        if self.retry_failed_links and self.failed_links_to_retry:
            self.log_signal.emit(f"开始重试 {len(self.failed_links_to_retry)} 个失败的链接...")
            # 将 watch 链接转换为 download 链接
            download_links = [link.replace('/watch?', '/download?') for link in self.failed_links_to_retry]
            await scraper.process_links_batch(download_links, self)

            # 获取本次处理后仍然失败的链接（原始 watch 链接）
            remaining_failed = scraper.get_failed_links()
            if remaining_failed:
                self.log_signal.emit(f"仍有 {len(remaining_failed)} 个链接失败，任务将暂停")
                # 更新 logger 中的失败列表
                if self.task_logger and self.task_id:
                    self.task_logger.clear_failed_links(self.task_id)
                    for link in remaining_failed:
                        self.task_logger.add_failed_link(self.task_id, link, "retry_failed")
                self.failed_links_to_retry = remaining_failed
                return False
            else:
                self.log_signal.emit("所有链接重试成功！")
                # 清空 logger 中的失败列表
                if self.task_logger and self.task_id:
                    self.task_logger.clear_failed_links(self.task_id)
                return True

        self.count_updated_signal.emit()

        self.log_signal.emit(f"开始处理 {len(video_links)} 个视频链接...")
        await scraper.process_links_batch(video_links, self)

        failed_links = scraper.get_failed_links()
        if failed_links:
            self.log_signal.emit(f"发现 {len(failed_links)} 个失败链接，任务将暂停以便重试")
            # 将失败链接保存到 logger 和自身
            if self.task_logger and self.task_id:
                for link in failed_links:
                    self.task_logger.add_failed_link(self.task_id, link, "initial_failed")
            self.failed_links_to_retry = failed_links
            return False
        else:
            self.log_signal.emit(f"处理完成！共找到 {len(scraper.download_links)} 个下载链接")
            return True

    def stop(self):
        self._is_running = False
        with self._pause_condition:
            self._pause_condition.notify_all()

    def pause(self):
        self._is_paused = True
        with self._pause_condition:
            self._pause_condition.notify_all()

    def resume(self):
        self._is_paused = False
        with self._pause_condition:
            self._pause_condition.notify_all()

    def reload_failed_links_from_logger(self):
        """从 logger 重新加载失败链接，并设置重试标志"""
        if self.task_logger and self.task_id:
            self.failed_links_to_retry = self.task_logger.get_task_failed_links(self.task_id)
            self.retry_failed_links = bool(self.failed_links_to_retry)

    def should_pause(self) -> bool:
        if self._is_paused and self._is_running:
            with self._pause_condition:
                while self._is_paused and self._is_running:
                    self._pause_condition.wait(0.1)
        return False   # 返回 False 表示不继续（调用方应检查 should_stop 决定是否退出）

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