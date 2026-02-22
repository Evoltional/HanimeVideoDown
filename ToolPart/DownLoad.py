import asyncio
import os
import queue
import threading
import time
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
        # 确保下载目录存在
        os.makedirs(self.download_dir, exist_ok=True)
        # 进度报告回调函数
        self.progress_callback = None

    def set_progress_callback(self, callback):
        """设置进度回调函数"""
        self.progress_callback = callback

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """
        清理文件名，移除非法字符
        """
        # 替换Windows/Linux不支持的字符
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')

        # 解码URL编码的字符（如果有）
        filename = unquote(filename)

        return filename

    def _format_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes < 1024 * 1024:  # 小于1MB
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    def standard_print(self, level: str, message: str) -> None:
        """标准控制台输出"""
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {level.upper()} - {message}")

    async def check_pause(self, worker) -> bool:
        """检查暂停状态，如果暂停则等待"""
        if worker and hasattr(worker, 'should_pause'):
            return worker.should_pause()
        return False

    async def extract_download_info(self, download_page_url: str, worker=None) -> Tuple[Optional[str], Optional[str]]:
        """
        从下载页面提取视频下载链接和文件名
        """
        # 在进入浏览器操作前检查暂停状态
        if await self.check_pause(worker):
            print("浏览器操作被暂停")
            return None, None

        async with BrowserManager(headless=self.headless, download_dir=self.download_dir) as browser:
            self.standard_print("INFO", f"访问下载页面: {download_page_url}")

            # 使用验证码绕过访问页面
            await browser.go_to_with_captcha_bypass(download_page_url)

            # 等待页面加载
            await asyncio.sleep(3)

            # 页面加载后再次检查暂停状态
            if await self.check_pause(worker):
                print("页面加载后检测到暂停指令")
                return None, None

            try:
                # 在查找元素前检查暂停状态
                if await self.check_pause(worker):
                    print("元素查找前检测到暂停指令")
                    return None, None

                # 查找下载链接列表表格
                table_element = None
                for attempt in range(3):
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

                # 在查找下载按钮前检查暂停状态
                if await self.check_pause(worker):
                    print("下载按钮查找前检测到暂停指令")
                    return None, None

                # 查找第一个下载按钮
                first_download_btn = None
                for attempt in range(3):
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

                # 获取下载链接
                download_url = first_download_btn.get_attribute('data-url')

                # 获取文件名
                filename = first_download_btn.get_attribute('download')
                if not filename:
                    # 如果没有download属性，则获取按钮的文本内容
                    filename = await first_download_btn.text

                # 清理文件名，确保扩展名
                if filename:
                    # 移除首尾空格
                    filename = filename.strip()
                    # 确保文件扩展名为.mp4
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
        """
        下载视频文件，使用文件名前缀标识下载状态
        """
        if not video_url or not filename:
            self.standard_print("WARNING", "下载链接或文件名为空，跳过下载")
            return False

        # 清理文件名，移除非法字符
        safe_filename = self._sanitize_filename(filename)

        # 添加下载中前缀
        downloading_filename = f"下载中_{safe_filename}"
        downloading_file_path = os.path.join(self.download_dir, downloading_filename)
        final_file_path = os.path.join(self.download_dir, safe_filename)

        # 检查最终文件是否已存在
        if os.path.exists(final_file_path):
            self.standard_print("INFO", f"文件已存在，跳过下载: {final_file_path}")

            # 记录到TaskLogger
            if task_logger and task_id:
                task_logger.add_downloaded_video(task_id, safe_filename)
                # 手动触发一次进度更新，使界面重新读取 TaskLogger 中的计数
                if self.progress_callback:
                    self.progress_callback(safe_filename, "已完成")

            return True

        # 检查是否有之前的下载中文件
        if os.path.exists(downloading_file_path):
            self.standard_print("WARNING", f"发现之前的下载中文件: {downloading_file_path}")
            # 可以选择继续下载或重新开始，这里选择重新开始
            try:
                os.remove(downloading_file_path)
                self.standard_print("INFO", "已删除之前的下载中文件")
            except Exception as e:
                self.standard_print("ERROR", f"删除之前下载文件失败: {e}")
                return False

        self.standard_print("INFO", f"开始下载: {safe_filename} (临时文件: {downloading_filename})")

        retry_count = 0
        while retry_count < self.max_retries:
            try:
                response = requests.get(video_url, stream=True, timeout=30)
                response.raise_for_status()

                total_size = int(response.headers.get('content-length', 0))
                downloaded_size = 0
                chunk_counter = 0

                # 确保下载目录存在
                os.makedirs(self.download_dir, exist_ok=True)

                with open(downloading_file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            chunk_counter += 1

                            # 每处理5个数据块检查一次暂停状态，提高响应速度
                            if chunk_counter % 5 == 0:
                                if worker and hasattr(worker, 'should_pause') and worker.should_pause():
                                    self.standard_print("WARNING", "\n下载已被暂停")
                                    return False

                                # 检查是否停止
                                if worker and hasattr(worker, 'is_running') and not worker.is_running():
                                    self.standard_print("WARNING", "\n下载已被停止")
                                    return False

                            f.write(chunk)
                            downloaded_size += len(chunk)

                            # 显示下载进度
                            if total_size > 0:
                                progress = (downloaded_size / total_size) * 100
                                progress_str = f"{progress:.1f}% ({self._format_size(downloaded_size)}/{self._format_size(total_size)})"

                                # 报告进度
                                if self.progress_callback:
                                    self.progress_callback(safe_filename, progress_str)

                                print(f"\r{safe_filename}: {progress_str}", end='')
                            else:
                                # 如果没有总大小信息，显示已下载大小
                                if self.progress_callback:
                                    self.progress_callback(safe_filename, f"{self._format_size(downloaded_size)}")

                                print(f"\r{safe_filename}: {self._format_size(downloaded_size)}", end='')

                self.standard_print("INFO", "\n下载完成，正在重命名文件...")

                # 下载完成后重命名为最终文件名
                try:
                    os.rename(downloading_file_path, final_file_path)
                    self.standard_print("SUCCESS", f"文件重命名完成: {final_file_path}")

                    # 下载完成后报告100%进度
                    if self.progress_callback and total_size > 0:
                        self.progress_callback(safe_filename,
                                               f"100.0% ({self._format_size(total_size)}/{self._format_size(total_size)})")
                except Exception as e:
                    self.standard_print("ERROR", f"文件重命名失败: {e}")
                    return False

                # 记录到TaskLogger
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
                    # 等待一段时间后重试
                    time.sleep(2)

        return False

    async def download_from_page(self, download_page_url: str, worker=None, task_logger=None, task_id=None) -> bool:
        """
        从下载页面获取信息并下载视频
        """
        # 提取下载信息
        video_url, filename = await self.extract_download_info(download_page_url, worker)

        if video_url and filename:
            # 执行下载
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
        self.headless = headless  # 保存headless参数
        self.config_manager = config_manager  # 保存配置管理器

        # 多线程处理队列
        self.link_queue = queue.Queue()
        self.results = []
        self.active_threads = 0
        self.lock = threading.Lock()

        # 正在下载的文件进度字典
        self.downloading_files = {}  # 格式: {filename: progress_str}

        # ========== 新增：记录失败的链接 ==========
        self.failed_links = []  # 存储失败的链接

    # ========== 新增：获取失败链接的方法 ==========
    def get_failed_links(self):
        """返回失败的链接列表（线程安全）"""
        with self.lock:
            return self.failed_links.copy()

    # ========== 新增：重置跟踪状态的方法 ==========
    def reset_link_tracking(self):
        """重置链接跟踪状态，用于重试（线程安全）"""
        with self.lock:
            self.failed_links = []
            self.download_links = []
            self.all_video_links = set()
            # 不清空 downloading_files，因为下载中的文件应保持状态
            # 不清空 results，因为它是临时结果，重置后需要重新处理

    async def check_pause(self, worker) -> bool:
        """检查暂停状态，如果暂停则等待"""
        if worker and hasattr(worker, 'should_pause'):
            return worker.should_pause()
        return False

    def standard_print(self, level: str, message: str) -> None:
        """标准控制台输出"""
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {level.upper()} - {message}")

    async def get_video_links(self, start_url: str, worker=None) -> List[str]:
        """获取所有视频链接"""
        # 在开始浏览器操作前检查暂停状态
        if await self.check_pause(worker):
            self.standard_print("WARNING", "获取视频链接前检测到暂停指令")
            return []

        async with BrowserManager(headless=self.headless, download_dir=self.downloader.download_dir, 
                                 config_manager=self.config_manager) as browser:
            # 使用验证码绕过访问页面
            await browser.go_to_with_captcha_bypass(start_url)

            # 等待页面加载
            await asyncio.sleep(3)

            # 页面加载后检查暂停状态
            if await self.check_pause(worker):
                self.standard_print("WARNING", "页面加载后检测到暂停指令")
                return []

            # 首先定位到playlist-scroll容器
            playlist_container = await browser.query_element('//*[@id="playlist-scroll"]', timeout=10, raise_exc=False)

            if playlist_container:
                # 在playlist-scroll容器内查找所有带有href属性的链接元素
                # 使用完整的XPath限定在playlist-scroll范围内
                link_elements = await browser.find_element(
                    xpath='//*[@id="playlist-scroll"]//a[@href]',
                    find_all=True,
                    timeout=10,
                    raise_exc=False
                )

                if link_elements:
                    for element in link_elements:
                        # 在处理每个元素前检查暂停状态
                        if await self.check_pause(worker):
                            self.standard_print("WARNING", "处理视频链接时检测到暂停指令")
                            return list(self.all_video_links)

                        # 获取链接
                        try:
                            href = await element.get_attribute('href')
                        except:
                            href = element.get_attribute('href')

                        if href and isinstance(href, str) and href.startswith('https://hanime1.me/watch?v='):
                            # 只添加hanime1.me的视频链接，过滤掉其他链接
                            self.all_video_links.add(href)
            else:
                self.standard_print("WARNING", "未找到playlist-scroll容器")

            # 输出获取到的视频链接
            video_links_list = list(self.all_video_links)
            if video_links_list:
                self.standard_print("INFO", f"获取到 {len(video_links_list)} 个视频链接:")
                for i, link in enumerate(video_links_list, 1):
                    self.standard_print("INFO", f"  {i}. {link}")
            else:
                self.standard_print("WARNING", "未获取到任何视频链接")

            # 记录到TaskLogger
            if self.task_logger and self.task_id:
                self.task_logger.add_video_links(self.task_id, video_links_list)

            return video_links_list

    def update_progress(self, filename: str, progress: str):
        """更新下载进度"""
        self.downloading_files[filename] = progress

    def get_progress_text(self) -> str:
        """获取进度文本，格式化为多行"""
        if not self.downloading_files:
            return "等待开始..."

        progress_lines = []
        for filename, progress in self.downloading_files.items():
            # 简化文件名显示，只显示前20个字符
            display_name = filename[:20] + "..." if len(filename) > 20 else filename
            progress_lines.append(f"{display_name}: {progress}")

        return "\n".join(progress_lines)

    async def process_single_link(self, video_url: str, worker=None) -> bool:
        """处理单个视频链接，提取下载链接并下载"""
        self.standard_print("INFO", f"处理链接: {video_url}")

        # 检查暂停状态
        if await self.check_pause(worker):
            self.standard_print("WARNING", "处理链接前检测到暂停指令")
            return False

        async with BrowserManager(headless=self.headless, download_dir=self.downloader.download_dir,
                                 config_manager=self.config_manager) as browser:
            try:
                # 使用验证码绕过访问页面
                await browser.go_to_with_captcha_bypass(video_url)

                # 等待页面加载
                await asyncio.sleep(3)

                # 检查暂停状态
                if await self.check_pause(worker):
                    self.standard_print("WARNING", "页面加载后检测到暂停指令")
                    return False

                # 查找下载按钮
                download_btn = None
                for attempt in range(3):
                    if await self.check_pause(worker):
                        self.standard_print("WARNING", "查找下载按钮时检测到暂停指令")
                        return False

                    download_btn = await browser.find_element(id='downloadBtn', timeout=5, raise_exc=False)
                    if download_btn:
                        break
                    await asyncio.sleep(1)

                if download_btn:
                    download_href = download_btn.get_attribute('href')

                    if download_href:
                        self.download_links.append(download_href)
                        self.standard_print("SUCCESS", f"✓ 找到下载链接: {download_href}")

                        # 直接下载该视频
                        success = await self.downloader.download_from_page(
                            download_href, worker, self.task_logger, self.task_id
                        )

                        # 下载完成后从正在下载的文件中移除
                        if hasattr(worker, 'get_current_filename'):
                            filename = worker.get_current_filename()
                            if filename and filename in self.downloading_files:
                                del self.downloading_files[filename]

                        return success
                    else:
                        self.standard_print("WARNING", f"✗ 未找到下载链接: {video_url}")
                        # ========== 新增：记录失败链接 ==========
                        with self.lock:
                            self.failed_links.append(video_url)
                        return False
                else:
                    self.standard_print("WARNING", f"✗ 未找到下载按钮: {video_url}")
                    # ========== 新增：记录失败链接 ==========
                    with self.lock:
                        self.failed_links.append(video_url)
                    return False

            except Exception as e:
                self.standard_print("ERROR", f"处理链接 {video_url} 时出错: {e}")
                # ========== 新增：记录失败链接 ==========
                with self.lock:
                    self.failed_links.append(video_url)
                return False

    def _process_link_thread(self, worker):
        """处理单个链接的线程函数"""
        while True:
            try:
                # 从队列获取链接
                video_url = self.link_queue.get_nowait()
            except queue.Empty:
                break

            try:
                # 处理链接
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
        """批量处理链接，使用多线程顺序处理"""
        self.standard_print("INFO", f"开始批量处理 {len(links_batch)} 个链接，使用 {self.max_workers} 个工作线程")

        # 将链接放入队列
        for link in links_batch:
            self.link_queue.put(link)

        # 启动线程处理
        self.active_threads = min(self.max_workers, len(links_batch))
        threads = []

        for _ in range(self.active_threads):
            thread = threading.Thread(target=self._process_link_thread, args=(worker,))
            thread.daemon = True
            thread.start()
            threads.append(thread)

        # 等待所有线程完成
        self.link_queue.join()

        # 等待所有线程结束
        for thread in threads:
            thread.join()

        # 统计结果
        success_count = sum(1 for r in self.results if r)
        self.standard_print("INFO", f"处理完成: 成功 {success_count}/{len(links_batch)} 个链接")


class DownloadWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)
    progress_signal = pyqtSignal(str)
    file_progress_signal = pyqtSignal(str, str)
    count_updated_signal = pyqtSignal()  # <--- 新增信号

    def __init__(self, url: str, download_dir: str, headless: bool, task_logger=None, task_id=None, config_manager=None):
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.headless = headless
        self._is_running = True
        self._is_paused = False
        self._pause_condition = threading.Condition(threading.Lock())
        self.task_logger = task_logger
        self.task_id = task_id
        self.config_manager = config_manager  # 配置管理器
        self.last_error = ""  # 记录最后错误信息
        self.current_filename = ""  # 当前正在下载的文件名
        self.scraper = None  # HanimeScraper实例
        self.retry_failed_links = False  # 是否重试失败链接
        self.failed_links_to_retry = []  # 待重试的失败链接

    def run(self):
        """执行下载任务"""
        try:
            self.log_signal.emit(f"开始处理链接: {self.url}")

            # 每次任务开始时检测最新的无头模式设置
            self._detect_headless_setting()

            self.log_signal.emit(f"当前无头模式设置: {'启用' if self.headless else '禁用'}")

            # 创建scraper实例并设置参数
            self.scraper = HanimeScraper(
                max_workers=2,  # 使用2个线程处理视频链接
                headless=self.headless,
                download_dir=self.download_dir,
                task_logger=self.task_logger,
                task_id=self.task_id,
                config_manager=self.config_manager
            )

            # 设置进度回调
            self.scraper.downloader.set_progress_callback(self.on_progress_update)

            # 运行异步任务
            success = asyncio.run(self._process_link(self.scraper))

            # 如果任务被暂停或停止，不算失败
            if not self._is_running or self._is_paused:
                self.log_signal.emit(f"任务被停止或暂停: {self.url}")
                self.finished_signal.emit(False)
                return

            self.finished_signal.emit(success)

        except Exception as e:
            error_msg = f"下载任务出错: {str(e)}"
            self.log_signal.emit(error_msg)
            self.last_error = error_msg

            # 记录到TaskLogger
            if self.task_logger and self.task_id:
                self.task_logger.add_failed_link(self.task_id, self.url, "download_error")

            import traceback
            traceback.print_exc()
            self.finished_signal.emit(False)

    def on_progress_update(self, filename: str, progress: str):
        self.current_filename = filename
        if self.scraper:
            self.scraper.update_progress(filename, progress)
            progress_text = self.scraper.get_progress_text()
            self.progress_signal.emit(progress_text)
            self.file_progress_signal.emit(filename, progress)

            # 如果进度为“已完成”，触发计数更新
            if progress == "已完成":
                self.count_updated_signal.emit()

    def get_current_filename(self) -> str:
        """获取当前正在下载的文件名"""
        return self.current_filename

    async def _process_link(self, scraper: HanimeScraper):
        """异步处理单个链接"""
        # 检查是否被停止
        if not self._is_running:
            return False

        # 检查暂停状态
        if self.should_pause():
            return False

        # 如果是重试模式，直接处理失败链接
        if self.retry_failed_links and self.failed_links_to_retry:
            self.log_signal.emit(f"开始重试 {len(self.failed_links_to_retry)} 个失败的链接...")
            # 清除之前的失败记录
            if self.task_logger and self.task_id:
                self.task_logger.clear_failed_links(self.task_id)
            # 重置scraper的跟踪状态
            scraper.reset_link_tracking()
            await scraper.process_links_batch(self.failed_links_to_retry, self)

            # 检查是否还有失败的链接
            remaining_failed = scraper.get_failed_links()
            if remaining_failed:
                self.log_signal.emit(f"仍有 {len(remaining_failed)} 个链接失败，任务将暂停")
                return False
            else:
                self.log_signal.emit("所有链接重试成功！")
                return True

        # 正常模式：获取视频链接
        video_links = await scraper.get_video_links(self.url, self)

        # 再次检查暂停状态
        if self.should_pause():
            return False

        if not video_links:
            self.log_signal.emit("没有找到任何视频链接")
            return False

        # 触发计数更新信号（此时 TaskLogger 已存入视频链接列表）
        self.count_updated_signal.emit()

        self.log_signal.emit(f"开始处理 {len(video_links)} 个视频链接...")
        await scraper.process_links_batch(video_links, self)

        # 检查是否有失败链接需要重试
        failed_links = scraper.get_failed_links()
        if failed_links:
            self.log_signal.emit(f"发现 {len(failed_links)} 个失败链接，任务将暂停以便重试")
            self.failed_links_to_retry = failed_links
            return False
        else:
            self.log_signal.emit(f"处理完成！共找到 {len(scraper.download_links)} 个下载链接")
            return True

    def stop(self):
        """停止任务"""
        self._is_running = False
        # 唤醒可能处于暂停状态的线程
        with self._pause_condition:
            self._pause_condition.notify_all()

    def pause(self):
        """暂停任务"""
        self._is_paused = True
        # 立即通知条件变量，让should_pause方法立即响应
        with self._pause_condition:
            self._pause_condition.notify_all()

    def resume(self):
        """恢复任务"""
        self._is_paused = False
        with self._pause_condition:
            self._pause_condition.notify_all()

    def should_pause(self) -> bool:
        """检查是否应该暂停"""
        if self._is_paused and self._is_running:
            with self._pause_condition:
                # 等待直到恢复或停止
                while self._is_paused and self._is_running:
                    self._pause_condition.wait(0.1)
            return True
        return False

    def is_running(self) -> bool:
        """检查任务是否仍在运行"""
        return self._is_running

    def _detect_headless_setting(self) -> None:
        """检测最新的无头模式设置

        每次任务开始时调用此方法，确保使用最新的无头模式配置
        """
        try:
            from ToolPart.Config import ConfigManager
            config_manager = ConfigManager()

            # 从配置文件读取最新的无头模式设置
            latest_headless = config_manager.get("headless_mode", True)

            # 如果设置发生了变化，更新当前实例
            if self.headless != latest_headless:
                self.log_signal.emit(f"检测到无头模式设置变更: {self.headless} -> {latest_headless}")
                self.headless = latest_headless

        except Exception as e:
            # 如果检测失败，保持原有设置并记录错误
            self.log_signal.emit(f"检测无头模式设置时出错: {str(e)}，使用原有设置: {self.headless}")