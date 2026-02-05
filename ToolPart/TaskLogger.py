import json
import os
import threading
from datetime import datetime
from enum import Enum
from typing import Dict, List, Any


class LogLevel(Enum):
    """日志级别枚举"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


class TaskLogger:
    """任务队列临时文件管理器"""

    def __init__(self, log_dir: str = "Logger", config_file: str = "TaskLogger.json"):
        self.log_dir = log_dir
        self.config_file = os.path.join(log_dir, config_file)
        self.lock = threading.Lock()

        # 确保日志目录存在
        os.makedirs(self.log_dir, exist_ok=True)

        # 初始化日志文件
        self._init_log_file()
        
        # 标准日志输出，无颜色

    def _init_log_file(self) -> None:
        """初始化日志文件"""
        if not os.path.exists(self.config_file):
            with self.lock:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump({}, f, indent=4, ensure_ascii=False)

    def add_task(self, task_id: str, url: str, download_dir: str) -> None:
        """添加新任务"""
        with self.lock:
            # 读取现有任务
            tasks = self._load_tasks()

            # 创建任务信息
            task_info = {
                "url": url,
                "download_dir": download_dir,
                "status": "pending",  # pending, running, paused, failed, completed
                "video_links": [],  # 存储获取到的视频链接
                "downloaded_videos": [],  # 存储已下载的视频文件名
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }

            # 添加到任务列表
            tasks[task_id] = task_info

            # 保存到文件
            self._save_tasks(tasks)

    def update_task_status(self, task_id: str, status: str) -> None:
        """更新任务状态"""
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                tasks[task_id]["status"] = status
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

    def add_video_links(self, task_id: str, video_links: List[str]) -> None:
        """添加视频链接到任务"""
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                tasks[task_id]["video_links"] = video_links
                tasks[task_id]["updated_at"] = datetime.now().isoformat()
                self._save_tasks(tasks)

    def add_downloaded_video(self, task_id: str, video_filename: str) -> None:
        """添加已下载的视频文件"""
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                if "downloaded_videos" not in tasks[task_id]:
                    tasks[task_id]["downloaded_videos"] = []

                if video_filename not in tasks[task_id]["downloaded_videos"]:
                    tasks[task_id]["downloaded_videos"].append(video_filename)
                    tasks[task_id]["updated_at"] = datetime.now().isoformat()
                    self._save_tasks(tasks)

    def remove_task(self, task_id: str) -> bool:
        """删除任务，返回是否删除成功"""
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
        """获取所有任务"""
        with self.lock:
            return self._load_tasks()

    def get_incomplete_tasks(self) -> Dict[str, Any]:
        """获取未完成的任务（failed、paused或downloading状态）"""
        with self.lock:
            tasks = self._load_tasks()
            incomplete_tasks = {}

            for task_id, task_info in tasks.items():
                if task_info["status"] in ["failed", "paused", "downloading"]:
                    incomplete_tasks[task_id] = task_info

            return incomplete_tasks
    
    def get_downloading_files(self, download_dir: str) -> List[str]:
        """获取指定目录下所有'下载中_'前缀的文件"""
        downloading_files = []
        try:
            if os.path.exists(download_dir):
                files = os.listdir(download_dir)
                downloading_files = [f for f in files if f.startswith('下载中_') and f.endswith('.mp4')]
        except Exception as e:
            print(f"扫描下载目录时出错: {e}")
        
        return downloading_files

    def clear_all_tasks(self) -> None:
        """清空所有任务"""
        with self.lock:
            # 清空任务文件
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=4, ensure_ascii=False)

    def auto_cleanup_completed_tasks(self, days_old: int = 1) -> None:
        """自动清理已完成超过指定天数的任务"""
        with self.lock:
            tasks = self._load_tasks()
            current_time = datetime.now()
            
            tasks_to_remove = []
            
            for task_id, task_info in tasks.items():
                if task_info["status"] == "completed":
                    # 解析创建时间
                    created_at = datetime.fromisoformat(task_info["created_at"])
                    # 计算天数差
                    days_diff = (current_time - created_at).days
                    
                    if days_diff >= days_old:
                        tasks_to_remove.append(task_id)
            
            # 删除符合条件的任务
            for task_id in tasks_to_remove:
                del tasks[task_id]
                self.log(LogLevel.INFO, f"已自动清理任务: {task_id}")
            
            # 保存更新后的任务列表
            if tasks_to_remove:
                self._save_tasks(tasks)

    # 文件清理功能已移除，现在使用文件名前缀方式标识下载状态

    def _load_tasks(self) -> Dict[str, Any]:
        """加载任务数据"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_tasks(self, tasks: Dict[str, Any]) -> None:
        """保存任务数据"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(tasks, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.log(LogLevel.ERROR, f"保存任务日志失败: {e}")
    
    def cleanup_completed_task(self, task_id: str) -> bool:
        """清理单个已完成的任务"""
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                task_info = tasks[task_id]
                # 只删除已完成的任务
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
    
    def log(self, level: LogLevel, message: str) -> None:
        """记录结构化日志"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = {
            "timestamp": timestamp,
            "level": level.value,
            "message": message
        }
        
        # 控制台输出（不带颜色）
        print(f"[{timestamp}] {level.value} - {message}")
        
        # 文件日志记录
        try:
            log_file = os.path.join(self.log_dir, "task_log.txt")
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(f"[{log_entry['timestamp']}] {log_entry['level']} - {log_entry['message']}\n")
        except Exception as e:
            print(f"写入日志文件失败: {e}")
    
    def get_plain_log_message(self, level: LogLevel, message: str) -> str:
        """获取纯文本格式的日志消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        return f"[{timestamp}] {level.value} - {message}"
    
