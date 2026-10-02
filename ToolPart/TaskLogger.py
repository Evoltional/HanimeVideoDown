import copy
import json
import os
import re
import threading
import time
import logging
from datetime import datetime
from enum import Enum
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

# 文件名清理正则（与下载端保持一致，用于重复比对）
_SUB_MARKETING = re.compile(r'\s*\[中字後補\]\s*')

# 合并写入的合并窗口（秒）：在窗口内的多次状态变更只触发一次磁盘写入
_FLUSH_INTERVAL = 0.3


class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


class TaskLogger:
    """任务与存储目录日志（线程安全，内存索引 + 合并落盘）。

    性能优化点（相对旧实现）：
    - 任务状态全部驻留内存，每次变更只做"合并落盘"，不再每次全量重写 JSON；
    - is_video_exists 基于内存 set 做 O(1) 查重；
    - 存储目录扫描基于目录 mtime 做增量，未变化的目录跳过 os.walk。
    """

    def __init__(self, log_dir: str = "Logger", config_file: str = "TaskLogger.json"):
        self.log_dir = log_dir
        self.config_file = os.path.join(log_dir, config_file)
        self.exis_file = os.path.join(log_dir, "Exis.json")
        self.exis_meta_file = os.path.join(log_dir, "Exis_meta.json")
        self.lock = threading.RLock()
        os.makedirs(self.log_dir, exist_ok=True)

        # 任务数据（内存）
        self._tasks: Dict[str, Any] = self._load_from_disk()
        self._dirty = False
        self._flush_timer: "threading.Timer | None" = None

        # Exis（已下载视频索引）
        self._exis_index: set = set()                       # filename 集合，O(1) 查重
        self._exis_by_dir: Dict[str, Dict[str, str]] = {}   # dir -> {filename: fullpath}
        self._exis_dir_mtime: Dict[str, float] = {}         # dir -> mtime
        self._load_exis_index()

    # ------------------------------------------------------------------ #
    # 任务持久化（内存 + 合并落盘）
    # ------------------------------------------------------------------ #
    def _load_from_disk(self) -> Dict[str, Any]:
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _schedule_flush(self) -> None:
        self._dirty = True
        if self._flush_timer is None or not self._flush_timer.is_alive():
            self._flush_timer = threading.Timer(_FLUSH_INTERVAL, self._flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _flush(self) -> None:
        with self.lock:
            if not self._dirty:
                return
            try:
                tmp = self.config_file + ".tmp"
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(self._tasks, f, indent=4, ensure_ascii=False)
                os.replace(tmp, self.config_file)
                self._dirty = False
            except Exception as e:
                logger.error(f"保存任务日志失败: {e}")

    def flush_now(self) -> None:
        """立即落盘（程序退出前调用）。"""
        if self._flush_timer is not None:
            self._flush_timer.cancel()
        self._flush()

    # ------------------------------------------------------------------ #
    # 任务写操作
    # ------------------------------------------------------------------ #
    def add_task(self, task_id: str, url: str, download_dir: str) -> None:
        with self.lock:
            self._tasks[task_id] = {
                "url": url,
                "download_dir": download_dir,
                "status": "waiting",
                "video_links": [],
                "downloaded_videos": [],
                "failed_links": [],
                "failure_type": "",
                "total_video_count": 0,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            self._schedule_flush()

    def update_task_status(self, task_id: str, status: str) -> None:
        with self.lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = status
                self._tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._schedule_flush()

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> None:
        with self.lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(updates)
                self._tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._schedule_flush()

    def add_video_links(self, task_id: str, video_links: List[str]) -> None:
        with self.lock:
            if task_id in self._tasks:
                self._tasks[task_id]["video_links"] = video_links
                self._tasks[task_id]["total_video_count"] = len(video_links)
                self._tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._schedule_flush()

    def add_downloaded_video(self, task_id: str, video_filename: str, video_url: str = None) -> None:
        """添加已下载视频记录，并从 video_links / failed_links 中移除对应链接。"""
        with self.lock:
            if task_id not in self._tasks:
                return
            task = self._tasks[task_id]
            if "downloaded_videos" not in task:
                task["downloaded_videos"] = []

            if video_url:
                video_links = task.get("video_links", [])
                if video_url in video_links:
                    video_links.remove(video_url)
                    task["video_links"] = video_links
                    self.log(LogLevel.INFO, f"已从任务 {task_id} 的视频链接列表中移除：{video_url}")

                failed_links = task.get("failed_links", [])
                if video_url in failed_links:
                    failed_links.remove(video_url)
                    task["failed_links"] = failed_links
                    self.log(LogLevel.INFO, f"已从失败链接中移除：{video_url}")

            cleaned_filename = _SUB_MARKETING.sub('', video_filename)
            if cleaned_filename not in task["downloaded_videos"]:
                task["downloaded_videos"].append(cleaned_filename)

            task["updated_at"] = datetime.now().isoformat()
            self._schedule_flush()

    def add_failed_link(self, task_id: str, link: str, failure_type: str = "") -> None:
        with self.lock:
            if task_id not in self._tasks:
                return
            task = self._tasks[task_id]
            if "failed_links" not in task:
                task["failed_links"] = []
            if link not in task["failed_links"]:
                task["failed_links"].append(link)
                if failure_type:
                    task["failure_type"] = failure_type
                task["updated_at"] = datetime.now().isoformat()
                self._schedule_flush()

    def remove_failed_link(self, task_id: str, link: str) -> None:
        with self.lock:
            if task_id not in self._tasks:
                return
            failed_links = self._tasks[task_id].get("failed_links", [])
            if link in failed_links:
                failed_links.remove(link)
                self._tasks[task_id]["failed_links"] = failed_links
                self._tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._schedule_flush()
                self.log(LogLevel.INFO, f"已从失败链接中移除: {link}")

    def remove_video_link(self, task_id: str, link: str) -> None:
        with self.lock:
            if task_id not in self._tasks:
                return
            video_links = self._tasks[task_id].get("video_links", [])
            if link in video_links:
                video_links.remove(link)
                self._tasks[task_id]["video_links"] = video_links
                self._tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._schedule_flush()
                self.log(LogLevel.INFO, f"已从任务 {task_id} 的视频链接列表中移除: {link}")

    def clear_failed_links(self, task_id: str) -> None:
        with self.lock:
            if task_id in self._tasks:
                self._tasks[task_id]["failed_links"] = []
                self._tasks[task_id]["failure_type"] = ""
                self._tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._schedule_flush()

    def get_remaining_video_links(self, task_id: str) -> List[str]:
        with self.lock:
            if task_id not in self._tasks:
                return []
            task = self._tasks[task_id]
            video_links = set(task.get("video_links", []))
            failed_links = set(task.get("failed_links", []))
            downloaded_videos = set(task.get("downloaded_videos", []))

            remaining_links = list((video_links | failed_links) - downloaded_videos)

            self.log(LogLevel.DEBUG,
                     f"任务 {task_id[:8]} 剩余链接计算: "
                     f"video_links={len(video_links)}, "
                     f"failed_links={len(failed_links)}, "
                     f"downloaded={len(downloaded_videos)}, "
                     f"remaining={len(remaining_links)}")

            return remaining_links

    def get_task_failed_links(self, task_id: str) -> List[str]:
        with self.lock:
            if task_id in self._tasks:
                return list(self._tasks[task_id].get("failed_links", []))
            return []

    def remove_task(self, task_id: str) -> bool:
        with self.lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                self._schedule_flush()
                self.log(LogLevel.INFO, f"已从TaskLogger删除任务: {task_id}")
                return True
            else:
                self.log(LogLevel.WARNING, f"任务不存在，无法删除: {task_id}")
                return False

    def get_all_tasks(self) -> Dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self._tasks)

    def get_incomplete_tasks(self) -> Dict[str, Any]:
        with self.lock:
            return {
                tid: copy.deepcopy(info)
                for tid, info in self._tasks.items()
                if info["status"] in ["waiting", "running", "paused", "failed", "stopped"]
            }

    def get_waiting_and_running_tasks(self) -> Dict[str, Any]:
        with self.lock:
            return {
                tid: copy.deepcopy(info)
                for tid, info in self._tasks.items()
                if info["status"] in ["waiting", "running"]
            }

    def get_failed_tasks(self) -> Dict[str, Any]:
        with self.lock:
            return {
                tid: copy.deepcopy(info)
                for tid, info in self._tasks.items()
                if info["status"] == "failed"
            }

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
            self._tasks = {}
            self._dirty = True
            self._flush()

    def cleanup_completed_task(self, task_id: str) -> bool:
        with self.lock:
            if task_id not in self._tasks:
                self.log(LogLevel.WARNING, f"任务 {task_id} 不存在")
                return False
            task_info = self._tasks[task_id]
            if task_info["status"] == "completed":
                del self._tasks[task_id]
                self._schedule_flush()
                self.log(LogLevel.INFO, f"已清理已完成任务: {task_id}")
                return True
            else:
                self.log(LogLevel.WARNING, f"任务 {task_id} 状态为 {task_info['status']}，不能清理")
                return False

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def log(self, level: LogLevel, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.log(getattr(logging, level.value), f"{timestamp} - {message}")

    def get_plain_log_message(self, level: LogLevel, message: str) -> str:
        timestamp = datetime.now().strftime("%H:%M:%S")
        return f"[{timestamp}] {level.value} - {message}"

    # ------------------------------------------------------------------ #
    # Exis（存储目录已下载视频索引）
    # ------------------------------------------------------------------ #
    def _load_exis_index(self) -> None:
        try:
            with open(self.exis_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = []
        self._exis_by_dir = {}
        self._exis_index = set()
        for info in data:
            if isinstance(info, dict):
                fn = info.get("filename", "")
                p = info.get("path", "")
                d = os.path.dirname(p) if p else ""
                self._exis_by_dir.setdefault(d, {})[fn] = p
                self._exis_index.add(fn)
        try:
            with open(self.exis_meta_file, 'r', encoding='utf-8') as f:
                self._exis_dir_mtime = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._exis_dir_mtime = {}

    @staticmethod
    def _safe_mtime(path: str):
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None

    def update_exis_file(self, storage_dir: str) -> List[dict]:
        """扫描单个存储目录，返回 [{"filename":..., "path":...}, ...]（递归）。"""
        video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm'}
        video_files = []
        try:
            for root, dirs, files in os.walk(storage_dir):
                for filename in files:
                    _, ext = os.path.splitext(filename)
                    if ext.lower() in video_extensions:
                        cleaned_filename = _SUB_MARKETING.sub('', filename)
                        full_path = os.path.join(root, filename)
                        video_files.append({
                            "filename": cleaned_filename,
                            "path": full_path,
                        })
        except Exception as e:
            logger.error(f"扫描目录时出错: {e}")
        return video_files

    def batch_update_exis_file(self, storage_dirs: List[str], incremental: bool = True) -> int:
        """批量扫描多个存储目录，更新 Exis.json。

        增量模式：仅重新扫描 mtime 发生变化的目录，未变化的目录复用缓存，
        避免每次刷新都全量 os.walk（大库性能关键）。
        """
        scanned: Dict[str, Dict[str, str]] = {}

        for storage_dir in storage_dirs:
            mtime = self._safe_mtime(storage_dir)
            unchanged = (
                incremental
                and storage_dir in self._exis_dir_mtime
                and self._exis_dir_mtime.get(storage_dir) == mtime
                and storage_dir in self._exis_by_dir
            )
            if unchanged:
                # 复用缓存条目
                scanned[storage_dir] = dict(self._exis_by_dir[storage_dir])
                continue

            files = self.update_exis_file(storage_dir)
            scanned[storage_dir] = {info["filename"]: info["path"] for info in files}

        # 合并去重（按文件名），保留首次出现的路径
        merged: Dict[str, str] = {}
        for dmap in scanned.values():
            for fn, p in dmap.items():
                if fn not in merged:
                    merged[fn] = p

        unique_files = [{"filename": fn, "path": p} for fn, p in merged.items()]
        unique_files.sort(key=lambda x: x.get("filename", "").lower())

        old_count = len(self._exis_index)
        self._exis_by_dir = {d: dict(dmap) for d, dmap in scanned.items()}
        self._exis_index = set(merged.keys())
        self._exis_dir_mtime = {d: self._safe_mtime(d) for d in storage_dirs}

        try:
            with self.lock:
                with open(self.exis_file, 'w', encoding='utf-8') as f:
                    json.dump(unique_files, f, indent=4, ensure_ascii=False)
                with open(self.exis_meta_file, 'w', encoding='utf-8') as f:
                    json.dump(self._exis_dir_mtime, f, indent=4, ensure_ascii=False)

            if incremental:
                added_count = len(unique_files) - old_count
                logger.info(
                    f"已更新Exis.json，新增 {added_count} 个文件，共 {len(unique_files)} 个唯一视频文件（已按名称排序）"
                )
            else:
                logger.info(f"已更新Exis.json，共找到 {len(unique_files)} 个唯一视频文件（已按名称排序）")
        except Exception as e:
            logger.error(f"保存Exis.json时出错: {e}")
            return 0
        return len(unique_files)

    def clear_exis_file(self) -> None:
        try:
            with self.lock:
                with open(self.exis_file, 'w', encoding='utf-8') as f:
                    json.dump([], f, indent=4, ensure_ascii=False)
                self._exis_by_dir = {}
                self._exis_index = set()
                self._exis_dir_mtime = {}
                try:
                    if os.path.exists(self.exis_meta_file):
                        os.remove(self.exis_meta_file)
                except OSError:
                    pass
            logger.info("已清空Exis.json")
        except Exception as e:
            logger.error(f"清空Exis.json时出错: {e}")
            raise

    def is_video_exists(self, filename: str) -> bool:
        """检查视频文件是否已存在于 Exis 索引中（O(1) 查重）。"""
        try:
            if filename in self._exis_index:
                return True
            cleaned_filename = _SUB_MARKETING.sub('', filename)
            return cleaned_filename in self._exis_index
        except Exception as e:
            logger.error(f"检查视频是否存在时出错: {e}")
            return False
