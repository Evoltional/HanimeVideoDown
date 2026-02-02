import os
import time
import json
import hashlib
from typing import Optional, Dict, Any, List
from PyQt5.QtCore import pyqtSignal, QObject
from datetime import datetime


class LogEmitter(QObject):
    log_signal = pyqtSignal(str)  # type: ignore


class TaskLogger:
    """任务日志管理器，用于管理所有任务状态和视频进度"""

    def __init__(self, logger_dir: str = "./logger"):
        self.logger_dir = logger_dir
        self.pending_tasks_file = os.path.join(logger_dir, "pending_tasks.json")
        os.makedirs(logger_dir, exist_ok=True)

        # 视频进度跟踪
        self.video_progress = {}  # {video_id: progress}
        self.video_status = {}  # {video_id: status}
        self.video_titles = {}  # {video_id: title}

    def _generate_video_id(self, video_url: str) -> str:
        """生成视频ID"""
        return hashlib.md5(video_url.encode()).hexdigest()[:8]

    def log_task_start(self, task_id: str, url: str, download_dir: str,
                       task_type: str = "playlist", retry_count: int = 0) -> None:
        """记录任务开始"""
        try:
            tasks = self._load_tasks()
            tasks[task_id] = {
                "task_id": task_id,
                "url": url,
                "download_dir": download_dir,
                "task_type": task_type,
                "status": "running",  # running, paused, completed, failed
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "video_tasks": {},  # 存储每个视频任务的状态
                "failed_videos": [],  # 存储失败视频的URL
                "completed_videos": [],  # 存储成功下载的视频URL
                "total_videos": 0,
                "current_progress": 0,
                "retry_count": retry_count,
                "last_error": None,
                "is_retry": False  # 标记是否为重试任务
            }
            self._save_tasks(tasks)
        except Exception as e:
            print(f"记录任务开始失败: {str(e)}")

    def log_video_task_start(self, task_id: str, video_url: str, video_title: str = None, video_id: str = None) -> None:
        """记录视频任务开始"""
        try:
            if video_id is None:
                video_id = self._generate_video_id(video_url)

            tasks = self._load_tasks()
            if task_id in tasks:
                tasks[task_id]["video_tasks"][video_id] = {
                    "url": video_url,
                    "title": video_title or video_url,
                    "status": "running",  # waiting, running, completed, failed
                    "start_time": datetime.now().isoformat(),
                    "end_time": None,
                    "error": None,
                    "retry_count": 0,
                    "progress": 0
                }
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

                # 更新视频进度和状态
                self.video_progress[video_id] = 0
                self.video_status[video_id] = "running"
                if video_title:
                    self.video_titles[video_id] = video_title
        except Exception as e:
            print(f"记录视频任务开始失败: {str(e)}")

    def log_video_task_progress(self, task_id: str, video_url: str, progress: float, video_id: str = None) -> None:
        """记录视频下载进度"""
        try:
            if video_id is None:
                video_id = self._generate_video_id(video_url)

            tasks = self._load_tasks()
            if task_id in tasks and video_id in tasks[task_id]["video_tasks"]:
                tasks[task_id]["video_tasks"][video_id]["progress"] = progress
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

                # 更新视频进度
                self.video_progress[video_id] = progress
        except Exception as e:
            print(f"记录视频进度失败: {str(e)}")

    def log_video_task_complete(self, task_id: str, video_url: str, video_title: str = None,
                                video_id: str = None) -> None:
        """记录视频任务完成"""
        try:
            if video_id is None:
                video_id = self._generate_video_id(video_url)

            tasks = self._load_tasks()
            if task_id in tasks:
                # 更新视频任务状态
                if video_id in tasks[task_id]["video_tasks"]:
                    tasks[task_id]["video_tasks"][video_id]["status"] = "completed"
                    tasks[task_id]["video_tasks"][video_id]["end_time"] = datetime.now().isoformat()
                    tasks[task_id]["video_tasks"][video_id]["progress"] = 100
                    if video_title:
                        tasks[task_id]["video_tasks"][video_id]["title"] = video_title

                # 添加到完成列表
                if video_url not in tasks[task_id]["completed_videos"]:
                    tasks[task_id]["completed_videos"].append(video_url)

                # 从失败列表中移除（如果存在）
                if video_url in tasks[task_id]["failed_videos"]:
                    tasks[task_id]["failed_videos"].remove(video_url)

                # 更新任务进度
                total = tasks[task_id]["total_videos"]
                completed = len(tasks[task_id]["completed_videos"])
                if total > 0:
                    tasks[task_id]["current_progress"] = int((completed / total) * 100)

                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

                # 更新视频状态
                self.video_status[video_id] = "completed"
                self.video_progress[video_id] = 100
                if video_title:
                    self.video_titles[video_id] = video_title
        except Exception as e:
            print(f"记录视频任务完成失败: {str(e)}")

    def log_video_task_failed(self, task_id: str, video_url: str, error: str = "", video_title: str = None,
                              video_id: str = None) -> None:
        """记录视频任务失败"""
        try:
            if video_id is None:
                video_id = self._generate_video_id(video_url)

            tasks = self._load_tasks()
            if task_id in tasks:
                # 更新视频任务状态
                if video_id in tasks[task_id]["video_tasks"]:
                    tasks[task_id]["video_tasks"][video_id]["status"] = "failed"
                    tasks[task_id]["video_tasks"][video_id]["end_time"] = datetime.now().isoformat()
                    tasks[task_id]["video_tasks"][video_id]["error"] = error
                    tasks[task_id]["video_tasks"][video_id]["retry_count"] += 1
                    if video_title:
                        tasks[task_id]["video_tasks"][video_id]["title"] = video_title

                # 添加到失败列表
                if video_url not in tasks[task_id]["failed_videos"]:
                    tasks[task_id]["failed_videos"].append(video_url)

                # 从完成列表中移除（如果存在）
                if video_url in tasks[task_id]["completed_videos"]:
                    tasks[task_id]["completed_videos"].remove(video_url)

                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

                # 更新视频状态
                self.video_status[video_id] = "failed"
                self.video_progress[video_id] = 0
                if video_title:
                    self.video_titles[video_id] = video_title
        except Exception as e:
            print(f"记录视频任务失败失败: {str(e)}")

    def update_task_total_videos(self, task_id: str, total_videos: int) -> None:
        """更新任务总视频数"""
        try:
            tasks = self._load_tasks()
            if task_id in tasks:
                tasks[task_id]["total_videos"] = total_videos
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)
        except Exception as e:
            print(f"更新任务总视频数失败: {str(e)}")

    def update_task_status(self, task_id: str, status: str) -> None:
        """更新任务状态"""
        try:
            tasks = self._load_tasks()
            if task_id in tasks:
                old_status = tasks[task_id]["status"]
                tasks[task_id]["status"] = status
                tasks[task_id]["updated_at"] = datetime.now().isoformat()

                # 如果任务完成且没有失败视频，则删除任务记录
                if status == "completed" and not tasks[task_id]["failed_videos"]:
                    self._remove_task_completely(tasks, task_id)
                else:
                    self._save_tasks(tasks)

                # 如果是任务从失败状态变为运行中，清空失败状态（准备重试）
                if old_status == "failed" and status == "running":
                    self._clear_failed_state(tasks, task_id)
        except Exception as e:
            print(f"更新任务状态失败: {str(e)}")

    def mark_task_failed(self, task_id: str, error: str = "") -> None:
        """标记任务失败"""
        try:
            tasks = self._load_tasks()
            if task_id in tasks:
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["last_error"] = error
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)
        except Exception as e:
            print(f"标记任务失败失败: {str(e)}")

    def get_all_tasks(self) -> Dict[str, Any]:
        """获取所有任务"""
        try:
            return self._load_tasks()
        except Exception as e:
            print(f"获取所有任务失败: {str(e)}")
            return {}

    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """获取所有待处理任务（状态为 running, paused, failed）"""
        try:
            tasks = self._load_tasks()
            pending_tasks = []

            for task_id, task_info in tasks.items():
                if task_info["status"] in ["running", "paused", "failed"]:
                    pending_tasks.append({
                        "task_id": task_id,
                        **task_info
                    })

            return pending_tasks
        except Exception as e:
            print(f"获取待处理任务失败: {str(e)}")
            return []

    def get_failed_tasks(self) -> List[Dict[str, Any]]:
        """获取所有失败的任务"""
        try:
            tasks = self._load_tasks()
            failed_tasks = []

            for task_id, task_info in tasks.items():
                if task_info["status"] == "failed":
                    failed_tasks.append({
                        "task_id": task_id,
                        **task_info
                    })

            return failed_tasks
        except Exception as e:
            print(f"获取失败任务失败: {str(e)}")
            return []

    def get_task_info(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取特定任务信息"""
        try:
            tasks = self._load_tasks()
            if task_id in tasks:
                return {
                    "task_id": task_id,
                    **tasks[task_id]
                }
            return None
        except Exception:
            return None

    def remove_task(self, task_id: str) -> None:
        """移除任务记录 - 修复：完全删除任务记录"""
        try:
            tasks = self._load_tasks()
            if task_id in tasks:
                # 完全删除任务记录
                self._remove_task_completely(tasks, task_id)
        except Exception as e:
            print(f"移除任务失败: {str(e)}")

    def remove_all_tasks(self) -> None:
        """移除所有任务记录"""
        try:
            # 清空视频进度数据
            self.video_progress.clear()
            self.video_status.clear()
            self.video_titles.clear()

            # 清空任务文件
            tasks = {}
            self._save_tasks(tasks)
        except Exception as e:
            print(f"移除所有任务失败: {str(e)}")

    def reset_task_for_retry(self, task_id: str) -> Dict[str, Any]:
        """重置任务状态用于重试"""
        try:
            tasks = self._load_tasks()
            if task_id in tasks:
                # 清空失败状态，准备重试
                tasks[task_id]["failed_videos"] = []
                tasks[task_id]["completed_videos"] = []
                tasks[task_id]["video_tasks"] = {}
                tasks[task_id]["current_progress"] = 0
                tasks[task_id]["retry_count"] = tasks[task_id].get("retry_count", 0) + 1
                tasks[task_id]["is_retry"] = True
                tasks[task_id]["status"] = "paused"  # 设置为暂停状态，等待用户继续
                tasks[task_id]["updated_at"] = datetime.now().isoformat()

                self._save_tasks(tasks)
                return tasks[task_id]
            return {}
        except Exception as e:
            print(f"重置任务状态失败: {str(e)}")
            return {}

    def get_video_info_by_id(self, task_id: str, video_id: str) -> Optional[Dict[str, Any]]:
        """通过视频ID获取视频信息"""
        try:
            tasks = self._load_tasks()
            if task_id in tasks and video_id in tasks[task_id]["video_tasks"]:
                return tasks[task_id]["video_tasks"][video_id]
            return None
        except Exception:
            return None

    def get_video_info_by_url(self, task_id: str, video_url: str) -> Optional[Dict[str, Any]]:
        """通过视频URL获取视频信息"""
        try:
            video_id = self._generate_video_id(video_url)
            return self.get_video_info_by_id(task_id, video_id)
        except Exception:
            return None

    def get_video_progress(self, video_url: str) -> float:
        """获取视频下载进度"""
        try:
            video_id = self._generate_video_id(video_url)
            return self.video_progress.get(video_id, 0)
        except Exception:
            return 0

    def get_video_status(self, video_url: str) -> str:
        """获取视频状态"""
        try:
            video_id = self._generate_video_id(video_url)
            return self.video_status.get(video_id, "unknown")
        except Exception:
            return "unknown"

    def get_video_title(self, video_url: str) -> str:
        """获取视频标题"""
        try:
            video_id = self._generate_video_id(video_url)
            if video_id in self.video_titles:
                return self.video_titles[video_id]

            # 如果内存中没有，尝试从任务记录中查找
            tasks = self._load_tasks()
            for task_id, task_info in tasks.items():
                for vid, video_info in task_info.get("video_tasks", {}).items():
                    if vid == video_id and "title" in video_info:
                        self.video_titles[vid] = video_info["title"]
                        return video_info["title"]

            return video_url  # 默认返回URL
        except Exception:
            return video_url

    def _load_tasks(self) -> Dict[str, Any]:
        """加载任务文件"""
        try:
            if os.path.exists(self.pending_tasks_file):
                with open(self.pending_tasks_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        tasks = json.loads(content)

                        # 恢复视频进度数据
                        self._restore_video_progress(tasks)

                        return tasks
            return {}
        except Exception as e:
            print(f"加载任务文件失败: {str(e)}")
            return {}

    def _restore_video_progress(self, tasks: Dict[str, Any]) -> None:
        """从任务数据恢复视频进度"""
        try:
            for task_id, task_info in tasks.items():
                for video_id, video_info in task_info.get("video_tasks", {}).items():
                    self.video_progress[video_id] = video_info.get("progress", 0)
                    self.video_status[video_id] = video_info.get("status", "unknown")
                    if "title" in video_info:
                        self.video_titles[video_id] = video_info["title"]
        except Exception as e:
            print(f"恢复视频进度失败: {str(e)}")

    def _save_tasks(self, tasks: Dict[str, Any]) -> None:
        """保存任务文件"""
        try:
            with open(self.pending_tasks_file, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存任务文件失败: {str(e)}")

    def _remove_task_completely(self, tasks: Dict[str, Any], task_id: str) -> None:
        """完全删除任务"""
        try:
            if task_id in tasks:
                # 清理该任务的视频进度数据
                for video_id in list(self.video_progress.keys()):
                    video_info = self.get_video_info_by_id(task_id, video_id)
                    if video_info:
                        del self.video_progress[video_id]
                        del self.video_status[video_id]
                        if video_id in self.video_titles:
                            del self.video_titles[video_id]

                # 删除任务
                del tasks[task_id]
                self._save_tasks(tasks)
        except Exception as e:
            print(f"完全删除任务失败: {str(e)}")

    def _clear_failed_state(self, tasks: Dict[str, Any], task_id: str) -> None:
        """清空失败状态"""
        try:
            if task_id in tasks:
                tasks[task_id]["failed_videos"] = []
                tasks[task_id]["last_error"] = None
                tasks[task_id]["is_retry"] = True
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)
        except Exception as e:
            print(f"清空失败状态失败: {str(e)}")


def log_failure(logger_dir: str, filename: str, url: str, error: str = "") -> Optional[str]:
    """记录下载失败到日志文件"""
    try:
        # 创建日志文件路径（按日期）
        log_file = os.path.join(logger_dir, f"{time.strftime('%Y-%m-%d')}.log")
        # 日志内容
        log_content = (
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"下载失败: 文件名: {filename}, "
            f"URL: {url}, "
            f"错误: {error}\n"
        )
        # 写入日志文件（追加模式）
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_content)
    except Exception as e:
        return f"记录失败日志时出错: {str(e)}"
    return None