import os
import random
import re
import threading
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Tuple
import requests
from PyQt5.QtCore import QThread, pyqtSignal
from ToolPart.Browser import get_browser
from ToolPart.Logger import log_failure, TaskLogger


class VideoDownloadThread(QThread):
    # 定义信号
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(str, float)
    finished_signal = pyqtSignal(str, list)  # task_id, failed_urls

    def __init__(self, list_url: str, download_dir: str, task_id: str,
                 task_logger: Optional[TaskLogger] = None, is_retry: bool = False,
                 headless: bool = True):  # 添加headless参数
        super().__init__()
        self.list_url = list_url
        self.download_dir = download_dir
        self.task_id = task_id
        self.task_logger = task_logger
        self.is_retry = is_retry  # 是否是重试任务
        self.headless = headless  # 保存headless参数
        self.running = True
        self.paused = False
        self.pause_cond = threading.Condition(threading.Lock())
        self.max_workers = 2  # 减少并发数，避免资源冲突
        os.makedirs(self.download_dir, exist_ok=True)

        # 确保日志目录存在
        self.logger_dir = "./logger"
        os.makedirs(self.logger_dir, exist_ok=True)

        # 定义非法字符的正则表达式模式
        self.illegal_chars_pattern = re.compile(r'[\\/*?:"<>|]')

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
        """暂停下载任务"""
        self.paused = True
        self.log_message(f"下载任务已暂停: {self.list_url}")

        # 更新任务状态
        if self.task_logger:
            self.task_logger.update_task_status(self.task_id, "paused")

    def resume(self) -> None:
        """继续下载任务"""
        with self.pause_cond:
            self.paused = False
            self.pause_cond.notify()
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
        """如果任务被暂停，则等待直到继续"""
        with self.pause_cond:
            while self.paused and self.running:
                self.pause_cond.wait()

    def get_video_links(self) -> Tuple[Optional[List[str]], Optional[str]]:
        """获取列表页中的所有视频链接和播放列表标题"""
        self.log_message(f"正在从 {self.list_url} 获取视频列表...")
        browser = None
        links: Optional[List[str]] = None
        playlist_title: Optional[str] = None

        try:
            browser = get_browser(self.headless)  # 使用headless参数
            browser.get(self.list_url)

            # 如果不是无头模式，记录日志
            if not self.headless:
                self.log_message("非无头模式：浏览器窗口已打开，请查看浏览器界面")

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
        """下载单个视频，增加失败重试机制"""
        if not self.running:
            return False

        # 记录视频任务开始
        if self.task_logger:
            self.task_logger.log_video_task_start(self.task_id, video_url)

        max_retries = 2  # 减少重试次数
        retry_count = 0
        success = False
        last_error = ""
        filename = None

        while retry_count <= max_retries and not success and self.running:
            retry_count += 1
            self.log_message(f"尝试下载视频: {video_url} (尝试 {retry_count}/{max_retries + 1})")
            try:
                success, error, file = self._download_video_attempt(video_url)
                if not success:
                    last_error = error
                    if retry_count <= max_retries:
                        self.log_message(f"第 {retry_count} 次下载失败，稍后重试...")
                        time.sleep(1 + random.random())  # 减少等待时间
                else:
                    filename = file
            except Exception as e:
                last_error = str(e)
                self.log_message(f"下载过程中发生异常: {str(e)}")
                time.sleep(2)

        if success:
            self.log_message(f"成功下载视频: {video_url}")
            # 记录视频任务完成
            if self.task_logger:
                self.task_logger.log_video_task_complete(self.task_id, video_url)
            return True
        else:
            self.log_message(f"下载失败: {video_url} (超过最大重试次数)")
            if last_error:
                log_filename = filename if filename else video_url
                log_failure(self.logger_dir, log_filename, video_url, last_error)

            # 记录视频任务失败
            if self.task_logger:
                self.task_logger.log_video_task_failed(self.task_id, video_url, last_error)

            return False

    def _download_video_attempt(self, video_url: str) -> Tuple[bool, str, Optional[str]]:
        """单个视频下载尝试，返回（是否成功，错误信息，文件名）"""
        self.log_message(f"处理视频: {video_url}")
        browser = None
        filename = None

        try:
            browser = get_browser(self.headless)  # 使用headless参数
            browser.get(video_url)

            # 如果不是无头模式，记录日志
            if not self.headless:
                self.log_message("非无头模式：正在打开视频页面...")

            # 使用条件等待替代固定等待
            start_time = time.time()
            download_btn = None
            while time.time() - start_time < 20:
                self.wait_if_paused()
                if not self.running:
                    return False, "任务已停止", None

                download_btn = browser.ele('#downloadBtn', timeout=1)
                if download_btn:
                    break
                time.sleep(1)
            else:
                error_msg = "等待下载按钮加载超时"
                self.log_message(error_msg)
                return False, error_msg, None

            download_page_url = download_btn.attr('href')
            if not download_page_url:
                error_msg = "下载按钮没有有效的链接"
                self.log_message(error_msg)
                return False, error_msg, None

            self.log_message(f"找到下载页面: {download_page_url}")
            browser.get(download_page_url)

            # 如果不是无头模式，记录日志
            if not self.headless:
                self.log_message("非无头模式：正在打开下载页面...")

            # 等待Cloudflare验证
            start_time = time.time()
            while time.time() - start_time < 30:
                self.wait_if_paused()
                if not self.running:
                    return False, "任务已停止", None

                if "just a moment" not in browser.title.lower():
                    break
                time.sleep(1)
            else:
                error_msg = "等待Cloudflare验证完成超时"
                self.log_message(error_msg)
                return False, error_msg, None

            # 定位下载链接
            download_table = browser.ele('#content-div', timeout=10)
            if not download_table:
                error_msg = "未找到下载表格"
                self.log_message(error_msg)
                return False, error_msg, None

            download_link_ele = download_table.ele('xpath:.//tr[2]/td[5]/a', timeout=10)
            if not download_link_ele:
                error_msg = "未找到下载链接元素"
                self.log_message(error_msg)
                return False, error_msg, None

            video_download_url = download_link_ele.attr('data-url')
            raw_filename = download_link_ele.attr('download') + '.mp4'

            filename = self.sanitize_filename(raw_filename)
            self.log_message(f"原始文件名: {raw_filename} -> 清洗后: {filename}")

            # 检查文件是否已存在
            filepath = os.path.join(self.download_dir, filename)
            if os.path.exists(filepath) and os.path.isfile(filepath):
                self.log_message(f"文件已存在，跳过下载: {filename}")
                return True, "文件已存在，跳过下载", filename

            if not video_download_url or not filename:
                error_msg = "未找到下载URL或文件名"
                self.log_message(error_msg)
                return False, error_msg, None

            self.log_message(f"找到视频URL: {video_download_url}")
            self.log_message(f"正在下载: {filename}")

            success, error = self.save_video(video_download_url, filename)
            return success, error, filename

        except Exception as e:
            error_msg = f"处理视频时出错: {str(e)}"
            self.log_message(error_msg)
            return False, error_msg, filename
        finally:
            if browser:
                try:
                    browser.quit()
                except Exception as e:
                    self.log_message(f"关闭浏览器时出错: {str(e)}")

    def save_video(self, url: str, filename: str) -> Tuple[bool, str]:
        """保存视频文件，返回（是否成功，错误信息）"""
        clean_filename = self.sanitize_filename(filename)
        filepath = os.path.join(self.download_dir, clean_filename)

        if not self.running:
            return False, "任务已停止"

        try:
            # 最终检查文件是否存在
            if os.path.exists(filepath) and os.path.isfile(filepath):
                self.log_message(f"文件已存在，跳过下载: {clean_filename}")
                return True, "文件已存在，跳过下载"

            headers = {
                'Referer': 'https://hanime1.me/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }

            session = requests.Session()
            response = session.get(url, headers=headers, stream=True, timeout=60)
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
                            if abs(percent - last_percent) > 1 or percent == 100:
                                self.log_message(f"下载进度: {clean_filename} - {percent:.1f}%")
                                last_percent = percent

            self.log_message(f"成功保存: {filepath}")
            return True, ""

        except Exception as e:
            error_msg = str(e)
            self.log_message(f"下载失败: {clean_filename} - {error_msg}")
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            return False, error_msg

    def run(self) -> None:
        """运行下载任务"""
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

            # 使用线程池并发下载视频
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []
                for i, link in enumerate(video_links):
                    if not self.running:
                        break

                    if 'search?query' in link:
                        self.log_message(f"链接: {link} 不是视频链接，跳过")
                        continue

                    self.log_message(f"提交下载任务: 视频 {i + 1}/{len(video_links)}")
                    future = executor.submit(self.download_video, link)
                    futures.append(future)
                    time.sleep(random.uniform(0.5, 1.0))  # 减少等待时间

                # 等待所有任务完成
                for i, future in enumerate(as_completed(futures)):
                    if not self.running:
                        for f in futures:
                            if not f.done():
                                f.cancel()
                        self.log_message("下载任务已取消")
                        break

                    try:
                        success = future.result(timeout=1800)  # 30分钟超时
                        if success:
                            self.log_message(f"视频 {i + 1} 下载成功")
                        else:
                            self.log_message(f"视频 {i + 1} 下载失败")
                            if i < len(video_links):
                                failed_downloads.append(video_links[i])
                    except Exception as e:
                        self.log_message(f"视频下载出错: {str(e)}")
                        if i < len(video_links):
                            failed_downloads.append(video_links[i])

            # 检查任务状态
            if self.task_logger:
                task_info = self.task_logger.get_task_info(self.task_id)
                if task_info:
                    if failed_downloads:
                        # 有失败视频，标记任务失败
                        self.task_logger.update_task_status(self.task_id, "failed")
                    else:
                        # 所有视频都成功，标记任务完成
                        self.task_logger.update_task_status(self.task_id, "completed")

            # 发送完成信号
            if self.running:
                self.finished_signal.emit(self.task_id, failed_downloads)
                self.log_message(f"下载任务完成: {self.list_url}")

        except Exception as e:
            self.log_message(f"下载任务异常: {str(e)}")
            # 发生异常时，标记任务失败
            if self.task_logger:
                self.task_logger.mark_task_failed(self.task_id, str(e))

            failed_downloads.append(self.list_url)
            self.finished_signal.emit(self.task_id, failed_downloads)