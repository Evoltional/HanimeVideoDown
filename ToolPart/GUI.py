import configparser
import os
import time
import uuid
from typing import List, Dict, Any
from collections import defaultdict

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QTextEdit, QGroupBox,
                             QScrollArea, QFrame, QFileDialog, QMessageBox, QCheckBox,
                             QProgressBar, QGridLayout, QSizePolicy)

from ToolPart.DownloadThread import VideoDownloadThread
from ToolPart.Logger import LogEmitter, TaskLogger


def update_task_status(task_frame: QFrame, status: str, color: str) -> None:
    """更新任务状态显示"""
    status_label = task_frame.findChild(QLabel, "status_label")
    if status_label:
        status_label.setText(f"状态: {status}")
        status_label.setStyleSheet(f"color: {color};")

    # 更新按钮状态
    pause_btn = task_frame.findChild(QPushButton, "pause_btn")
    resume_btn = task_frame.findChild(QPushButton, "resume_btn")

    if status == "运行中":
        if pause_btn:
            pause_btn.setEnabled(True)
        if resume_btn:
            resume_btn.setEnabled(False)
    elif status == "已暂停":
        if pause_btn:
            pause_btn.setEnabled(False)
        if resume_btn:
            resume_btn.setEnabled(True)
    else:  # 等待中或队列中
        if pause_btn:
            pause_btn.setEnabled(False)
        if resume_btn:
            resume_btn.setEnabled(False)


class VideoProgressWidget(QWidget):
    """单个视频进度显示组件"""

    def __init__(self, task_id: str, video_title: str, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.video_title = video_title
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        # 视频标题
        self.title_label = QLabel(self.video_title)
        self.title_label.setStyleSheet("color: #ecf0f1; font-size: 12px;")
        self.title_label.setMaximumWidth(400)
        self.title_label.setWordWrap(True)
        self.title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.title_label, 3)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #3498db;
                border-radius: 5px;
                background-color: #2c3e50;
                text-align: center;
                color: #ecf0f1;
                font-size: 11px;
            }
            QProgressBar::chunk {
                background-color: #3498db;
                border-radius: 5px;
            }
        """)
        layout.addWidget(self.progress_bar, 2)

        # 状态标签
        self.status_label = QLabel("等待中")
        self.status_label.setStyleSheet("color: #95a5a6; font-size: 11px;")
        self.status_label.setFixedWidth(80)
        layout.addWidget(self.status_label, 1)

    def update_progress(self, progress: float, status: str = None):
        """更新进度"""
        self.progress_bar.setValue(int(progress))
        if status:
            self.status_label.setText(status)
            if status == "下载中":
                self.status_label.setStyleSheet("color: #2ecc71; font-size: 11px;")
            elif status == "已完成":
                self.status_label.setStyleSheet("color: #3498db; font-size: 11px;")
            elif status == "失败":
                self.status_label.setStyleSheet("color: #e74c3c; font-size: 11px;")
            elif status == "等待中":
                self.status_label.setStyleSheet("color: #95a5a6; font-size: 11px;")

    def set_title(self, title: str):
        """设置视频标题"""
        if len(title) > 40:
            title = title[:37] + "..."
        self.title_label.setText(title)
        self.video_title = title


class HanimeDownloaderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.status_bar = None
        self.tasks_layout = None
        self.tasks_container = None
        self.log_area = None
        self.tasks_scroll = None
        self.url_input = None
        self.delete_all_btn = None
        self.resume_all_btn = None
        self.pause_all_btn = None
        self.download_btn = None
        self.download_path_label = None
        self.headless_checkbox = None
        self.setWindowTitle("Hanime视频下载器")
        self.setGeometry(100, 100, 1000, 1200)  # 增大窗口高度
        self.setStyleSheet("""
            QMainWindow {
                background-color: #2c3e50;
            }
            QGroupBox {
                background-color: #34495e;
                border: 2px solid #3498db;
                border-radius: 10px;
                margin-top: 1ex;
                color: white;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top center;
                padding: 0 5px;
            }
            QLabel {
                color: #ecf0f1;
            }
            QLineEdit {
                background-color: #2c3e50;
                color: #ecf0f1;
                border: 1px solid #3498db;
                border-radius: 5px;
                padding: 5px;
                min-height: 40px;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #1c6ea4;
            }
            QPushButton:disabled {
                background-color: #7f8c8d;
            }
            QTextEdit {
                background-color: #2c3e50;
                color: #ecf0f1;
                border: 1px solid #3498db;
                border-radius: 5px;
                font-family: Consolas, Courier New;
                font-size: 11px;
            }
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QFrame {
                background-color: #34495e;
                border-radius: 5px;
                padding: 10px;
                border: 1px solid #3498db;
            }
            #tasks_container {
                background-color: #2c3e50;
            }
            QCheckBox {
                color: #ecf0f1;
                spacing: 5px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
            QCheckBox::indicator:unchecked {
                border: 2px solid #7f8c8d;
                border-radius: 3px;
                background-color: #2c3e50;
            }
            QCheckBox::indicator:checked {
                border: 2px solid #3498db;
                border-radius: 3px;
                background-color: #3498db;
            }
        """)

        self.active_threads: List[VideoDownloadThread] = []
        self.pending_tasks: List[Dict[str, Any]] = []
        self.log_emitter = LogEmitter()
        self.task_logger = TaskLogger()
        self.max_concurrent_tasks = 2

        # 视频进度管理
        self.video_progress_widgets = {}  # {video_url: VideoProgressWidget}
        self.task_videos = defaultdict(list)  # {task_id: [video_urls]}

        # 视频进度更新定时器
        self.progress_timer = QTimer()
        self.progress_timer.timeout.connect(self.update_video_progresses)
        self.progress_timer.start(1000)  # 每秒更新一次

        # 加载配置文件
        self.config = configparser.ConfigParser()
        self.config_file = "./config.ini"
        self.download_dir = self.load_config()
        self.headless_mode = self.load_headless_config()

        self.init_ui()
        self.restore_pending_tasks()

        # 在 init_ui() 之后连接信号
        self.log_emitter.log_signal.connect(self.log_message)  # type: ignore

    def load_config(self) -> str:
        """加载配置文件，返回下载路径"""
        default_dir = os.path.join(os.getcwd(), "downloads")

        if os.path.exists(self.config_file):
            self.config.read(self.config_file, encoding='utf-8')
            return self.config.get('Settings', 'DownloadDir', fallback=default_dir)

        # 如果配置文件不存在，创建默认配置
        os.makedirs(default_dir, exist_ok=True)
        return default_dir

    def load_headless_config(self) -> bool:
        """加载无头模式配置"""
        if os.path.exists(self.config_file):
            self.config.read(self.config_file, encoding='utf-8')
            return self.config.getboolean('Settings', 'HeadlessMode', fallback=True)
        return True

    def save_config(self) -> None:
        """保存配置到文件"""
        self.config['Settings'] = {
            'DownloadDir': self.download_dir,
            'HeadlessMode': str(self.headless_mode)
        }
        with open(self.config_file, 'w', encoding='utf-8') as configfile:
            self.config.write(configfile)

    def update_video_progresses(self):
        """定时更新视频进度"""
        try:
            for task_id, videos in self.task_videos.items():
                task_info = self.task_logger.get_task_info(task_id)
                if task_info:
                    video_tasks = task_info.get("video_tasks", {})
                    for video_url in videos:
                        # 生成视频ID
                        import hashlib
                        video_id = hashlib.md5(video_url.encode()).hexdigest()[:8]

                        if video_id in video_tasks:
                            video_info = video_tasks[video_id]
                            status = video_info.get("status", "waiting")
                            progress = 0

                            if status == "running":
                                progress = 50  # 下载中
                            elif status == "completed":
                                progress = 100
                            elif status == "failed":
                                progress = 0

                            if video_url in self.video_progress_widgets:
                                widget = self.video_progress_widgets[video_url]

                                # 更新状态文本
                                status_text = {
                                    "waiting": "等待中",
                                    "running": "下载中",
                                    "completed": "已完成",
                                    "failed": "失败"
                                }.get(status, "未知")

                                widget.update_progress(progress, status_text)
        except Exception as e:
            print(f"更新视频进度时出错: {str(e)}")

    def add_video_progress(self, task_id: str, video_url: str, video_title: str = None):
        """添加视频进度显示"""
        if video_url in self.video_progress_widgets:
            return

        # 如果没有提供标题，使用URL的最后一部分
        if not video_title:
            import urllib.parse
            parsed = urllib.parse.urlparse(video_url)
            video_title = parsed.path.split('/')[-1]
            if not video_title or len(video_title) < 5:
                video_title = f"视频{len(self.video_progress_widgets) + 1}"

        # 清理标题
        import re
        video_title = re.sub(r'[\\/*?:"<>|]', '_', video_title)
        if len(video_title) > 40:
            video_title = video_title[:37] + "..."

        # 创建进度组件
        widget = VideoProgressWidget(task_id, video_title)
        self.video_progress_container.layout().addWidget(widget)
        self.video_progress_widgets[video_url] = widget

        # 添加到任务视频列表
        self.task_videos[task_id].append(video_url)

        # 调整容器高度
        self.update_progress_container_height()

    def remove_video_progress(self, video_url: str):
        """移除视频进度显示"""
        if video_url in self.video_progress_widgets:
            widget = self.video_progress_widgets[video_url]
            self.video_progress_container.layout().removeWidget(widget)
            widget.deleteLater()
            del self.video_progress_widgets[video_url]

            # 从任务视频列表中移除
            for task_id in list(self.task_videos.keys()):
                if video_url in self.task_videos[task_id]:
                    self.task_videos[task_id].remove(video_url)
                    if not self.task_videos[task_id]:
                        del self.task_videos[task_id]

            # 调整容器高度
            self.update_progress_container_height()

    def update_progress_container_height(self):
        """更新进度容器高度"""
        count = len(self.video_progress_widgets)
        if count == 0:
            self.video_progress_container.setMaximumHeight(0)
        else:
            # 每个进度条大约50像素高
            self.video_progress_container.setMaximumHeight(min(count * 50, 300))

    def clear_all_video_progress(self):
        """清除所有视频进度显示"""
        for video_url in list(self.video_progress_widgets.keys()):
            self.remove_video_progress(video_url)

    def restore_pending_tasks(self) -> None:
        """恢复未完成的任务"""
        try:
            # 获取所有待处理任务
            pending_tasks = self.task_logger.get_pending_tasks()
            self.log_message(f"找到 {len(pending_tasks)} 个未完成的任务")

            for task_info in pending_tasks:
                task_id = task_info["task_id"]
                url = task_info["url"]
                download_dir = task_info.get("download_dir", self.download_dir)
                status = task_info.get("status", "pending")
                task_type = task_info.get("task_type", "playlist")

                self.log_message(f"恢复任务: {url} (状态: {status}, 类型: {task_type})")

                # 创建任务显示框
                task_frame = self.create_task_frame(task_id, url, task_type, status)

                # 恢复视频进度显示
                completed_videos = task_info.get("completed_videos", [])
                failed_videos = task_info.get("failed_videos", [])
                video_tasks = task_info.get("video_tasks", {})

                for video_url in completed_videos:
                    self.add_video_progress(task_id, video_url, "已完成")
                for video_url in failed_videos:
                    self.add_video_progress(task_id, video_url, "失败")

                # 根据状态设置颜色和按钮状态
                if status == "paused":
                    update_task_status(task_frame, "已暂停", "#f39c12")
                    # 添加到队列但不立即启动
                    self.pending_tasks.append({
                        "url": url,
                        "frame": task_frame,
                        "task_id": task_id,
                        "task_type": task_type,
                        "status": "paused",
                        "is_retry": task_info.get("is_retry", False)
                    })
                elif status == "failed":
                    update_task_status(task_frame, "失败", "#e74c3c")
                    # 失败任务添加到队列末尾，状态为paused
                    self.pending_tasks.append({
                        "url": url,
                        "frame": task_frame,
                        "task_id": task_id,
                        "task_type": task_type,
                        "status": "paused",
                        "is_retry": True
                    })
                else:  # running or pending
                    color = "#2ecc71" if status == "running" else "#f39c12"
                    update_task_status(task_frame, "等待中" if status == "pending" else "运行中", color)
                    # 添加到等待队列
                    self.pending_tasks.append({
                        "url": url,
                        "frame": task_frame,
                        "task_id": task_id,
                        "task_type": task_type,
                        "status": "pending"
                    })

                self.log_message(f"任务已添加到队列: {url}")

            if self.pending_tasks:
                self.delete_all_btn.setEnabled(True)
                self.update_queue_status()

                # 启动队列中的任务
                self.start_next_task()

        except Exception as e:
            self.log_message(f"恢复任务失败: {str(e)}")

    def create_task_frame(self, task_id: str, url: str, task_type: str, status: str) -> QFrame:
        """创建任务显示框"""
        task_frame = QFrame()
        task_frame.setFrameShape(QFrame.StyledPanel)
        task_frame.setObjectName(task_id)
        task_frame.url = url
        task_layout = QVBoxLayout(task_frame)

        # 显示任务类型
        task_type_text = "播放列表" if task_type == "playlist" else "单视频"
        if status == "failed":
            task_label_text = f"[失败]{task_type_text}: {url}"
        else:
            task_label_text = f"{task_type_text}: {url}"

        task_label = QLabel(task_label_text)
        task_label.setStyleSheet("color: #ecf0f1; font-weight: bold;")
        task_label.setWordWrap(True)
        task_layout.addWidget(task_label)

        status_text = "失败" if status == "failed" else status
        status_label = QLabel(f"状态: {status_text}")
        status_label.setObjectName("status_label")
        task_layout.addWidget(status_label)

        # 显示进度信息（如果有）
        total_videos = 0
        completed_videos = 0
        failed_videos = 0

        task_info = self.task_logger.get_task_info(task_id)
        if task_info:
            total_videos = task_info.get("total_videos", 0)
            completed_videos = len(task_info.get("completed_videos", []))
            failed_videos = len(task_info.get("failed_videos", []))

        if total_videos > 0:
            progress_text = f"进度: {completed_videos}/{total_videos}"
            if failed_videos > 0:
                progress_text += f" (失败: {failed_videos})"

            progress_label = QLabel(progress_text)
            progress_label.setStyleSheet("color: #95a5a6;")
            task_layout.addWidget(progress_label)

        # 按钮布局
        button_layout = QHBoxLayout()

        pause_btn = QPushButton("暂停")
        pause_btn.setObjectName("pause_btn")
        pause_btn.clicked.connect(lambda: self.pause_task(task_frame))
        button_layout.addWidget(pause_btn)

        resume_btn = QPushButton("继续")
        resume_btn.setObjectName("resume_btn")
        resume_btn.clicked.connect(lambda: self.resume_task(task_frame))
        button_layout.addWidget(resume_btn)

        delete_btn = QPushButton("删除")
        delete_btn.setObjectName("delete_btn")
        delete_btn.clicked.connect(lambda: self.delete_task(task_frame))
        button_layout.addWidget(delete_btn)

        task_layout.addLayout(button_layout)
        self.tasks_layout.addWidget(task_frame)

        return task_frame

    def init_ui(self) -> None:
        """初始化用户界面"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)  # 减少间距
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 标题
        title_label = QLabel("Hanime视频下载器")
        title_label.setFont(QFont("Arial", 18, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #3498db; margin-bottom: 10px;")
        main_layout.addWidget(title_label)

        # 下载路径设置区域
        path_group = QGroupBox("下载路径设置")
        path_layout = QVBoxLayout(path_group)
        path_group.setMaximumHeight(80)

        path_control_layout = QHBoxLayout()
        self.download_path_label = QLabel(f"当前下载路径: {self.download_dir}")
        self.download_path_label.setStyleSheet("color: #ecf0f1;")
        path_control_layout.addWidget(self.download_path_label)

        change_path_btn = QPushButton("更改路径")
        change_path_btn.clicked.connect(self.change_download_path)
        path_control_layout.addWidget(change_path_btn)

        path_layout.addLayout(path_control_layout)
        main_layout.addWidget(path_group)

        # 输入区域
        input_group = QGroupBox("输入视频列表链接")
        input_layout = QVBoxLayout(input_group)
        input_group.setMaximumHeight(120)

        # 创建输入框、粘贴按钮和无头模式开关的水平布局
        url_input_layout = QHBoxLayout()

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("例如: https://hanime1.me/watch?v=????")
        url_input_layout.addWidget(self.url_input, 1)

        # 添加粘贴按钮
        paste_btn = QPushButton("粘贴")
        paste_btn.setFixedWidth(80)
        paste_btn.clicked.connect(self.paste_clipboard)
        url_input_layout.addWidget(paste_btn)

        # 添加无头模式开关
        self.headless_checkbox = QCheckBox("无头模式")
        self.headless_checkbox.setChecked(self.headless_mode)
        self.headless_checkbox.stateChanged.connect(self.on_headless_changed)
        url_input_layout.addWidget(self.headless_checkbox)

        input_layout.addLayout(url_input_layout)

        # 按钮布局
        button_layout = QHBoxLayout()

        self.download_btn = QPushButton("开始下载")
        self.download_btn.clicked.connect(self.start_download)
        button_layout.addWidget(self.download_btn)

        self.pause_all_btn = QPushButton("暂停所有")
        self.pause_all_btn.clicked.connect(self.pause_all_tasks)
        button_layout.addWidget(self.pause_all_btn)

        self.resume_all_btn = QPushButton("继续所有")
        self.resume_all_btn.clicked.connect(self.resume_all_tasks)
        button_layout.addWidget(self.resume_all_btn)

        self.delete_all_btn = QPushButton("删除所有")
        self.delete_all_btn.clicked.connect(self.delete_all_tasks)
        self.delete_all_btn.setEnabled(False)
        button_layout.addWidget(self.delete_all_btn)

        input_layout.addLayout(button_layout)
        main_layout.addWidget(input_group)

        # 日志区域（缩小显示区域）
        log_group = QGroupBox("下载日志")
        log_layout = QVBoxLayout(log_group)
        log_group.setMaximumHeight(150)  # 设置最大高度

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(120)  # 设置高度
        log_layout.addWidget(self.log_area)

        main_layout.addWidget(log_group)

        # 视频下载进度区域（新增）
        progress_group = QGroupBox("视频下载进度")
        progress_layout = QVBoxLayout(progress_group)

        # 创建滚动区域
        progress_scroll = QScrollArea()
        progress_scroll.setWidgetResizable(True)
        progress_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # 进度容器
        self.video_progress_container = QWidget()
        self.video_progress_container.setObjectName("video_progress_container")
        self.video_progress_container.setStyleSheet("background-color: #2c3e50;")
        progress_layout_container = QVBoxLayout(self.video_progress_container)
        progress_layout_container.setAlignment(Qt.AlignTop)
        progress_layout_container.setSpacing(5)
        progress_layout_container.setContentsMargins(5, 5, 5, 5)

        # 设置初始高度为0
        self.video_progress_container.setMaximumHeight(0)

        progress_scroll.setWidget(self.video_progress_container)
        progress_layout.addWidget(progress_scroll)

        progress_group.setMaximumHeight(200)  # 设置最大高度
        main_layout.addWidget(progress_group)

        # 活动任务区域
        tasks_group = QGroupBox("活动下载任务")
        tasks_layout = QVBoxLayout(tasks_group)

        self.tasks_scroll = QScrollArea()
        self.tasks_scroll.setWidgetResizable(True)
        self.tasks_container = QWidget()
        self.tasks_container.setObjectName("tasks_container")
        self.tasks_layout = QVBoxLayout(self.tasks_container)
        self.tasks_layout.setAlignment(Qt.AlignTop)
        self.tasks_layout.setSpacing(5)  # 减少间距
        self.tasks_layout.setContentsMargins(5, 5, 5, 5)

        self.tasks_scroll.setWidget(self.tasks_container)
        tasks_layout.addWidget(self.tasks_scroll)

        main_layout.addWidget(tasks_group, 1)  # 给任务区域更多空间

        # 状态栏
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("就绪")

    def on_headless_changed(self, state: int) -> None:
        """无头模式复选框状态改变"""
        self.headless_mode = (state == Qt.Checked)
        self.save_config()

        if self.headless_mode:
            self.log_message("已启用无头模式（不显示浏览器界面）")
        else:
            self.log_message("已禁用无头模式（将显示浏览器界面）")

    def paste_clipboard(self) -> None:
        """粘贴剪贴板内容到输入框"""
        try:
            from PyQt5.QtWidgets import QApplication

            # 获取剪贴板内容
            clipboard = QApplication.clipboard()
            text = clipboard.text().strip()

            if text:
                self.url_input.setText(text)
                self.log_message(f"已粘贴剪贴板内容: {text[:50]}{'...' if len(text) > 50 else ''}")
            else:
                self.log_message("剪贴板为空或内容不是文本")

        except Exception as e:
            self.log_message(f"粘贴失败: {str(e)}")

    def log_message(self, message: str) -> None:
        """添加消息到日志区域"""
        if message.startswith("[TITLE_UPDATE]|||"):
            parts = message.split("|||")
            if len(parts) >= 3:
                task_id = parts[1]
                playlist_title = parts[2]
                self.update_task_title(task_id, playlist_title)
        elif message.startswith("[VIDEO_START]|||"):
            parts = message.split("|||")
            if len(parts) >= 3:
                task_id = parts[1]
                video_url = parts[2]
                # 添加视频进度显示
                self.add_video_progress(task_id, video_url)
        elif message.startswith("[VIDEO_PROGRESS]|||"):
            # 进度消息不再显示在日志中，只更新进度条
            pass
        elif message.startswith("[VIDEO_COMPLETE]|||"):
            parts = message.split("|||")
            if len(parts) >= 3:
                task_id = parts[1]
                video_url = parts[2]
                # 视频完成，更新状态
                if video_url in self.video_progress_widgets:
                    self.video_progress_widgets[video_url].update_progress(100, "已完成")
        elif message.startswith("[VIDEO_FAILED]|||"):
            parts = message.split("|||")
            if len(parts) >= 4:
                task_id = parts[1]
                video_url = parts[2]
                error = parts[3]
                # 视频失败，更新状态
                if video_url in self.video_progress_widgets:
                    self.video_progress_widgets[video_url].update_progress(0, "失败")
        else:
            timestamp = time.strftime("%H:%M:%S")
            self.log_area.append(f"[{timestamp}] {message}")
            # 自动滚动到底部
            self.log_area.verticalScrollBar().setValue(
                self.log_area.verticalScrollBar().maximum()
            )

    def update_task_title(self, task_id: str, playlist_title: str) -> None:
        """更新任务标题显示播放列表名称"""
        task_frame = self.findChild(QFrame, task_id)
        if task_frame:
            task_label = task_frame.findChild(QLabel)
            if task_label:
                # 获取当前文本，如果是失败任务则保留失败标记
                current_text = task_label.text()
                if current_text.startswith("[失败]播放列表:"):
                    task_label.setText(f"[失败]播放列表: {playlist_title}")
                elif current_text.startswith("[重试]播放列表:"):
                    task_label.setText(f"[重试]播放列表: {playlist_title}")
                else:
                    task_label.setText(f"播放列表: {playlist_title}")

    def start_download(self) -> None:
        """开始新的下载任务"""
        url = self.url_input.text().strip()
        if not url:
            self.log_message("请输入有效的URL")
            return

        self.url_input.clear()

        # 生成任务ID
        task_id = str(uuid.uuid4())

        # 记录任务开始
        self.task_logger.log_task_start(task_id, url, self.download_dir)

        # 创建任务显示框
        task_frame = self.create_task_frame(task_id, url, "playlist", "等待中")

        # 检查当前活动任务数量
        active_count = sum(1 for thread in self.active_threads
                           if thread.isRunning() and not getattr(thread, 'paused', False))

        if active_count < self.max_concurrent_tasks:
            update_task_status(task_frame, "运行中", "#2ecc71")
            self.start_download_task(url, task_frame, task_id)
        else:
            self.pending_tasks.append({
                "url": url,
                "frame": task_frame,
                "task_id": task_id,
                "task_type": "playlist",
                "status": "pending"
            })
            self.log_message(f"任务已添加到队列，当前队列位置: {len(self.pending_tasks)}")
            self.update_queue_status()

        self.delete_all_btn.setEnabled(True)

    def start_download_task(self, url: str, task_frame: QFrame, task_id: str) -> None:
        """启动下载线程"""
        self.log_message(f"启动新下载任务: {url}")
        self.log_message(f"下载路径: {self.download_dir}")
        self.log_message(f"无头模式: {'已启用' if self.headless_mode else '已禁用'}")

        # 获取任务信息
        task_info = self.task_logger.get_task_info(task_id)
        is_retry = task_info.get("is_retry", False) if task_info else False

        # 如果是重试任务，更新状态为运行中并清空失败状态
        if is_retry:
            self.task_logger.update_task_status(task_id, "running")

        thread = VideoDownloadThread(url, self.download_dir, task_id,
                                     self.task_logger, is_retry, self.headless_mode)
        thread.task_frame = task_frame
        thread.task_id = task_id

        # 连接信号
        thread.log_signal.connect(self.log_message)
        thread.finished_signal.connect(self.on_download_finished)

        self.active_threads.append(thread)

        update_task_status(task_frame, "运行中", "#2ecc71")
        thread.start()

    def on_download_finished(self, task_id: str, failed_urls: List[str]) -> None:
        """下载完成处理"""
        # 查找对应的线程和任务框
        thread = None
        task_frame = None

        for t in self.active_threads:
            if hasattr(t, 'task_id') and t.task_id == task_id:
                thread = t
                task_frame = t.task_frame
                break

        if thread and thread in self.active_threads:
            self.active_threads.remove(thread)

        # 获取任务信息
        task_info = self.task_logger.get_task_info(task_id)
        if not task_info:
            # 如果任务信息不存在，说明任务已完成并被删除
            self.log_message(f"任务 {task_id} 已完成并从记录中删除")
            if task_frame and task_frame.parent():
                task_frame.deleteLater()

            # 启动下一个任务
            self.start_next_task()
            return

        task_type = task_info.get("task_type", "playlist")
        status = task_info.get("status", "completed")
        is_retry = task_info.get("is_retry", False)

        # 检查任务是否失败
        if status == "failed" and failed_urls:
            self.log_message(f"任务 {task_id} 失败，有 {len(failed_urls)} 个失败视频")

            # 重置任务状态用于重试
            self.task_logger.reset_task_for_retry(task_id)

            # 更新任务显示
            if task_frame:
                # 更新任务标签
                task_label = task_frame.findChild(QLabel)
                if task_label:
                    current_text = task_label.text()
                    if task_type == "playlist":
                        new_text = f"[重试]播放列表: {task_info['url']}"
                    else:
                        new_text = f"[重试]视频: {task_info['url']}"
                    task_label.setText(new_text)

                # 更新状态标签
                status_label = task_frame.findChild(QLabel, "status_label")
                if status_label:
                    status_label.setText("状态: 已暂停")
                    status_label.setStyleSheet("color: #f39c12;")

                # 更新按钮状态
                pause_btn = task_frame.findChild(QPushButton, "pause_btn")
                if pause_btn:
                    pause_btn.setEnabled(False)
                resume_btn = task_frame.findChild(QPushButton, "resume_btn")
                if resume_btn:
                    resume_btn.setEnabled(True)

                # 将任务以暂停状态重新加入队列末尾
                already_in_queue = False
                for task in self.pending_tasks:
                    if task["task_id"] == task_id:
                        already_in_queue = True
                        task["status"] = "paused"
                        break

                if not already_in_queue:
                    self.pending_tasks.append({
                        "url": task_info["url"],
                        "frame": task_frame,
                        "task_id": task_id,
                        "task_type": task_type,
                        "status": "paused",
                        "is_retry": True
                    })

                self.log_message(f"失败任务已以暂停状态重新加入队列末尾，点击继续按钮重新开始")
                self.update_queue_status()
        elif status == "completed":
            # 任务成功完成，删除任务记录和界面显示
            self.log_message(f"任务 {task_id} 完成")
            if task_frame and task_frame.parent():
                task_frame.deleteLater()

            # 清除该任务的视频进度显示
            for video_url in list(self.video_progress_widgets.keys()):
                if video_url in self.task_videos.get(task_id, []):
                    self.remove_video_progress(video_url)
        else:
            self.log_message(f"任务 {task_id} 状态: {status}")

        # 启动下一个等待任务
        self.start_next_task()

    def start_next_task(self) -> None:
        """启动下一个等待中的任务"""
        if self.pending_tasks:
            # 查找第一个状态不是"paused"的任务
            for i, task in enumerate(self.pending_tasks):
                if task.get("status") != "paused":
                    next_task = self.pending_tasks.pop(i)
                    update_task_status(next_task["frame"], "运行中", "#2ecc71")
                    self.start_download_task(next_task["url"], next_task["frame"], next_task["task_id"])
                    self.update_queue_status()
                    return

    def update_queue_status(self) -> None:
        """更新队列中任务的状态显示"""
        for i, task in enumerate(self.pending_tasks):
            if task.get("status") == "paused":
                update_task_status(task["frame"], "已暂停", "#f39c12")
            else:
                update_task_status(task["frame"], f"队列中 ({i + 1})", "#f39c12")

    def pause_task(self, task_frame: QFrame) -> None:
        """暂停单个任务"""
        task_id = task_frame.objectName()

        # 首先检查任务是否在活动线程中
        found_in_active = False
        for thread in self.active_threads:
            if hasattr(thread, 'task_frame') and thread.task_frame == task_frame:
                thread.pause()
                update_task_status(task_frame, "已暂停", "#f39c12")
                self.log_message(f"任务已暂停: {thread.list_url}")

                # 更新任务状态
                task_id = thread.task_id
                if self.task_logger:
                    self.task_logger.update_task_status(task_id, "paused")

                # 更新pending_tasks中的状态
                for task in self.pending_tasks:
                    if task["frame"] == task_frame:
                        task["status"] = "paused"
                        break
                found_in_active = True
                break

        # 如果不在活动线程中，说明这是一个队列中的任务
        if not found_in_active:
            # 在pending_tasks中找到这个任务
            for task in self.pending_tasks:
                if task["frame"] == task_frame and task.get("status") != "paused":
                    # 更新状态
                    update_task_status(task_frame, "已暂停", "#f39c12")
                    task["status"] = "paused"

                    # 更新任务日志
                    if self.task_logger:
                        self.task_logger.update_task_status(task["task_id"], "paused")

                    self.log_message(f"任务已暂停: {task['url']}")
                    break

    def resume_task(self, task_frame: QFrame) -> None:
        """继续单个任务"""
        task_id = task_frame.objectName()

        # 首先检查任务是否在活动线程中
        found_in_active = False
        for thread in self.active_threads:
            if hasattr(thread, 'task_frame') and thread.task_frame == task_frame:
                thread.resume()
                update_task_status(task_frame, "运行中", "#2ecc71")
                self.log_message(f"任务已继续: {thread.list_url}")

                # 更新任务状态
                task_id = thread.task_id
                if self.task_logger:
                    self.task_logger.update_task_status(task_id, "running")

                # 更新pending_tasks中的状态
                for task in self.pending_tasks:
                    if task["frame"] == task_frame:
                        task["status"] = "running"
                        break
                found_in_active = True
                break

        # 如果不在活动线程中，说明这是一个暂停的队列任务
        if not found_in_active:
            # 在pending_tasks中找到这个任务
            for task in self.pending_tasks:
                if task["frame"] == task_frame and task.get("status") == "paused":
                    # 更新状态
                    update_task_status(task_frame, "运行中", "#2ecc71")
                    task["status"] = "running"

                    # 更新任务日志
                    if self.task_logger:
                        self.task_logger.update_task_status(task["task_id"], "running")

                    self.log_message(f"任务已恢复: {task['url']}")

                    # 检查当前活动任务数量
                    active_count = sum(1 for thread in self.active_threads
                                       if thread.isRunning() and not getattr(thread, 'paused', False))

                    # 如果并发数允许，立即启动这个任务
                    if active_count < self.max_concurrent_tasks:
                        # 从pending_tasks中移除并立即启动
                        self.pending_tasks.remove(task)
                        self.start_download_task(task["url"], task["frame"], task["task_id"])
                    else:
                        # 保持在队列中，但状态为运行中
                        self.log_message("并发任务数已达上限，任务保持在队列中")
                        update_task_status(task_frame, "队列中", "#f39c12")

                    self.update_queue_status()
                    break

    def delete_task(self, task_frame: QFrame) -> None:
        """删除单个任务（放弃任务）"""
        task_id = task_frame.objectName()

        # 停止活动线程中的任务
        for thread in self.active_threads:
            if hasattr(thread, 'task_frame') and thread.task_frame == task_frame:
                thread.stop()
                if thread.isRunning():
                    thread.wait(5000)  # 等待线程停止
                self.log_message(f"任务已删除: {thread.list_url}")

                # 删除任务记录
                task_id = thread.task_id
                self.task_logger.remove_task(task_id)

                if thread in self.active_threads:
                    self.active_threads.remove(thread)
                if task_frame and task_frame.parent():
                    task_frame.deleteLater()

                # 从pending_tasks中移除
                self.pending_tasks = [t for t in self.pending_tasks if t["frame"] != task_frame]

                # 清除该任务的视频进度显示
                for video_url in list(self.video_progress_widgets.keys()):
                    if video_url in self.task_videos.get(task_id, []):
                        self.remove_video_progress(video_url)

                self.start_next_task()
                return

        # 删除等待队列中的任务
        for task in self.pending_tasks:
            if task["frame"] == task_frame:
                self.pending_tasks.remove(task)
                self.log_message(f"已从队列中删除任务: {task['url']}")

                # 删除任务记录
                task_id = task["task_id"]
                self.task_logger.remove_task(task_id)

                if task_frame and task_frame.parent():
                    task_frame.deleteLater()

                # 清除该任务的视频进度显示
                for video_url in list(self.video_progress_widgets.keys()):
                    if video_url in self.task_videos.get(task_id, []):
                        self.remove_video_progress(video_url)

                self.update_queue_status()
                return

    def pause_all_tasks(self) -> None:
        """暂停所有任务"""
        # 暂停所有活动线程
        for thread in self.active_threads[:]:  # 使用副本遍历
            try:
                if thread.isRunning():
                    thread.pause()
                self.log_message(f"已暂停任务: {thread.list_url}")

                # 更新任务状态为暂停
                task_id = thread.task_id
                if self.task_logger:
                    self.task_logger.update_task_status(task_id, "paused")

                # 更新任务显示状态
                if hasattr(thread, 'task_frame'):
                    update_task_status(thread.task_frame, "已暂停", "#f39c12")
            except Exception as e:
                self.log_message(f"暂停任务时出错: {str(e)}")

        # 更新所有等待任务的状态为暂停
        for task in self.pending_tasks:
            if task.get("status") != "paused":
                task["status"] = "paused"
                update_task_status(task["frame"], "已暂停", "#f39c12")
                self.task_logger.update_task_status(task["task_id"], "paused")

        self.log_message("已暂停所有下载任务")

    def resume_all_tasks(self) -> None:
        """继续所有任务"""
        # 继续所有活动线程
        for thread in self.active_threads:
            try:
                if thread.isRunning():
                    thread.resume()
                self.log_message(f"已继续任务: {thread.list_url}")

                # 更新任务状态为运行中
                task_id = thread.task_id
                if self.task_logger:
                    self.task_logger.update_task_status(task_id, "running")

                # 更新任务显示状态
                if hasattr(thread, 'task_frame'):
                    update_task_status(thread.task_frame, "运行中", "#2ecc71")
            except Exception as e:
                self.log_message(f"继续任务时出错: {str(e)}")

        # 更新所有等待任务的状态为运行中
        for task in self.pending_tasks:
            if task.get("status") == "paused":
                task["status"] = "running"
                update_task_status(task["frame"], "运行中", "#2ecc71")
                self.task_logger.update_task_status(task["task_id"], "running")

        self.log_message("已继续所有下载任务")

    def delete_all_tasks(self) -> None:
        """删除所有任务（放弃所有任务）"""
        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定要删除所有任务吗？此操作将永久删除任务记录，不可恢复。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        # 删除所有活动线程
        for thread in self.active_threads[:]:  # 使用副本遍历
            try:
                if thread.isRunning():
                    thread.stop()
                    thread.wait(5000)  # 等待5秒让线程停止
                self.log_message(f"已删除任务: {thread.list_url}")

                # 删除任务记录
                task_id = thread.task_id
                self.task_logger.remove_task(task_id)
            except Exception as e:
                self.log_message(f"删除任务时出错: {str(e)}")

        # 删除所有等待任务
        for task in self.pending_tasks[:]:  # 使用副本遍历
            try:
                self.log_message(f"已删除任务: {task['url']}")

                # 删除任务记录
                task_id = task["task_id"]
                self.task_logger.remove_task(task_id)

                # 删除任务框
                if task["frame"] and task["frame"].parent():
                    task["frame"].deleteLater()
            except Exception as e:
                self.log_message(f"删除任务时出错: {str(e)}")

        # 清除所有视频进度显示
        self.clear_all_video_progress()

        # 清除所有列表
        self.active_threads.clear()
        self.pending_tasks.clear()
        self.delete_all_btn.setEnabled(False)
        self.log_message("已删除所有任务")

    def change_download_path(self) -> None:
        """更改下载路径"""
        new_path = QFileDialog.getExistingDirectory(
            self,
            "选择下载目录",
            self.download_dir,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )

        if new_path:
            self.download_dir = new_path
            self.download_path_label.setText(f"当前下载路径: {self.download_dir}")
            self.save_config()
            self.log_message(f"下载路径已更新为: {self.download_dir}")

    def closeEvent(self, event) -> None:
        """关闭窗口时停止所有线程"""
        if self.active_threads or self.pending_tasks:
            reply = QMessageBox.question(
                self,
                "确认退出",
                "有任务正在运行，确定要退出吗？未完成的任务将自动保存，下次启动时恢复。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                # 更新所有活动任务状态为暂停
                for thread in self.active_threads:
                    if thread.isRunning():
                        task_id = thread.task_id
                        self.task_logger.update_task_status(task_id, "paused")
                        thread.stop()

                # 更新所有等待任务状态为暂停
                for task in self.pending_tasks:
                    if task.get("status") != "paused":
                        self.task_logger.update_task_status(task["task_id"], "paused")

                # 等待线程停止
                time.sleep(2)
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()