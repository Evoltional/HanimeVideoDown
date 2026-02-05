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


class VideoDownloader:
    def __init__(self, download_dir="./downloads", max_retries=3, headless=True):
        self.download_dir = download_dir
        self.max_retries = max_retries
        self.headless = headless
        # 确保下载目录存在
        os.makedirs(self.download_dir, exist_ok=True)

    async def _check_pause(self, worker) -> bool:
        """检查暂停状态，如果暂停则等待"""
        if worker and hasattr(worker, 'should_pause'):
            return worker.should_pause()
        return False

    async def extract_download_info(self, download_page_url: str, worker=None) -> Tuple[Optional[str], Optional[str]]:
        """
        从下载页面提取视频下载链接和文件名
        """
        # 在进入浏览器操作前检查暂停状态
        if await self._check_pause(worker):
            print("浏览器操作被暂停")
            return None, None

        async with BrowserManager(headless=self.headless, download_dir=self.download_dir) as browser:
            print(f"访问下载页面: {download_page_url}")

            # 使用验证码绕过访问页面
            tab = await browser.go_to_with_captcha_bypass(download_page_url)

            # 等待页面加载
            await asyncio.sleep(3)

            # 页面加载后再次检查暂停状态
            if await self._check_pause(worker):
                print("页面加载后检测到暂停指令")
                return None, None

            try:
                # 在查找元素前检查暂停状态
                if await self._check_pause(worker):
                    print("元素查找前检测到暂停指令")
                    return None, None

                # 查找下载链接列表表格
                table_element = None
                for attempt in range(3):
                    if await self._check_pause(worker):
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

                print("找到下载链接列表表格")

                # 在查找下载按钮前检查暂停状态
                if await self._check_pause(worker):
                    print("下载按钮查找前检测到暂停指令")
                    return None, None

                # 查找第一个下载按钮
                first_download_btn = None
                for attempt in range(3):
                    if await self._check_pause(worker):
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

                print(f"提取到下载链接: {download_url}")
                print(f"提取到文件名: {filename}")

                return download_url, filename

            except Exception as e:
                print(f"提取下载信息时出错: {e}")
                import traceback
                traceback.print_exc()
                return None, None

    def download_video(self, video_url: str, filename: str, worker=None, task_logger=None, task_id=None) -> bool:
        """
        下载视频文件，使用文件名前缀标识下载状态
        """
        if not video_url or not filename:
            print("下载链接或文件名为空，跳过下载")
            return False

        # 清理文件名，移除非法字符
        safe_filename = _sanitize_filename(filename)

        # 添加下载中前缀
        downloading_filename = f"下载中_{safe_filename}"
        downloading_file_path = os.path.join(self.download_dir, downloading_filename)
        final_file_path = os.path.join(self.download_dir, safe_filename)

        # 检查最终文件是否已存在
        if os.path.exists(final_file_path):
            print(f"文件已存在，跳过下载: {final_file_path}")

            # 记录到TaskLogger
            if task_logger and task_id:
                task_logger.add_downloaded_video(task_id, safe_filename)

            return True

        # 检查是否有之前的下载中文件
        if os.path.exists(downloading_file_path):
            print(f"发现之前的下载中文件: {downloading_file_path}")
            # 可以选择继续下载或重新开始，这里选择重新开始
            try:
                os.remove(downloading_file_path)
                print("已删除之前的下载中文件")
            except Exception as e:
                print(f"删除之前下载文件失败: {e}")
                return False

        print(f"开始下载: {safe_filename} (临时文件: {downloading_filename})")

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
                                    print("\n下载已被暂停")
                                    # 保留下载中的文件，不删除
                                    return False

                                # 检查是否停止
                                if worker and hasattr(worker, 'is_running') and not worker.is_running():
                                    print("\n下载已被停止")
                                    # 保留下载中的文件，不删除
                                    return False

                            f.write(chunk)
                            downloaded_size += len(chunk)

                            # 显示下载进度
                            if total_size > 0:
                                progress = (downloaded_size / total_size) * 100
                                print(f"\r下载进度: {progress:.1f}% ({downloaded_size}/{total_size} bytes)", end='')

                print(f"\n下载完成，正在重命名文件...")

                # 下载完成后重命名为最终文件名
                try:
                    os.rename(downloading_file_path, final_file_path)
                    print(f"文件重命名完成: {final_file_path}")
                except Exception as e:
                    print(f"文件重命名失败: {e}")
                    return False

                # 记录到TaskLogger
                if task_logger and task_id:
                    task_logger.add_downloaded_video(task_id, safe_filename)

                return True

            except Exception as e:
                retry_count += 1
                print(f"下载失败 (重试 {retry_count}/{self.max_retries}): {e}")
                if retry_count >= self.max_retries:
                    print(f"下载最终失败: {e}")
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
            print("无法提取下载信息")
            return False


class HanimeScraper:
    def __init__(self, max_workers=2, headless=True, download_dir="./downloads", task_logger=None, task_id=None):
        self.all_video_links = set()
        self.download_links = []
        self.downloader = VideoDownloader(download_dir=download_dir, headless=headless)
        self.max_workers = max_workers
        self.task_logger = task_logger
        self.task_id = task_id
        self.headless = headless  # 保存headless参数

        # 多线程处理队列
        self.link_queue = queue.Queue()
        self.results = []
        self.active_threads = 0
        self.lock = threading.Lock()

    async def _check_pause(self, worker) -> bool:
        """检查暂停状态，如果暂停则等待"""
        if worker and hasattr(worker, 'should_pause'):
            return worker.should_pause()
        return False

    async def get_video_links(self, start_url: str, worker=None) -> List[str]:
        """获取所有视频链接"""
        # 在开始浏览器操作前检查暂停状态
        if await self._check_pause(worker):
            print("获取视频链接前检测到暂停指令")
            return []

        async with BrowserManager(headless=self.headless, download_dir=self.downloader.download_dir) as browser:
            # 使用验证码绕过访问页面
            tab = await browser.go_to_with_captcha_bypass(start_url)

            # 等待页面加载
            await asyncio.sleep(3)

            # 页面加载后检查暂停状态
            if await self._check_pause(worker):
                print("页面加载后检测到暂停指令")
                return []

            overlay_elements = await browser.find_element(class_name='overlay', find_all=True, timeout=10,
                                                          raise_exc=False)

            if overlay_elements:
                for element in overlay_elements:
                    # 在处理每个元素前检查暂停状态
                    if await self._check_pause(worker):
                        print("处理视频链接时检测到暂停指令")
                        return list(self.all_video_links)

                    # 获取链接
                    href = element.get_attribute('href')
                    if href:
                        self.all_video_links.add(href)

            # 记录到TaskLogger
            if self.task_logger and self.task_id:
                self.task_logger.add_video_links(self.task_id, list(self.all_video_links))

            return list(self.all_video_links)

    async def process_single_link(self, video_url: str, worker=None) -> bool:
        """处理单个视频链接，提取下载链接并下载"""
        print(f"处理链接: {video_url}")

        # 检查暂停状态
        if await self._check_pause(worker):
            print("处理链接前检测到暂停指令")
            return False

        async with BrowserManager(headless=self.headless, download_dir=self.downloader.download_dir) as browser:
            try:
                # 使用验证码绕过访问页面
                tab = await browser.go_to_with_captcha_bypass(video_url)

                # 等待页面加载
                await asyncio.sleep(3)

                # 检查暂停状态
                if await self._check_pause(worker):
                    print("页面加载后检测到暂停指令")
                    return False

                # 查找下载按钮
                download_btn = None
                for attempt in range(3):
                    if await self._check_pause(worker):
                        print("查找下载按钮时检测到暂停指令")
                        return False

                    download_btn = await browser.find_element(id='downloadBtn', timeout=5, raise_exc=False)
                    if download_btn:
                        break
                    await asyncio.sleep(1)

                if download_btn:
                    download_href = download_btn.get_attribute('href')

                    if download_href:
                        self.download_links.append(download_href)
                        print(f"✓ 找到下载链接: {download_href}")

                        # 直接下载该视频
                        success = await self.downloader.download_from_page(
                            download_href, worker, self.task_logger, self.task_id
                        )

                        return success
                    else:
                        print(f"✗ 未找到下载链接: {video_url}")
                        return False
                else:
                    print(f"✗ 未找到下载按钮: {video_url}")
                    return False

            except Exception as e:
                print(f"处理链接 {video_url} 时出错: {e}")
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
                print(f"线程处理链接失败: {video_url}, 错误: {e}")
                with self.lock:
                    self.results.append(False)
            finally:
                self.link_queue.task_done()

        with self.lock:
            self.active_threads -= 1

    async def process_links_batch(self, links_batch: List[str], worker=None) -> None:
        """批量处理链接，使用多线程顺序处理"""
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
        print(f"处理完成: 成功 {success_count}/{len(links_batch)} 个链接")


class DownloadWorker(QThread):
    """下载工作线程"""
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)  # 参数为下载是否成功
    progress_signal = pyqtSignal(int)  # 添加进度信号

    def __init__(self, url: str, download_dir: str, headless: bool, task_logger=None, task_id=None):
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        self.headless = headless
        self._is_running = True
        self._is_paused = False
        self._pause_condition = threading.Condition(threading.Lock())
        self.task_logger = task_logger
        self.task_id = task_id

    def run(self):
        """执行下载任务"""
        try:
            self.log_signal.emit(f"开始处理链接: {self.url}")

            # 创建scraper实例并设置参数
            scraper = HanimeScraper(
                max_workers=2,  # 使用2个线程处理视频链接
                headless=self.headless,
                download_dir=self.download_dir,
                task_logger=self.task_logger,
                task_id=self.task_id
            )

            # 运行异步任务
            success = asyncio.run(self._process_link(scraper))

            self.finished_signal.emit(success)
        except Exception as e:
            self.log_signal.emit(f"下载任务出错: {str(e)}")
            import traceback
            traceback.print_exc()
            self.finished_signal.emit(False)

    async def _process_link(self, scraper: HanimeScraper):
        """异步处理单个链接"""
        # 检查是否被停止
        if not self._is_running:
            return False

        # 检查暂停状态
        if self.should_pause():
            return False

        # 获取视频链接
        video_links = await scraper.get_video_links(self.url, self)

        # 再次检查暂停状态
        if self.should_pause():
            return False

        if not video_links:
            self.log_signal.emit("没有找到任何视频链接")
            return False

        self.log_signal.emit(f"开始处理 {len(video_links)} 个视频链接...")
        await scraper.process_links_batch(video_links, self)

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