import json
import os
import threading
import time
from datetime import datetime
from typing import Dict, List, Any


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

    def remove_task(self, task_id: str) -> None:
        """删除任务"""
        with self.lock:
            tasks = self._load_tasks()
            if task_id in tasks:
                # 如果任务未完成，删除未完成的视频文件
                if tasks[task_id]["status"] not in ["completed"]:
                    self._cleanup_incomplete_videos(tasks[task_id])

                del tasks[task_id]
                self._save_tasks(tasks)

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
            tasks = self._load_tasks()

            # 清理所有未完成任务的视频文件
            for task_id, task_info in tasks.items():
                if task_info["status"] not in ["completed"]:
                    self._cleanup_incomplete_videos(task_info)

            # 清空任务文件
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=4, ensure_ascii=False)

    def cleanup_on_exit(self) -> None:
        """程序退出时清理未完成下载的视频文件"""
        print("开始执行退出清理...")
        with self.lock:
            tasks = self._load_tasks()
            print(f"找到 {len(tasks)} 个任务记录")

            # 清理每个未完成任务的文件
            for task_id, task_info in tasks.items():
                if task_info["status"] not in ["completed"]:
                    print(f"清理任务 {task_id} 的未完成文件...")
                    self._cleanup_incomplete_videos(task_info)
            
            # 额外检查常见的下载目录
            self._cleanup_common_download_dirs()
            
            print("退出清理完成")
    
    def _cleanup_common_download_dirs(self):
        """清理常见的下载目录中的未完成文件"""
        print("检查常见下载目录...")
        common_dirs = [
            "./Download",
            "./downloads", 
            "E:/Temp/NEW",
            os.path.join(os.getcwd(), "Download"),
            os.path.join(os.getcwd(), "downloads")
        ]
        
        for dir_path in common_dirs:
            if os.path.exists(dir_path):
                print(f"检查目录: {dir_path}")
                try:
                    files = os.listdir(dir_path)
                    mp4_files = [f for f in files if f.endswith('.mp4') and os.path.isfile(os.path.join(dir_path, f))]
                    
                    for filename in mp4_files:
                        file_path = os.path.join(dir_path, filename)
                        file_size = os.path.getsize(file_path)
                        file_mtime = os.path.getmtime(file_path)
                        current_time = time.time()
                        
                        # 删除条件：小于10MB或超过1小时未修改
                        if file_size < 10 * 1024 * 1024 or (current_time - file_mtime) > 3600:
                            try:
                                os.remove(file_path)
                                print(f"  已删除未完成文件: {filename} ({file_size} bytes)")
                            except PermissionError:
                                print(f"  文件被占用，跳过: {filename}")
                            except Exception as e:
                                print(f"  删除文件失败 {filename}: {e}")
                except Exception as e:
                    print(f"检查目录 {dir_path} 时出错: {e}")

    def _cleanup_incomplete_videos(self, task_info: Dict[str, Any]) -> None:
        """清理未完成任务的视频文件"""
        try:
            download_dir = task_info.get("download_dir", "./Download")
            downloaded_videos = task_info.get("downloaded_videos", [])
            video_links = task_info.get("video_links", [])
            
            print(f"清理目录 {download_dir} 中的未完成文件")
            
            if not os.path.exists(download_dir):
                print(f"下载目录不存在: {download_dir}")
                return
                
            # 删除已下载但未完成的视频文件
            for video_filename in downloaded_videos:
                file_path = os.path.join(download_dir, video_filename)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        print(f"已删除未完成视频文件: {file_path}")
                    except PermissionError:
                        print(f"文件被占用，无法删除: {file_path}")
                    except Exception as e:
                        print(f"删除文件 {file_path} 失败: {e}")
                else:
                    print(f"文件不存在: {file_path}")
                        
            # 删除下载中的临时文件
            downloading_files = self.get_downloading_files(download_dir)
            for filename in downloading_files:
                file_path = os.path.join(download_dir, filename)
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        print(f"已删除下载中文件: {file_path}")
                    else:
                        print(f"下载中文件不存在: {file_path}")
                except PermissionError:
                    print(f"下载中文件被占用，无法删除: {file_path}")
                except Exception as e:
                    print(f"删除下载中文件 {file_path} 失败: {e}")
                    
        except Exception as e:
            print(f"清理未完成视频文件时出错: {e}")

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
            print(f"保存任务日志失败: {e}")