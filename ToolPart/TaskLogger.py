import json
import os
import threading
import logging
from datetime import datetime
from enum import Enum
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


class TaskLogger:
    def __init__(self, log_dir: str = "Logger", config_file: str = "TaskLogger.json"):
        self.log_dir = log_dir
        self.config_file = os.path.join(log_dir, config_file)
        self.exis_file = os.path.join(log_dir, "Exis.json")
        self.lock = threading.Lock()
        os.makedirs(self.log_dir, exist_ok=True)

    def add_task(self, task_id: str, url: str, download_dir: str) -> None:
        with self.lock:
            tasks = self._load_tasks()
            task_info = {
                "url": url,
                "download_dir": download_dir,
                "status": "waiting",
                "video_links": [],
                "downloaded_videos": [],
                "failed_links": [],
                "failure_type": "",
                "total_video_count": 0,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
            tasks[task_id] = task_info
            self._save_tasks(tasks)

    def update_task_status(self, task_id: str, status: str) -> None:
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                tasks[task_id]["status"] = status
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> None:
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                tasks[task_id].update(updates)
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

    def add_video_links(self, task_id: str, video_links: List[str]) -> None:
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                tasks[task_id]["video_links"] = video_links
                tasks[task_id]["total_video_count"] = len(video_links)
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

    # ---------- 修改点：将移除链接的逻辑独立出来 ----------
    def add_downloaded_video(self, task_id: str, video_filename: str, video_url: str = None) -> None:
        """
        添加已下载的视频记录，并从 video_links 和 failed_links 中移除对应的链接（如果提供）
        :param task_id: 任务 ID
        :param video_filename: 视频文件名
        :param video_url: 可选的视频 URL，如果提供则从 video_links 和 failed_links 中移除
        """
        import re
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                # 确保 downloaded_videos 列表存在
                if "downloaded_videos" not in tasks[task_id]:
                    tasks[task_id]["downloaded_videos"] = []

                # 1. 无论文件名是否已存在，都尝试移除链接（关键修复）
                if video_url:
                    # 从 video_links 中移除
                    if "video_links" in tasks[task_id]:
                        video_links = tasks[task_id]["video_links"]
                        if video_url in video_links:
                            video_links.remove(video_url)
                            tasks[task_id]["video_links"] = video_links
                            self.log(LogLevel.INFO, f"已从任务 {task_id} 的视频链接列表中移除：{video_url}")
                    
                    # 从 failed_links 中移除
                    if "failed_links" in tasks[task_id]:
                        failed_links = tasks[task_id]["failed_links"]
                        if video_url in failed_links:
                            failed_links.remove(video_url)
                            tasks[task_id]["failed_links"] = failed_links
                            self.log(LogLevel.INFO, f"已从失败链接中移除：{video_url}")

                # 2. 删除 [中字後補] 及其前后空格后保存
                cleaned_filename = re.sub(r'\s*\[中字後補\]\s*', '', video_filename)
                
                # 3. 如果文件名尚未记录，则添加到 downloaded_videos
                if cleaned_filename not in tasks[task_id]["downloaded_videos"]:
                    tasks[task_id]["downloaded_videos"].append(cleaned_filename)

                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

    def add_failed_link(self, task_id: str, link: str, failure_type: str = "") -> None:
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                if "failed_links" not in tasks[task_id]:
                    tasks[task_id]["failed_links"] = []
                if link not in tasks[task_id]["failed_links"]:
                    tasks[task_id]["failed_links"].append(link)
                    if failure_type:
                        tasks[task_id]["failure_type"] = failure_type
                    tasks[task_id]["updated_at"] = datetime.now().isoformat()
                    self._save_tasks(tasks)

    def remove_failed_link(self, task_id: str, link: str) -> None:
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                failed_links = tasks[task_id].get("failed_links", [])
                if link in failed_links:
                    failed_links.remove(link)
                    tasks[task_id]["failed_links"] = failed_links
                    tasks[task_id]["updated_at"] = datetime.now().isoformat()
                    self._save_tasks(tasks)
                    self.log(LogLevel.INFO, f"已从失败链接中移除: {link}")

    def remove_video_link(self, task_id: str, link: str) -> None:
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                video_links = tasks[task_id].get("video_links", [])
                if link in video_links:
                    video_links.remove(link)
                    tasks[task_id]["video_links"] = video_links
                    tasks[task_id]["updated_at"] = datetime.now().isoformat()
                    self._save_tasks(tasks)
                    self.log(LogLevel.INFO, f"已从任务 {task_id} 的视频链接列表中移除: {link}")

    def clear_failed_links(self, task_id: str) -> None:
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                tasks[task_id]["failed_links"] = []
                tasks[task_id]["failure_type"] = ""
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

    def get_remaining_video_links(self, task_id: str) -> List[str]:
        """
        获取任务剩余未下载的视频链接（包括失败的链接），排除已下载的
        :param task_id: 任务 ID
        :return: 剩余的视频链接列表
        """
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                video_links = set(tasks[task_id].get("video_links", []))
                failed_links = set(tasks[task_id].get("failed_links", []))
                downloaded_videos = set(tasks[task_id].get("downloaded_videos", []))
                
                # 合并 video_links 和 failed_links，然后排除已下载的
                remaining_links = list((video_links | failed_links) - downloaded_videos)
                
                self.log(LogLevel.DEBUG, 
                    f"任务 {task_id[:8]} 剩余链接计算: "
                    f"video_links={len(video_links)}, "
                    f"failed_links={len(failed_links)}, "
                    f"downloaded={len(downloaded_videos)}, "
                    f"remaining={len(remaining_links)}")
                
                return remaining_links
            return []

    def get_task_failed_links(self, task_id: str) -> List[str]:
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                failed_links = tasks[task_id].get("failed_links", [])
                return failed_links
            return []

    def remove_task(self, task_id: str) -> bool:
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                del tasks[task_id]
                self._save_tasks(tasks)
                self.log(LogLevel.INFO, f"已从TaskLogger删除任务: {task_id}")
                return True
            else:
                self.log(LogLevel.WARNING, f"任务不存在，无法删除: {task_id}")
                return False

    def get_all_tasks(self) -> Dict[str, Any]:
        with self.lock:
            return self._load_tasks()

    def get_incomplete_tasks(self) -> Dict[str, Any]:
        with self.lock:
            tasks = self._load_tasks()
            incomplete_tasks = {}
            for task_id, task_info in tasks.items():
                if task_info["status"] in ["waiting", "running", "paused", "failed", "stopped"]:
                    incomplete_tasks[task_id] = task_info
            return incomplete_tasks

    def get_waiting_and_running_tasks(self) -> Dict[str, Any]:
        with self.lock:
            tasks = self._load_tasks()
            result = {}
            for task_id, task_info in tasks.items():
                if task_info["status"] in ["waiting", "running"]:
                    result[task_id] = task_info
            return result

    def get_failed_tasks(self) -> Dict[str, Any]:
        with self.lock:
            tasks = self._load_tasks()
            failed_tasks = {}
            for task_id, task_info in tasks.items():
                if task_info["status"] == "failed":
                    failed_tasks[task_id] = task_info
            return failed_tasks

    def get_downloading_files(self, download_dir: str) -> List[str]:
        downloading_files = []
        try:
            if os.path.exists(download_dir):
                files = os.listdir(download_dir)
                downloading_files = [f for f in files if f.startswith('下载中_') and f.endswith('.mp4')]
        except Exception as e:
            logger.error(f"扫描下载目录时出错: {e}")
        return downloading_files

    def clear_all_tasks(self) -> None:
        with self.lock:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=4, ensure_ascii=False)

    def cleanup_completed_task(self, task_id: str) -> bool:
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                task_info = tasks[task_id]
                if task_info["status"] == "completed":
                    del tasks[task_id]
                    self._save_tasks(tasks)
                    self.log(LogLevel.INFO, f"已清理已完成任务: {task_id}")
                    return True
                else:
                    self.log(LogLevel.WARNING, f"任务 {task_id} 状态为 {task_info['status']}，不能清理")
                    return False
            else:
                self.log(LogLevel.WARNING, f"任务 {task_id} 不存在")
                return False

    def _load_tasks(self) -> Dict[str, Any]:
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_tasks(self, tasks: Dict[str, Any]) -> None:
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(tasks, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存任务日志失败: {e}")

    def log(self, level: LogLevel, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.log(getattr(logging, level.value), f"{timestamp} - {message}")

    def get_plain_log_message(self, level: LogLevel, message: str) -> str:
        timestamp = datetime.now().strftime("%H:%M:%S")
        return f"[{timestamp}] {level.value} - {message}"

    def update_exis_file(self, storage_dir: str) -> List[dict]:
        """
        扫描存储目录中的所有视频文件，返回文件名和路径信息
        :param storage_dir: 存储目录路径
        :return: 找到的视频文件列表，每个元素为 {"filename": 文件名, "path": 完整路径}
        """
        import re
        video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm'}
        video_files = []
        
        try:
            # 递归遍历所有子文件夹
            for root, dirs, files in os.walk(storage_dir):
                for filename in files:
                    _, ext = os.path.splitext(filename)
                    if ext.lower() in video_extensions:
                        # 删除 [中字後補] 及其前后空格
                        cleaned_filename = re.sub(r'\s*\[中字後補\]\s*', '', filename)
                        # 构建完整路径
                        full_path = os.path.join(root, filename)
                        # 保存文件名和路径信息
                        video_files.append({
                            "filename": cleaned_filename,
                            "path": full_path
                        })
            
            return video_files
            
        except Exception as e:
            logger.error(f"扫描目录时出错: {e}")
            return []

    def batch_update_exis_file(self, storage_dirs: List[str], incremental: bool = True) -> int:
        """
        批量扫描多个存储目录，更新Exis.json
        Exis.json格式：[{"filename": "文件名", "path": "完整路径"}, ...]
        按文件名排序
        :param storage_dirs: 存储目录列表
        :param incremental: 是否增量更新（True=只添加不删除，False=清空后重新扫描）
        :return: 总共找到的视频文件数量
        """
        all_video_files = []
        
        # 如果是增量更新，先加载现有的视频文件
        existing_videos = []
        if incremental:
            try:
                with self.lock:
                    if os.path.exists(self.exis_file):
                        with open(self.exis_file, 'r', encoding='utf-8') as f:
                            existing_videos = json.load(f)
            except Exception as e:
                logger.error(f"加载现有Exis.json时出错: {e}")
                existing_videos = []
        
        # 扫描所有目录
        for storage_dir in storage_dirs:
            video_files = self.update_exis_file(storage_dir)
            all_video_files.extend(video_files)
        
        # 合并现有文件和新扫描的文件
        merged_files = existing_videos + all_video_files
        
        # 去重（基于文件名）
        seen_filenames = set()
        unique_files = []
        for file_info in merged_files:
            filename = file_info.get("filename", "")
            if filename not in seen_filenames:
                seen_filenames.add(filename)
                unique_files.append(file_info)
        
        # 按文件名排序（不区分大小写）
        unique_files.sort(key=lambda x: x.get("filename", "").lower())
        
        # 保存到Exis.json（覆盖原有内容）
        try:
            with self.lock:
                with open(self.exis_file, 'w', encoding='utf-8') as f:
                    json.dump(unique_files, f, indent=4, ensure_ascii=False)
            
            if incremental:
                added_count = len(unique_files) - len(existing_videos)
                logger.info(f"已更新Exis.json，新增 {added_count} 个文件，共 {len(unique_files)} 个唯一视频文件（已按名称排序）")
            else:
                logger.info(f"已更新Exis.json，共找到 {len(unique_files)} 个唯一视频文件（已按名称排序）")
            return len(unique_files)
        except Exception as e:
            logger.error(f"保存Exis.json时出错: {e}")
            return 0

    def clear_exis_file(self) -> None:
        """
        清空Exis.json文件
        """
        try:
            with self.lock:
                with open(self.exis_file, 'w', encoding='utf-8') as f:
                    json.dump([], f, indent=4, ensure_ascii=False)
            logger.info("已清空Exis.json")
        except Exception as e:
            logger.error(f"清空Exis.json时出错: {e}")
            raise

    def is_video_exists(self, filename: str) -> bool:
        """
        检查视频文件是否已存在于Exis.json中
        :param filename: 视频文件名
        :return: 如果存在返回True，否则返回False
        """
        try:
            with self.lock:
                if not os.path.exists(self.exis_file):
                    return False
                
                with open(self.exis_file, 'r', encoding='utf-8') as f:
                    existing_videos = json.load(f)
                
                # 删除 [中字後補] 及其前后空格后再对比
                import re
                cleaned_filename = re.sub(r'\s*\[中字後補\]\s*', '', filename)
                
                # 检查是否存在（兼容旧格式和新格式）
                for video_info in existing_videos:
                    if isinstance(video_info, dict):
                        # 新格式：{"filename": "...", "path": "..."}
                        existing_name = video_info.get("filename", "")
                        if filename == existing_name or cleaned_filename == existing_name:
                            return True
                    else:
                        # 旧格式：直接是文件名字符串
                        if filename == video_info or cleaned_filename == video_info:
                            return True
                
                return False
        except Exception as e:
            logger.error(f"检查视频是否存在时出错: {e}")
            return False