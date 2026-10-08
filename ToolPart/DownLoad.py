import asyncio
import os
import queue
import threading
import time
import logging
from typing import Dict, List, Optional, Tuple

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from pydoll.exceptions import NavigationError

from ToolPart.BrowserManager import BrowserManager
from ToolPart.utils import sanitize_filename
from ToolPart.SiteHandlers import (
    build_download_url,
    build_watch_url,
    is_video_link,
    parse_video_id,
    extract_button_info,
    get_attr,
    XPATH_PLAYLIST_CONTAINER,
    XPATH_PLAYLIST_LINKS,
    XPATH_DOWNLOAD_TABLE,
    XPATH_DOWNLOAD_BUTTON,
)
from ToolPart.optimizer import PerformanceOptimizer

logger = logging.getLogger(__name__)


class VideoDownloader:
    def __init__(self, download_dir="./downloads", max_retries=3, headless=True):
        self.download_dir = download_dir
        self.max_retries = max_retries
        self.headless = headless
        self.element_wait_timeout = 10  # 可被调用方（HanimeScraper）按配置覆盖
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
            # 判断是 watch 还是 download 页面
            if '/download?' in download_page_url:
                logger.info("✓ 检测到 download 页面，直接提取下载链接（跳过 watch 页面扫描）")
            elif '/watch?' in download_page_url:
                logger.info("⚠ 检测到 watch 页面，需要解析视频列表")

            # 性能优化（关键）：watch 页已经清过 Cloudflare，cf_clearance 在同标签页/
            # 同源下会持久化，后续 download 页通常不再弹 Turnstile。直接用非 bypass
            # 模式访问，可避免 expect_cloudflare_turnstile 一直阻塞到页面 load 事件
            # （实测每个 download 页因此多等 ~19s）。若没拿到表格（偶发重新挑战），
            # 再回退用 bypass 重试一次兜底。
            table_element = None
            for attempt_bp in ([use_bypass] if use_bypass else [False, True]):
                # 关键修复（第十轮）：并行预取时，刚绕过 Cloudflare 的页面往往仍处于
                # "请稍候…" 的 JS 重定向中间态，此刻立刻 go_to 到 download 页会被这次
                # 重定向中止，pydoll 抛 NavigationError(net::ERR_ABORTED)。对导航单独
                # 重试一次，避免一个瞬时的 ERR_ABORTED 整条丢掉一个视频链接。
                nav_ok = False
                for nav_retry in range(2):  # 至多重试一次导航
                    try:
                        await browser.go_to(download_page_url, use_bypass=attempt_bp)
                        nav_ok = True
                        break
                    except NavigationError as ne:
                        logger.warning(
                            f"导航被中止（ERR_ABORTED，第 {nav_retry + 1} 次），稍后重试: "
                            f"{download_page_url} - {ne}"
                        )
                        await asyncio.sleep(1.0)
                if not nav_ok:
                    logger.warning(f"导航多次失败，跳过本方式: {download_page_url}")
                    continue
                # 下载页同样会先经过 Cloudflare + "请稍候…"加载页，#content-div 表格可能
                # 延迟出现，故把等待时间放宽到至少 30s，避免还没渲染完就判定失败。
                wait = max(self.element_wait_timeout, 30)
                # 等待表格出现
                table_element = await browser.wait_for_element(
                    XPATH_DOWNLOAD_TABLE,
                    timeout=wait
                )
                if table_element:
                    break
                logger.warning(f"未找到下载链接列表表格（use_bypass={attempt_bp}），尝试下一方式")

            if not table_element:
                logger.warning("未找到下载链接列表表格")
                return None, None

            logger.info("找到下载链接列表表格")

            # 查找下载按钮（真正的 a 元素，带 data-url）
            first_download_btn = await browser.wait_for_element(
                XPATH_DOWNLOAD_BUTTON,
                timeout=wait
            )
            if not first_download_btn:
                logger.warning("未找到第一个下载按钮")
                return None, None

            download_url, filename = await extract_button_info(first_download_btn)

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

        safe_filename = sanitize_filename(filename)
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

        # 重试配置
        MAX_RETRIES = max(self.max_retries, 5)  # 至少重试5次，应对网络波动
        BASE_DELAY = 3  # 基础延迟秒数（增加到3秒）
        
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                success = self._execute_download(
                    video_url, downloading_file_path, final_file_path,
                    safe_filename, worker, task_logger, task_id, watch_url
                )
                if success:
                    return True
                    
            except requests.exceptions.SSLError as e:
                # SSL错误：可能是服务器主动断开或证书问题
                wait_time = min(PerformanceOptimizer.calculate_optimal_delay(BASE_DELAY, attempt), 60)
                logger.warning(f"SSL错误 ({attempt}/{MAX_RETRIES}): {e}")
                if worker and hasattr(worker, 'log_signal'):
                    worker.log_signal.emit(f"⚠ SSL连接错误，{wait_time}秒后重试 ({attempt}/{MAX_RETRIES}): {safe_filename}")
                
                # 清理临时文件，准备重试
                self._cleanup_temp_file(downloading_file_path)
                time.sleep(wait_time)
                continue
                
            except requests.exceptions.ConnectionError as e:
                # 连接错误：典型的网络波动
                wait_time = min(PerformanceOptimizer.calculate_optimal_delay(BASE_DELAY, attempt), 30)
                logger.warning(f"连接错误 ({attempt}/{MAX_RETRIES}): {e}")
                if worker and hasattr(worker, 'log_signal'):
                    worker.log_signal.emit(f"⚠ 网络连接失败，{wait_time}秒后重试 ({attempt}/{MAX_RETRIES}): {safe_filename}")
                
                self._cleanup_temp_file(downloading_file_path)
                time.sleep(wait_time)
                continue
                
            except requests.exceptions.Timeout as e:
                # 超时错误
                wait_time = min(PerformanceOptimizer.calculate_optimal_delay(BASE_DELAY, attempt), 30)
                logger.warning(f"请求超时 ({attempt}/{MAX_RETRIES}): {e}")
                if worker and hasattr(worker, 'log_signal'):
                    worker.log_signal.emit(f"⚠ 请求超时，{wait_time}秒后重试 ({attempt}/{MAX_RETRIES}): {safe_filename}")
                
                self._cleanup_temp_file(downloading_file_path)
                time.sleep(wait_time)
                continue
                
            except requests.exceptions.HTTPError as e:
                # HTTP错误：根据状态码决定是否重试
                http_status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
                logger.warning(f"HTTP错误 ({attempt}/{MAX_RETRIES}): {e}, 状态码: {http_status}")
                
                if http_status and http_status >= 500:
                    # 服务器错误，可以重试
                    wait_time = min(PerformanceOptimizer.calculate_optimal_delay(BASE_DELAY, attempt), 30)
                    if worker and hasattr(worker, 'log_signal'):
                        worker.log_signal.emit(f"⚠ 服务器错误({http_status})，{wait_time}秒后重试: {safe_filename}")
                    self._cleanup_temp_file(downloading_file_path)
                    time.sleep(wait_time)
                    continue
                elif http_status == 403:
                    # 访问被拒绝，不重试
                    logger.error("访问被拒绝(403)，可能是IP被封或需要验证")
                    if worker and hasattr(worker, 'log_signal'):
                        worker.log_signal.emit(f"✗ 访问被拒绝(403)，请检查是否需要启用Bypass模式: {safe_filename}")
                    self._cleanup_temp_file(downloading_file_path)
                    return False
                else:
                    # 其他HTTP错误，不重试
                    logger.error(f"HTTP错误({http_status})，不重试")
                    if worker and hasattr(worker, 'log_signal'):
                        worker.log_signal.emit(f"✗ HTTP错误({http_status}): {safe_filename}")
                    self._cleanup_temp_file(downloading_file_path)
                    return False
                
            except Exception as e:
                # 其他异常
                logger.warning(f"下载尝试 {attempt}/{MAX_RETRIES} 失败: {type(e).__name__}: {e}")
                
                # 最后一次尝试失败，直接返回
                if attempt == MAX_RETRIES:
                    logger.error("达到最大重试次数")
                    if worker and hasattr(worker, 'log_signal'):
                        worker.log_signal.emit(f"✗ 下载失败（超过{MAX_RETRIES}次重试）: {safe_filename}")
                    self._cleanup_temp_file(downloading_file_path)
                    return False
                
                # 指数退避等待
                wait_time = min(PerformanceOptimizer.calculate_optimal_delay(BASE_DELAY, attempt), 30)
                logger.info(f"{wait_time}秒后重试...")
                
                if worker and hasattr(worker, 'log_signal'):
                    worker.log_signal.emit(f"↻ 重试 {attempt}/{MAX_RETRIES} ({wait_time}秒后): {safe_filename}")
                
                # 清理临时文件，准备重试
                self._cleanup_temp_file(downloading_file_path)
                time.sleep(wait_time)
        
        return False

    def _execute_download(self, video_url: str, downloading_path: str, final_path: str,
                         safe_filename: str, worker, task_logger, task_id, watch_url) -> bool:
        """
        执行单次下载操作（支持断点续传）
        :return: 是否成功
        """
        response = None
        CHECK_INTERVAL = 0.5  # 每0.5秒检查一次暂停/停止
        
        try:
            # 检查是否有未完成的下载，支持断点续传
            resume_position = 0
            if os.path.exists(downloading_path):
                file_size = os.path.getsize(downloading_path)
                if file_size > 0:
                    resume_position = file_size
                    logger.info(f"发现未完成文件，从 {self._format_size(resume_position)} 处续传")
                    if worker and hasattr(worker, 'log_signal'):
                        worker.log_signal.emit(f"↻ 断点续传: {safe_filename} (已下载 {self._format_size(resume_position)})")
            
            # 设置请求头，支持断点续传
            headers = {}
            if resume_position > 0:
                headers['Range'] = f'bytes={resume_position}-'
            
            # 分离连接超时和读取超时（增加读取超时以应对大文件）
            timeout_config = (
                15,  # 连接超时15秒
                120  # 读取超时120秒（每个chunk），应对网络波动
            )
            
            # 尝试请求，如果失败则重建Session后重试
            try:
                response = self.session.get(video_url, stream=True, timeout=timeout_config, headers=headers)
            except requests.exceptions.RequestException as e:
                logger.warning(f"首次请求失败，尝试重建Session后重试: {e}")
                self._session = None
                response = self.session.get(video_url, stream=True, timeout=timeout_config, headers=headers)
            
            # 206表示部分内容（续传），200表示完整下载
            if response.status_code not in [200, 206]:
                # 如果是416错误且是断点续传，说明服务器不支持，重新开始下载
                if response.status_code == 416 and resume_position > 0:
                    logger.warning("服务器不支持断点续传(Range 416)，重新开始下载")
                    response.close()
                    if os.path.exists(downloading_path):
                        os.remove(downloading_path)
                    headers.pop('Range', None)
                    response = self.session.get(video_url, stream=True, timeout=timeout_config, headers=headers)
                    if response.status_code != 200:
                        response.raise_for_status()
                    resume_position = 0
                else:
                    response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            # 如果是续传，total_size是剩余大小，需要加上已下载的部分
            if resume_position > 0 and response.status_code == 206:
                # 尝试从Content-Range头获取总大小
                content_range = response.headers.get('content-range', '')
                if content_range and '/' in content_range:
                    try:
                        total_size = int(content_range.split('/')[-1])
                    except:
                        total_size += resume_position
            
            downloaded_size = resume_position
            last_check_time = time.time()

            os.makedirs(self.download_dir, exist_ok=True)

            # 根据是否有续传决定打开模式
            mode = 'ab' if resume_position > 0 else 'wb'
            
            with open(downloading_path, mode) as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        try:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                        except IOError as write_error:
                            logger.error(f"写入数据块时出错: {write_error}")
                            raise

                        # 基于时间检查暂停/停止
                        now = time.time()
                        if now - last_check_time >= CHECK_INTERVAL:
                            last_check_time = now
                            
                            # 检查停止信号
                            if worker and hasattr(worker, 'should_stop') and worker.should_stop():
                                logger.warning("下载已被停止")
                                response.close()
                                if os.path.exists(downloading_path):
                                    try:
                                        os.remove(downloading_path)
                                        logger.info("已删除临时文件")
                                    except Exception as e:
                                        logger.error(f"删除临时文件失败: {e}")
                                return False

                            # 检查暂停信号
                            if worker and hasattr(worker, 'should_pause'):
                                if worker.should_pause(timeout=0.5):
                                    logger.info("下载已暂停，等待恢复...")
                                    # 持续检查直到恢复或停止
                                    while worker.should_pause(timeout=0.5):
                                        if worker.should_stop():
                                            logger.warning("暂停期间收到停止指令")
                                            response.close()
                                            if os.path.exists(downloading_path):
                                                try:
                                                    os.remove(downloading_path)
                                                except:
                                                    pass
                                            return False
                                    logger.info("下载已恢复")

                        if total_size > 0:
                            progress = (downloaded_size / total_size) * 100
                            progress_str = f"{progress:.1f}% ({self._format_size(downloaded_size)}/{self._format_size(total_size)})"
                            if self.progress_callback:
                                self.progress_callback(safe_filename, progress_str)
                        else:
                            if self.progress_callback:
                                self.progress_callback(safe_filename, f"{self._format_size(downloaded_size)}")

            # 完整性校验：已知总长度时，比对实际写入字节数，避免截断文件被误判为成功
            if total_size > 0 and downloaded_size != total_size:
                logger.warning(
                    f"下载不完整：已写入 {downloaded_size} 字节，期望 {total_size} 字节，将触发重试"
                )
                raise IOError("下载文件大小与 Content-Length 不符，可能中断")

            logger.info("下载完成，正在重命名文件...")

            try:
                os.replace(downloading_path, final_path)
                logger.info(f"文件重命名完成: {final_path}")
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

        finally:
            if response:
                response.close()

    def _cleanup_temp_file(self, file_path: str):
        """清理临时文件"""
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"已清理临时文件: {file_path}")
            except Exception as e:
                logger.error(f"清理临时文件失败: {e}")

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
                 config_manager=None, use_bypass=False, max_prefetch_browsers=4):
        self.all_video_links = set()
        self.download_links = []
        self.downloader = VideoDownloader(download_dir=download_dir, headless=headless)
        self.max_workers = max_workers
        self.task_logger = task_logger
        self.task_id = task_id
        self.headless = headless
        self.config_manager = config_manager
        self.use_bypass = use_bypass
        # 预取阶段并行浏览器数（默认 4，硬上限 4；可由 config.json 的 max_prefetch_browsers 覆盖）
        self.max_prefetch_browsers = (
            int(config_manager.get("max_prefetch_browsers", max_prefetch_browsers))
            if config_manager else max_prefetch_browsers
        )

        self.link_queue = queue.Queue()
        self.results = []
        self.active_threads = 0
        self.lock = threading.Lock()

        self.downloading_files = {}
        self.downloading_files_lock = threading.Lock()

        self.processing_links = {}
        self.processing_links_lock = threading.Lock()

        self.failed_links = []

        self.direct_url_map = {}  # original_link -> (video_url, filename)，由预取阶段填充

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

    async def _fetch_playlist(self, browser: BrowserManager, start_url: str, worker=None) -> List[str]:
        """使用已存在的浏览器实例抓取播放列表链接。"""
        if await self.check_stop(worker):
            logger.warning("获取视频链接前检测到停止指令")
            return []
        if await self.check_pause(worker):
            logger.warning("获取视频链接前检测到暂停指令")
            return []

        await browser.go_to(start_url, use_bypass=self.use_bypass)

        # 关键修复：hanime1.me 通过 Cloudflare 后会先显示"请稍候…"加载页，
        # #playlist-scroll 可能先以空壳 / 占位 a 元素出现，真正的 a.overlay
        # （watch?v=...）链接由 JS 在加载完成后才注入。因此不能只等"有 a 元素"，
        # 必须轮询直到出现真正匹配的视频链接（最多 ~30s）。
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30
        container_seen = False
        while loop.time() < deadline:
            if await self.check_stop(worker) or await self.check_pause(worker):
                break
            try:
                link_elements = await browser.find_element(
                    xpath=XPATH_PLAYLIST_LINKS, find_all=True, timeout=0, raise_exc=False
                )
            except Exception as exc:
                logger.debug(f"轮询播放列表链接时出错（可能页面正在重定向）: {exc}")
                link_elements = None

            if link_elements:
                container_seen = True
                for element in link_elements:
                    if await self.check_stop(worker) or await self.check_pause(worker):
                        break
                    try:
                        # 关键修复：pydoll 3.0 的 get_attribute 是同步方法，
                        # 不能 await，否则抛 TypeError 被 except 吞掉 → 0 链接。
                        # 改用 get_attr（同步优先 + JS 回退）。
                        href = await get_attr(element, 'href')
                    except Exception as exc:
                        logger.debug(f"获取 href 失败: {exc}")
                        continue
                    if is_video_link(href):
                        self.all_video_links.add(href)
                if self.all_video_links:
                    break
            await asyncio.sleep(0.5)

        if not self.all_video_links:
            # 诊断：容器/链接都没等到 → 多半"请稍候…"没解析完或选择器不匹配
            try:
                final_url = await browser.tab.current_url()
                final_title = await browser.tab.title()
            except Exception as e:
                final_url, final_title = '(读取失败)', f'({e})'
            logger.warning(
                f"未找到有效视频链接 (URL: {final_url} | 标题: {final_title} | 容器初见: {container_seen})"
            )
            try:
                html = await browser.tab.page_source()
                markers = {
                    'playlist-scroll': 'id="playlist-scroll"' in html,
                    'content-div': 'id="content-div"' in html,
                    '含 playlist 文本': 'playlist' in html.lower(),
                    '含 watch?v= 文本': 'watch?v=' in html,
                }
                logger.warning(f"页面源码标记检测: {markers} | 源码长度: {len(html)}")
                debug_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), '..', 'Logger', 'debug_page.html'
                )
                with open(debug_path, 'w', encoding='utf-8') as f:
                    f.write(html)
                logger.warning(f"已保存页面源码到: {os.path.abspath(debug_path)}（本地调试用）")
            except Exception as e:
                logger.warning(f"保存页面源码失败: {e}")

        video_links_list = list(self.all_video_links)
        logger.info(f"获取到 {len(video_links_list)} 个视频链接")
        if self.task_logger and self.task_id:
            self.task_logger.add_video_links(self.task_id, video_links_list)
        return video_links_list

    async def _wait_page_settled(self, browser: BrowserManager, timeout: int = 12) -> None:
        """等待页面离开"请稍候…"加载态，避免随后的导航被正在进行的重定向中止（ERR_ABORTED）。

        仅用于预取阶段：bypass Cloudflare 后，hanime1.me 会先显示"请稍候…"加载页，
        再由前端 JS 重定向到真实内容。若此时立刻 go_to 到 download 页，该重定向会
        abort 新导航，pydoll 抛 net::ERR_ABORTED，造成该浏览器首个下载链接被整条跳过。
        轮询直到标题不再是"请稍候…"（即真实内容已就位）再继续。
        """
        if not browser or not getattr(browser, 'tab', None):
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                title = await browser.tab.title()
            except Exception:
                title = None
            if title and title.strip() != "请稍候…":
                return
            await asyncio.sleep(0.5)

    async def _prefetch_direct_urls(self, browser: BrowserManager, links: List[str], worker=None) -> Dict[str, Tuple[str, str]]:
        """对给定链接（watch/download）逐一提取直接下载地址，返回 original_link -> (video_url, filename)。"""
        direct_map: Dict[str, Tuple[str, str]] = {}
        for link in links:
            if await self.check_stop(worker) or await self.check_pause(worker):
                break
            download_link = build_download_url(link)
            try:
                video_url, filename = await self.downloader.extract_download_info(
                    download_link, worker, browser=browser, use_bypass=False
                )
            except Exception as exc:
                logger.warning(f"预取直接链接异常: {link} - {exc}")
                continue
            if video_url and filename:
                direct_map[build_watch_url(link)] = (video_url, filename)
            else:
                logger.warning(f"预取直接链接失败（无有效地址）: {link}")
        return direct_map

    @staticmethod
    def _split_round_robin(items: List[str], n: int) -> List[List[str]]:
        """把 items 按 n 路 round-robin 切分，保证各浏览器负载均衡。"""
        n = max(1, int(n))
        return [items[i::n] for i in range(n)]

    def _prefetch_chunk_in_thread(self, chunk, prime_url, worker, result_dict, lock):
        """线程内：独立事件循环 + 独立浏览器，预取一个分片，结果写入 result_dict（加锁）。

        每个预取浏览器都在自己的线程里跑独立的 asyncio 事件循环，
        与下载阶段 _process_worker 的并发模型一致，彻底隔离 pydoll 状态、
        避免多个浏览器共享连接/回调引发的冲突。
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        browser = None
        try:
            browser = BrowserManager(headless=self.headless, download_dir=self.downloader.download_dir,
                                      config_manager=self.config_manager, use_bypass=self.use_bypass)
            loop.run_until_complete(browser.start())
            self._track_browser(browser)
            # 预清除 Cloudflare：先 bypass 访问一次（watch/download 页）拿到 cf_clearance，
            # 之后本线程内所有 download 页用 use_bypass=False 即可秒过（见第八轮优化）。
            if prime_url:
                try:
                    loop.run_until_complete(browser.go_to(prime_url, use_bypass=True))
                    # 关键修复（第十轮）：bypass 后的 watch/download 页会先停留在"请稍候…"，
                    # 随后由 JS 重定向到真实内容。若立刻导航到 download 页，这次重定向会
                    # 中止（net::ERR_ABORTED）新导航，导致该浏览器首个链接提取失败。
                    # 等待首屏真正稳定（标题不再是"请稍候…"）后再开始逐条预取。
                    loop.run_until_complete(self._wait_page_settled(browser, timeout=12))
                except Exception as e:
                    logger.warning(f"预清除 Cloudflare 失败（后续按需回退 bypass）: {e}")
            partial = loop.run_until_complete(self._prefetch_direct_urls(browser, chunk, worker))
            if isinstance(partial, dict):
                with lock:
                    result_dict.update(partial)
        except Exception as e:
            logger.exception(f"预取分片线程异常: {e}")
        finally:
            if browser is not None:
                self._untrack_browser(browser)
                try:
                    loop.run_until_complete(browser.close())
                except Exception:
                    pass
            loop.close()

    async def _run_parallel_prefetch(self, chunks, prime_url, worker) -> Dict[str, Tuple[str, str]]:
        """并发启动各分片线程，join 后合并 direct_map。"""
        result_dict: Dict[str, Tuple[str, str]] = {}
        lock = threading.Lock()
        threads = []
        for chunk in chunks:
            if not chunk:
                continue
            t = threading.Thread(
                target=self._prefetch_chunk_in_thread,
                args=(chunk, prime_url, worker, result_dict, lock),
                daemon=True,
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        return result_dict

    async def prepare_links(self, start_url: str, worker=None) -> Tuple[List[str], Dict[str, Tuple[str, str]]]:
        """并行预取：最多开 max_prefetch_browsers（≤4）个浏览器——
        1 个抓播放列表，其余并发抓各视频的直接下载地址。

        返回 (video_links, direct_map)。direct_map 供 worker 线程免浏览器下载使用。
        """
        n = max(1, min(int(self.max_prefetch_browsers), 4))
        logger.info(f"预取阶段：先抓播放列表，再用 {n} 个浏览器并行提取直接下载链接")

        # 1) 单个浏览器抓播放列表（顺带清 Cloudflare；随后关闭，预取用全新浏览器以隔离状态）
        b0 = BrowserManager(headless=self.headless, download_dir=self.downloader.download_dir,
                            config_manager=self.config_manager, use_bypass=self.use_bypass)
        await b0.start()
        self._track_browser(b0)
        video_links: List[str] = []
        try:
            video_links = await self._fetch_playlist(b0, start_url, worker)
        finally:
            self._untrack_browser(b0)
            try:
                await b0.close()
            except Exception:
                pass

        if not video_links:
            return video_links, {}

        # 2) 按浏览器数 round-robin 切分，各线程并发预取；prime_url 用 watch 页给每个浏览器设 cf_clearance
        chunks = self._split_round_robin(video_links, n)
        direct_map = await self._run_parallel_prefetch(chunks, start_url, worker)
        logger.info(f"预取完成：列表 {len(video_links)} 条，成功提取直接链接 {len(direct_map)} 条")
        return video_links, direct_map

    async def prepare_direct_map(self, links: List[str], worker=None) -> Dict[str, Tuple[str, str]]:
        """针对已有链接（如重试的失败链接）并行预取直接下载地址（同样最多开 max_prefetch_browsers 个浏览器）。"""
        if not links:
            return {}
        n = max(1, min(int(self.max_prefetch_browsers), 4))
        logger.info(f"预取阶段：针对 {len(links)} 个链接，使用 {n} 个浏览器并行提取直接下载链接")
        chunks = self._split_round_robin(links, n)
        # prime_url 用首个链接的 download 页给每个浏览器设 cf_clearance
        prime_url = build_download_url(links[0])
        return await self._run_parallel_prefetch(chunks, prime_url, worker)

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
        safe_filename = sanitize_filename(filename)
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
        # 判断是否是重试模式
        is_retry = '/download?' in download_link and '/watch?' in original_link
        if is_retry:
            logger.info(f"🔄 重试失败链接: {original_link}")
            logger.info(f"   → 使用 download 页面直接提取: {download_link[:60]}...")
        else:
            logger.info(f"处理链接: {download_link} (原始: {original_link})")

        video_id_match = parse_video_id(download_link)
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
                download_link, worker, browser=browser, use_bypass=False
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
                browser = None
                created_browser = False
                try:
                    while True:
                        # 关键修改：在每次从队列取任务前检查暂停/停止状态
                        if hasattr(worker, 'should_stop') and worker.should_stop():
                            logger.info("工作线程检测到停止指令，退出")
                            break

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
                                    original_link = build_watch_url(download_link)
                                else:
                                    original_link = download_link
                                logger.warning(f"队列中发现旧格式链接，已恢复原始链接: {original_link}")
                            else:
                                logger.error(f"未知的队列项类型: {type(item)}")
                                self.link_queue.task_done()
                                continue

                        # 处理链接前再次检查暂停/停止
                        if hasattr(worker, 'should_stop') and worker.should_stop():
                            logger.info("处理链接前检测到停止指令")
                            self.link_queue.task_done()
                            break

                        if hasattr(worker, 'should_pause'):
                            while worker.should_pause(timeout=0.5):
                                if worker.should_stop():
                                    logger.info("暂停期间检测到停止指令")
                                    self.link_queue.task_done()
                                    break
                            else:
                                # 如果没有break（即没有停止），继续处理
                                if hasattr(worker, 'should_stop') and worker.should_stop():
                                    self.link_queue.task_done()
                                    break

                        try:
                            # 预取阶段已拿到直接链接 → 免浏览器直接用 requests 下载；
                            # 否则（极少数提取失败的链接）按需创建浏览器回退。
                            if original_link in self.direct_url_map:
                                video_url_real, filename = self.direct_url_map[original_link]
                                success = self._handle_video_download(video_url_real, filename, worker, original_link)
                            else:
                                if browser is None:
                                    browser = BrowserManager(headless=self.headless,
                                                              download_dir=self.downloader.download_dir,
                                                              config_manager=self.config_manager,
                                                              use_bypass=self.use_bypass)
                                    await browser.start()
                                    self._track_browser(browser)
                                    created_browser = True
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
                    if created_browser and browser is not None:
                        self._untrack_browser(browser)
                        try:
                            await browser.close()
                        except Exception as exc:
                            logger.error(f"关闭浏览器失败: {exc}")

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

        for link in links_batch:
            # 智能判断链接类型：如果已经是 download 链接，直接使用；否则转换
            if '/download?' in link:
                # 已经是 download 链接，将其还原为 watch 作为 original_link
                original_link = build_watch_url(link)
                download_link = link
                logger.info(f"检测到 download 链接，将直接访问下载页面: {download_link[:60]}...")
            elif '/watch?' in link:
                # watch 链接，需要转换
                download_link = build_download_url(link)
                original_link = link
            else:
                # 其他情况，假设是原始链接
                download_link = link
                original_link = link
            
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
        self.retry_count = 0  # 任务重试计数器
        self.max_task_retries = 3  # 最大任务重试次数

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

            max_workers = self.config_manager.get("max_workers", 2) if self.config_manager else 2
            self.scraper = HanimeScraper(
                max_workers=max_workers,
                headless=self.headless,
                download_dir=self.download_dir,
                task_logger=self.task_logger,
                task_id=self.task_id,
                config_manager=self.config_manager,
                use_bypass=self.use_bypass
            )
            self.scraper.downloader.element_wait_timeout = (
                self.config_manager.get("element_wait_timeout", 10) if self.config_manager else 10
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
        """节流进度更新，避免UI卡顿"""
        current_time = time.time()
        with self._progress_lock:
            last_time = self._last_progress_time.get(filename, 0)
            # 降低更新频率到0.5秒（原来是0.2秒）
            MIN_UPDATE_INTERVAL = 0.5
            
            if current_time - last_time < MIN_UPDATE_INTERVAL and progress != "100.0%" and progress != "已完成":
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

        # 检查是否超过最大重试次数
        if self.retry_failed_links and self.retry_count >= self.max_task_retries:
            self.log_signal.emit(f"✗ 任务已达到最大重试次数({self.max_task_retries})，不再重试")
            logger.warning(f"任务 {self.task_id} 达到最大重试次数")
            return False

        remaining_links = []

        # 如果是重试模式，只处理失败链接（已转换为 download 格式）
        if self.retry_failed_links and self.failed_links_to_retry:
            remaining_links = self.failed_links_to_retry
            self.log_signal.emit(f"🔄 重试模式：将处理 {len(remaining_links)} 个失败链接")
            self.log_signal.emit(f"💡 使用 download 页面直接提取，跳过 watch 页面扫描")
            self.scraper.direct_url_map = await self.scraper.prepare_direct_map(remaining_links, self)
        elif self.is_restored and self.task_logger and self.task_id:
            all_tasks = self.task_logger.get_all_tasks()
            task_data = all_tasks.get(self.task_id, {})
            stored_video_links = task_data.get('video_links', [])
            stored_failed_links = task_data.get('failed_links', [])

            if stored_video_links or stored_failed_links:
                remaining_links = list(set(stored_video_links + stored_failed_links))
                self.log_signal.emit(f"恢复任务，使用已存储的 {len(remaining_links)} 个剩余链接")
                self.scraper.direct_url_map = await self.scraper.prepare_direct_map(remaining_links, self)
            else:
                self.log_signal.emit("未找到已存储的剩余链接，尝试重新抓取")
                video_links, direct_map = await self.scraper.prepare_links(self.url, self)
                remaining_links = video_links
                self.scraper.direct_url_map = direct_map
        else:
            video_links, direct_map = await self.scraper.prepare_links(self.url, self)
            remaining_links = video_links
            self.scraper.direct_url_map = direct_map

        if self.should_stop() or self.should_pause():
            return False

        if not remaining_links:
            self.log_signal.emit("没有找到任何视频链接")
            return False

        # 如果是重试模式，增加重试计数
        if self.retry_failed_links:
            self.retry_count += 1
            self.log_signal.emit(f"🔄 第 {self.retry_count}/{self.max_task_retries} 次重试，处理 {len(remaining_links)} 个链接...")
        else:
            self.log_signal.emit(f"开始处理 {len(remaining_links)} 个剩余视频链接...")
        
        await self.scraper.process_links_batch(remaining_links, self)

        # 关键修改：在处理完成后再次检查暂停/停止状态
        if self.should_stop():
            self.log_signal.emit("任务已被停止")
            return False
        
        if self.should_pause():
            self.log_signal.emit("任务已暂停")
            return False

        failed_links = self.scraper.get_failed_links()
        if failed_links:
            self.log_signal.emit(f"发现 {len(failed_links)} 个失败链接，任务将暂停以便重试")
            if self.task_logger and self.task_id:
                for link in failed_links:
                    self.task_logger.add_failed_link(self.task_id, link, "initial_failed")
            self.failed_links_to_retry = failed_links
            return False
        else:
            # 从 TaskLogger 获取实际下载的视频数量
            downloaded_count = 0
            if self.task_logger and self.task_id:
                all_tasks = self.task_logger.get_all_tasks()
                task_data = all_tasks.get(self.task_id, {})
                downloaded_videos = task_data.get('downloaded_videos', [])
                downloaded_count = len(downloaded_videos)
            
            self.log_signal.emit(f"处理完成！共下载 {downloaded_count} 个视频")
            return True

    def stop(self):
        """立即停止所有操作，清理资源"""
        self._is_running = False
        self._is_paused = False
        self._pause_event.set()
        
        # 关闭所有活动连接
        if hasattr(self, '_session') and self._session:
            try:
                self._session.close()
                logger.info("已关闭HTTP会话")
            except Exception as e:
                logger.error(f"关闭HTTP会话失败: {e}")
        
        # 强制关闭浏览器（增强错误处理）
        if self.scraper and self._loop:
            try:
                # 检查事件循环状态
                if not self._loop.is_closed():
                    if self._loop.is_running():
                        # 在运行中的事件循环中异步关闭
                        future = asyncio.run_coroutine_threadsafe(
                            self.scraper.close_all_browsers(), 
                            self._loop
                        )
                        try:
                            # 等待最多3秒
                            future.result(timeout=3)
                            logger.info("已关闭所有浏览器实例")
                        except asyncio.TimeoutError:
                            logger.warning("关闭浏览器超时，强制继续")
                        except Exception as e:
                            logger.error(f"异步关闭浏览器失败: {e}")
                    else:
                        # 事件循环未运行，直接运行
                        try:
                            self._loop.run_until_complete(self.scraper.close_all_browsers())
                            logger.info("已关闭所有浏览器实例")
                        except RuntimeError as e:
                            if "Event loop is closed" in str(e):
                                logger.warning("事件循环已关闭，跳过浏览器清理")
                            else:
                                raise
                else:
                    logger.warning("事件循环已关闭，跳过浏览器清理")
            except Exception as e:
                logger.error(f"停止时关闭浏览器失败: {e}")

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

    def should_pause(self, timeout=0.5) -> bool:
        """
        检查是否暂停，如果是则等待直到恢复或停止
        :param timeout: 每次等待的超时时间（秒）
        :return: 如果仍在暂停状态返回True，否则返回False
        """
        if not self._is_paused:
            return False
        
        # 使用Event等待，避免忙等待消耗CPU
        self._pause_event.wait(timeout)
        
        # 如果仍然暂停，返回True表示需要继续等待
        return self._is_paused

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