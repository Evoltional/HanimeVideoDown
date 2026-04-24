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

    # ---------- 修改点1：增加 watch_url 参数 ----------
    def download_video(self, video_url: str, filename: str, worker=None, task_logger=None, task_id=None,
                       watch_url=None) -> bool:
        """
        下载视频文件（增强网络波动抗性）
        :param video_url: 视频直链
        :param filename: 文件名
        :param worker: 工作线程
        :param task_logger: TaskLogger 实例
        :param task_id: 任务ID
        :param watch_url: 原始观看链接，用于从 video_links 中移除
        """
        if not video_url or not filename:
            logger.warning("下载链接或文件名为空，跳过下载")
            return False

        safe_filename = self._sanitize_filename(filename)
        downloading_filename = f"下载中_{safe_filename}"
        downloading_file_path = os.path.join(self.download_dir, downloading_filename)
        final_file_path = os.path.join(self.download_dir, safe_filename)

        # 检查文件是否已在存储目录中存在（通过Exis.json）
        if task_logger and task_logger.is_video_exists(safe_filename):
            logger.info(f"文件已存在于存储目录中（Exis.json记录），跳过下载: {safe_filename}")
            # 发送日志到UI
            if worker and hasattr(worker, 'log_signal'):
                worker.log_signal.emit(f"✓ 文件已存在（Exis.json记录），跳过下载: {safe_filename}")
            if task_logger and task_id:
                task_logger.add_downloaded_video(task_id, safe_filename,
                                                 video_url=watch_url if watch_url else video_url)
                if self.progress_callback:
                    self.progress_callback(safe_filename, "已完成")
            return True

        if os.path.exists(final_file_path):
            logger.info(f"文件已存在，跳过下载: {final_file_path}")
            # 发送日志到UI
            if worker and hasattr(worker, 'log_signal'):
                worker.log_signal.emit(f"✓ 文件已存在，跳过下载: {safe_filename}")
            if task_logger and task_id:
                task_logger.add_downloaded_video(task_id, safe_filename,
                                                 video_url=watch_url if watch_url else video_url)
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
        # 发送日志到UI
        if worker and hasattr(worker, 'log_signal'):
            worker.log_signal.emit(f"▶ 开始下载: {safe_filename}")

        if self.progress_callback:
            self.progress_callback(safe_filename, "0%")

        # 外层循环：整体重试次数（应对完全失败的情况）
        max_overall_retries = self.max_retries
        overall_retry_count = 0
        CHECK_INTERVAL = 0.5  # 每0.5秒检查一次暂停/停止
        
        # 内层配置：网络波动容忍度
        NETWORK_RETRY_DELAY = 3  # 网络波动时等待3秒
        MAX_NETWORK_RETRIES = 5  # 最多连续5次网络波动重试后才认为真正失败
        
        while overall_retry_count < max_overall_retries:
            network_retry_count = 0  # 每次整体重试时重置网络重试计数
            
            try:
                response = self.session.get(video_url, stream=True, timeout=30)
                response.raise_for_status()

                total_size = int(response.headers.get('content-length', 0))
                downloaded_size = 0
                last_check_time = time.time()
                consecutive_network_errors = 0  # 连续网络错误计数器

                os.makedirs(self.download_dir, exist_ok=True)

                with open(downloading_file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            try:
                                f.write(chunk)
                                downloaded_size += len(chunk)
                                consecutive_network_errors = 0  # 成功写入，重置计数器
                            except IOError as write_error:
                                # 磁盘写入错误，可能是网络导致的IO问题
                                consecutive_network_errors += 1
                                logger.warning(f"写入数据块时出错 ({consecutive_network_errors}/{MAX_NETWORK_RETRIES}): {write_error}")
                                
                                if consecutive_network_errors >= MAX_NETWORK_RETRIES:
                                    logger.error(f"连续{MAX_NETWORK_RETRIES}次写入失败，判定为严重错误")
                                    response.close()
                                    raise
                                
                                # 等待3秒后重试当前块
                                logger.info(f"等待{NETWORK_RETRY_DELAY}秒后重试...")
                                time.sleep(NETWORK_RETRY_DELAY)
                                continue

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
                    # 发送日志到UI
                    if worker and hasattr(worker, 'log_signal'):
                        worker.log_signal.emit(f"✓ 下载完成: {safe_filename}")
                    if self.progress_callback:
                        self.progress_callback(safe_filename, "100.0%")
                except Exception as e:
                    logger.error(f"文件重命名失败: {e}")
                    if worker and hasattr(worker, 'log_signal'):
                        worker.log_signal.emit(f"✗ 文件重命名失败: {safe_filename} - {str(e)}")
                    return False

                if task_logger and task_id:
                    # 使用 watch_url 移除对应的原始链接
                    task_logger.add_downloaded_video(task_id, safe_filename,
                                                     video_url=watch_url if watch_url else video_url)

                return True

            except requests.exceptions.ConnectionError as e:
                # 连接错误：典型的网络波动，等待3秒后重试当前整体请求
                network_retry_count += 1
                if network_retry_count <= MAX_NETWORK_RETRIES:
                    wait_time = NETWORK_RETRY_DELAY
                    logger.warning(f"网络连接波动 ({network_retry_count}/{MAX_NETWORK_RETRIES})，等待{wait_time}秒后重试: {e}")
                    # 发送日志到UI
                    if worker and hasattr(worker, 'log_signal'):
                        worker.log_signal.emit(f"⚠ 网络波动 ({network_retry_count}/{MAX_NETWORK_RETRIES})，{wait_time}秒后重试: {safe_filename}")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"连续{MAX_NETWORK_RETRIES}次网络波动，尝试整体重试")
                    # 发送日志到UI
                    if worker and hasattr(worker, 'log_signal'):
                        worker.log_signal.emit(f"✗ 网络波动严重，准备重试: {safe_filename}")
                    # 清理临时文件，进入下一轮整体重试
                    if os.path.exists(downloading_file_path):
                        try:
                            os.remove(downloading_file_path)
                            logger.info("已清理临时文件准备重试")
                        except Exception as cleanup_err:
                            logger.error(f"清理临时文件失败: {cleanup_err}")
                    
                    overall_retry_count += 1
                    if overall_retry_count < max_overall_retries:
                        wait_time = min(2 ** overall_retry_count, 30)
                        logger.info(f"整体重试 {overall_retry_count}/{max_overall_retries}，等待{wait_time}秒")
                        if worker and hasattr(worker, 'log_signal'):
                            worker.log_signal.emit(f"↻ 整体重试 {overall_retry_count}/{max_overall_retries}: {safe_filename}")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error("下载最终失败：超过最大重试次数")
                        if worker and hasattr(worker, 'log_signal'):
                            worker.log_signal.emit(f"✗ 下载失败（超过重试次数）: {safe_filename}")
                        return False
                        
            except requests.exceptions.Timeout as e:
                # 超时错误：也视为网络波动，等待3秒后重试当前整体请求
                network_retry_count += 1
                if network_retry_count <= MAX_NETWORK_RETRIES:
                    wait_time = NETWORK_RETRY_DELAY
                    logger.warning(f"请求超时 ({network_retry_count}/{MAX_NETWORK_RETRIES})，等待{wait_time}秒后重试: {e}")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"连续{MAX_NETWORK_RETRIES}次超时，尝试整体重试")
                    if os.path.exists(downloading_file_path):
                        try:
                            os.remove(downloading_file_path)
                            logger.info("已清理临时文件准备重试")
                        except Exception as cleanup_err:
                            logger.error(f"清理临时文件失败: {cleanup_err}")
                    
                    overall_retry_count += 1
                    if overall_retry_count < max_overall_retries:
                        wait_time = min(2 ** overall_retry_count, 30)
                        logger.info(f"整体重试 {overall_retry_count}/{max_overall_retries}，等待{wait_time}秒")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error("下载最终失败：超过最大重试次数")
                        return False
                        
            except requests.exceptions.RequestException as e:
                # 其他请求异常：先尝试网络波动重试，再整体重试
                network_retry_count += 1
                if network_retry_count <= MAX_NETWORK_RETRIES:
                    wait_time = NETWORK_RETRY_DELAY
                    logger.warning(f"请求异常 ({network_retry_count}/{MAX_NETWORK_RETRIES})，等待{wait_time}秒后重试: {e}")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"连续{MAX_NETWORK_RETRIES}次请求异常，尝试整体重试")
                    if os.path.exists(downloading_file_path):
                        try:
                            os.remove(downloading_file_path)
                            logger.info("已清理临时文件准备重试")
                        except Exception as cleanup_err:
                            logger.error(f"清理临时文件失败: {cleanup_err}")
                    
                    overall_retry_count += 1
                    if overall_retry_count < max_overall_retries:
                        wait_time = min(2 ** overall_retry_count, 30)
                        logger.info(f"整体重试 {overall_retry_count}/{max_overall_retries}，等待{wait_time}秒")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error("下载最终失败：超过最大重试次数")
                        return False
                        
            except Exception as e:
                # 未知异常：直接计入整体重试，不浪费在网络重试上
                logger.exception(f"下载过程中发生未知异常")
                if os.path.exists(downloading_file_path):
                    try:
                        os.remove(downloading_file_path)
                        logger.info("已清理临时文件")
                    except Exception as cleanup_err:
                        logger.error(f"清理临时文件失败: {cleanup_err}")
                
                overall_retry_count += 1
                if overall_retry_count < max_overall_retries:
                    wait_time = min(2 ** overall_retry_count, 30)
                    logger.warning(f"整体重试 {overall_retry_count}/{max_overall_retries}，等待{wait_time}秒: {e}")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error("下载最终失败：超过最大重试次数")
                    return False

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
    def __init__(self, max_workers=2, headless=True, download_dir="./downloads", task_logger=None, task_id=None,
                 config_manager=None, use_bypass=False):
        self.all_video_links = set()
        self.download_links = []
        self.downloader = VideoDownloader(download_dir=download_dir, headless=headless)
        self.max_workers = max_workers
        self.task_logger = task_logger
        self.task_id = task_id
        self.headless = headless
        self.config_manager = config_manager
        self.use_bypass = use_bypass

        self.link_queue = queue.Queue()
        self.results = []
        self.active_threads = 0
        self.lock = threading.Lock()

        self.downloading_files = {}
        self.downloading_files_lock = threading.Lock()

        self.processing_links = {}
        self.processing_links_lock = threading.Lock()

        self.failed_links = []

        self._active_browsers = set()
        self._browsers_lock = threading.Lock()

    def _track_browser(self, browser: BrowserManager):
        with self._browsers_lock:
            self._active_browsers.add(browser)

    def _untrack_browser(self, browser: BrowserManager):
        with self._browsers_lock:
            self._active_browsers.discard(browser)

    async def close_all_browsers(self):
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
                await browser.go_to(start_url, use_bypass=False)
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

    # ---------- 修改点2：在调用 download_video 时传入原始链接 ----------
    def _handle_video_download(self, video_url: str, filename: str, worker, original_url: str) -> bool:
        safe_filename = self.downloader._sanitize_filename(filename)
        self.promote_to_downloading(original_url, safe_filename, "0%")

        try:
            success = self.downloader.download_video(
                video_url, safe_filename, worker,
                self.task_logger, self.task_id,
                watch_url=original_url  # 关键：传入原始链接
            )
            if success:
                if self.task_logger and self.task_id:
                    self.task_logger.remove_failed_link(self.task_id, original_url)
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
            logger.exception(f"下载视频时发生异常")
            self.remove_progress(safe_filename)
            if self.task_logger and self.task_id:
                self.task_logger.add_failed_link(self.task_id, original_url, f"exception:{str(e)}")
            with self.lock:
                if original_url not in self.failed_links:
                    self.failed_links.append(original_url)
            return False

    # ---------- 修改点3：接收原始链接和下载链接 ----------
    async def _process_single_link_with_browser(self, original_link: str, download_link: str, browser: BrowserManager,
                                                worker) -> bool:
        logger.info(f"处理链接: {download_link} (原始: {original_link})")

        video_id_match = re.search(r'download\?v=([^&]+)', download_link)
        if video_id_match:
            display_text = f"正在处理: {video_id_match.group(1)}"
        else:
            display_text = f"正在处理: {original_link[:30]}..."
        self.add_processing(original_link, display_text)

        if await self.check_stop(worker):
            logger.warning("处理链接前检测到停止指令")
            self.remove_processing(original_link)
            return False
        if await self.check_pause(worker):
            logger.warning("处理链接前检测到暂停指令")
            self.remove_processing(original_link)
            return False

        try:
            video_url_real, filename = await self.downloader.extract_download_info(
                download_link, worker, browser=browser, use_bypass=self.use_bypass
            )
            if not video_url_real or not filename:
                self.remove_processing(original_link)
                return False

            return self._handle_video_download(video_url_real, filename, worker, original_link)

        except Exception as e:
            logger.exception(f"处理链接 {download_link} 时出错")
            self.remove_processing(original_link)
            if self.task_logger and self.task_id:
                self.task_logger.add_failed_link(self.task_id, original_link, f"browser_error:{str(e)}")
            with self.lock:
                self.failed_links.append(original_link)
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
                                # 从队列取出 (original_link, download_link) 元组
                                original_link, download_link = self.link_queue.get(timeout=1)
                            except queue.Empty:
                                if self.link_queue.unfinished_tasks == 0:
                                    break
                                continue
                            except ValueError:
                                # 兼容旧数据（如果队列中仍有字符串），尝试恢复原始链接
                                item = self.link_queue.get_nowait()
                                if isinstance(item, str):
                                    download_link = item
                                    if '/download?' in download_link:
                                        original_link = download_link.replace('/download?', '/watch?')
                                    else:
                                        original_link = download_link
                                    logger.warning(f"队列中发现旧格式链接，已恢复原始链接: {original_link}")
                                else:
                                    logger.error(f"未知的队列项类型: {type(item)}")
                                    self.link_queue.task_done()
                                    continue

                            try:
                                success = await self._process_single_link_with_browser(
                                    original_link, download_link, browser, worker
                                )
                                with self.lock:
                                    self.results.append(success)
                            except Exception as e:
                                logger.exception(f"线程处理链接失败: {download_link}")
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

    # ---------- 修改点4：在队列中放入原始链接和下载链接的元组 ----------
    async def process_links_batch(self, links_batch: List[str], worker=None) -> None:
        logger.info(f"开始批量处理 {len(links_batch)} 个链接，使用 {self.max_workers} 个工作线程")

        while not self.link_queue.empty():
            try:
                self.link_queue.get_nowait()
                self.link_queue.task_done()
            except queue.Empty:
                break

        for original_link in links_batch:
            if '/watch?' in original_link:
                download_link = original_link.replace('/watch?', '/download?')
            else:
                download_link = original_link
            # 放入元组 (original_link, download_link)
            self.link_queue.put((original_link, download_link))

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

    _last_progress_time = {}
    _progress_lock = threading.Lock()

    def __init__(self, url: str, download_dir: str, headless: bool, use_bypass: bool, task_logger=None, task_id=None,
                 config_manager=None, is_restored=False):
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.headless = headless
        self.use_bypass = use_bypass
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
        self._loop = None

        if task_logger and task_id:
            self.failed_links_to_retry = task_logger.get_task_failed_links(task_id)
            self.retry_failed_links = bool(self.failed_links_to_retry)

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
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
                use_bypass=self.use_bypass
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
            if self.scraper:
                try:
                    loop.run_until_complete(self.scraper.close_all_browsers())
                except Exception as e:
                    logger.error(f"关闭浏览器时出错: {e}")
            loop.close()
            self._loop = None

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

        remaining_links = []
        if self.is_restored and self.task_logger and self.task_id:
            all_tasks = self.task_logger.get_all_tasks()
            task_data = all_tasks.get(self.task_id, {})
            stored_video_links = task_data.get('video_links', [])
            stored_failed_links = task_data.get('failed_links', [])

            if stored_video_links or stored_failed_links:
                remaining_links = list(set(stored_video_links + stored_failed_links))
                self.log_signal.emit(f"恢复任务，使用已存储的 {len(remaining_links)} 个剩余链接")
            else:
                self.log_signal.emit("未找到已存储的剩余链接，尝试重新抓取")
                video_links = await self.scraper.get_video_links(self.url, self)
                remaining_links = video_links
        else:
            video_links = await self.scraper.get_video_links(self.url, self)
            remaining_links = video_links

        if self.should_stop() or self.should_pause():
            return False

        if not remaining_links:
            self.log_signal.emit("没有找到任何视频链接")
            return False

        self.log_signal.emit(f"开始处理 {len(remaining_links)} 个剩余视频链接...")
        await self.scraper.process_links_batch(remaining_links, self)

        failed_links = self.scraper.get_failed_links()
        if failed_links:
            self.log_signal.emit(f"发现 {len(failed_links)} 个失败链接，任务将暂停以便重试")
            if self.task_logger and self.task_id:
                for link in failed_links:
                    self.task_logger.add_failed_link(self.task_id, link, "initial_failed")
            self.failed_links_to_retry = failed_links
            return False
        else:
            self.log_signal.emit(f"处理完成！共下载 {len(self.scraper.download_links)} 个视频")
            return True

    def stop(self):
        self._is_running = False
        self._pause_event.set()
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