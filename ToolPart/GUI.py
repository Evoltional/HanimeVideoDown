import os
import sys
import time
import uuid
import logging
from datetime import datetime
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QTextEdit, QGroupBox,
                             QScrollArea, QFrame, QFileDialog, QCheckBox)

from ToolPart.Config import ConfigManager
from ToolPart.DownLoad import DownloadWorker
from ToolPart.TaskLogger import TaskLogger

logger = logging.getLogger(__name__)


class StepTracker:
    """步骤跟踪器，用于记录每个操作步骤的时间和状态"""

    def __init__(self, task_id: str, step_name: str):
        self.task_id = task_id
        self.step_name = step_name
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.status = "running"
        self.details: Dict = {}

    def complete(self, details: Optional[Dict] = None):
        self.end_time = time.time()
        self.status = "completed"
        if details:
            self.details.update(details)

    def fail(self, error_message: str, details: Optional[Dict] = None):
        self.end_time = time.time()
        self.status = "failed"
        self.details["error"] = error_message
        if details:
            self.details.update(details)

    def get_duration(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "step_name": self.step_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.get_duration(),
            "status": self.status,
            "details": self.details
        }


class PerformanceOptimizer:
    """性能优化器，用于优化浏览器操作和减少等待时间"""

    PAGE_LOAD_TIMEOUT = 2
    ELEMENT_WAIT_TIMEOUT = 3
    RETRY_ATTEMPTS = 2

    @staticmethod
    def optimize_browser_settings() -> Dict:
        return {
            "page_load_timeout": PerformanceOptimizer.PAGE_LOAD_TIMEOUT,
            "element_wait_timeout": PerformanceOptimizer.ELEMENT_WAIT_TIMEOUT,
            "retry_attempts": PerformanceOptimizer.RETRY_ATTEMPTS
        }

    @staticmethod
    def calculate_optimal_delay(base_delay: float, attempt: int) -> float:
        return base_delay * (1.5 ** attempt)


def _update_task_ui_status(task_frame, status_text, status_color,
                           pause_enabled=True, pause_style="",
                           resume_enabled=False, resume_style="",
                           stop_enabled=True):
    """更新任务卡片的 UI 状态（新增 stop_enabled 参数）"""
    status_label = task_frame.findChild(QLabel, "status_label")
    if status_label:
        status_label.setText(status_text)
        status_label.setStyleSheet(f"color: {status_color}; font-weight: bold; font-size: 12px;")

    pause_btn = task_frame.findChild(QPushButton, "pause_btn")
    if pause_btn:
        pause_btn.setEnabled(pause_enabled)
        if pause_style:
            pause_btn.setStyleSheet(pause_style)
        else:
            pause_btn.setStyleSheet("")

    resume_btn = task_frame.findChild(QPushButton, "resume_btn")
    if resume_btn:
        resume_btn.setEnabled(resume_enabled)
        if resume_style:
            resume_btn.setStyleSheet(resume_style)
        else:
            resume_btn.setStyleSheet("")

    stop_btn = task_frame.findChild(QPushButton, "stop_btn")
    if stop_btn:
        stop_btn.setEnabled(stop_enabled)


class TaskManager:
    """任务管理器，控制并发数量，统一状态管理"""

    # 状态常量
    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_PAUSED = "paused"
    STATUS_FAILED = "failed"
    STATUS_STOPPED = "stopped"

    def __init__(self, max_active_tasks=2, task_logger=None):
        self.max_active_tasks = max_active_tasks
        self.task_info_map = {}          # task_id -> task_info
        self.task_logger = task_logger

    def _get_tasks_by_status(self, status: str) -> List[Dict]:
        return [info for info in self.task_info_map.values() if info['status'] == status]

    def add_task(self, worker, task_frame, url, task_id=None, is_resume=False, status=STATUS_PENDING):
        if task_id is None:
            task_id = str(uuid.uuid4())

        task_info = {
            'worker': worker,
            'task_frame': task_frame,
            'url': url,
            'task_id': task_id,
            'status': status
        }

        self.task_info_map[task_id] = task_info

        if status == self.STATUS_ACTIVE:
            worker.start()
        elif status == self.STATUS_PENDING:
            self._try_start_pending_tasks()
        elif status == self.STATUS_PAUSED:
            # 已暂停，不启动
            pass

        if self.task_logger and not is_resume:
            self.task_logger.add_task(task_id, url, worker.download_dir)

        return task_id

    def _try_start_pending_tasks(self):
        pending = self._get_tasks_by_status(self.STATUS_PENDING)
        active = self._get_tasks_by_status(self.STATUS_ACTIVE)
        while len(active) < self.max_active_tasks and pending:
            task_info = pending[0]
            task_info['status'] = self.STATUS_ACTIVE
            worker = task_info['worker']
            if worker.isRunning():
                worker.resume()
            else:
                worker.start()
            if self.task_logger:
                self.task_logger.update_task_status(task_info['task_id'], 'running')
            self._set_task_ui_running(task_info['task_frame'])
            pending = self._get_tasks_by_status(self.STATUS_PENDING)
            active = self._get_tasks_by_status(self.STATUS_ACTIVE)

    def _find_task_id_by_worker(self, worker):
        for tid, info in self.task_info_map.items():
            if info['worker'] == worker:
                return tid
        return None

    def pause_task(self, worker):
        task_id = self._find_task_id_by_worker(worker)
        if not task_id:
            return
        task_info = self.task_info_map[task_id]
        if task_info['status'] == self.STATUS_ACTIVE:
            task_info['worker'].pause()
            task_info['status'] = self.STATUS_PAUSED
            if self.task_logger:
                self.task_logger.update_task_status(task_id, 'paused')
            self._set_task_ui_paused(task_info['task_frame'])
            self._try_start_pending_tasks()

    def resume_task(self, worker, force_retry=False):
        task_id = self._find_task_id_by_worker(worker)
        if not task_id:
            return
        task_info = self.task_info_map[task_id]
        if force_retry:
            if hasattr(worker, 'reload_failed_links_from_logger'):
                worker.reload_failed_links_from_logger()
            worker.retry_failed_links = True
        if task_info['status'] in [self.STATUS_PAUSED, self.STATUS_FAILED]:
            task_info['status'] = self.STATUS_PENDING
            if self.task_logger:
                self.task_logger.update_task_status(task_id, 'waiting')
            self._set_task_ui_pending(task_info['task_frame'])
            self._try_start_pending_tasks()

    def stop_task(self, worker):
        task_id = self._find_task_id_by_worker(worker)
        if not task_id:
            return
        task_info = self.task_info_map[task_id]
    
        if hasattr(worker, 'stop'):
            worker.stop()
        if hasattr(worker, '_pause_event'):
            worker._pause_event.set()
    
        if worker.isRunning():
            if not worker.wait(5000):
                logger.warning(f"任务 {task_id} 线程在 5 秒内未停止")
    
        task_info['status'] = self.STATUS_STOPPED
        self._set_task_ui_stopped(task_info['task_frame'])
        # 注意：不再在这里调用 task_logger.update_task_status，由 GUI 层处理
        self._try_start_pending_tasks()

    def remove_task(self, worker):
        task_id = self._find_task_id_by_worker(worker)
        if task_id and task_id in self.task_info_map:
            self._cleanup_task_resources(task_id)
            self._try_start_pending_tasks()

    def _cleanup_task_resources(self, task_id: str) -> None:
        if task_id not in self.task_info_map:
            return
        task_info = self.task_info_map[task_id]

        try:
            if hasattr(task_info['worker'], 'stop'):
                task_info['worker'].stop()
            if task_info['worker'].isRunning():
                task_info['worker'].wait(3000)
        except Exception as e:
            logger.error(f"停止任务线程时出错: {e}")

        try:
            if self.task_logger:
                self.task_logger.remove_task(task_id)
        except Exception as e:
            logger.error(f"从TaskLogger删除任务时出错: {e}")

        try:
            del self.task_info_map[task_id]
        except Exception as e:
            logger.error(f"删除任务映射时出错: {e}")

    def _set_task_ui_paused(self, task_frame):
        _update_task_ui_status(
            task_frame,
            "状态: 已暂停",
            "#f39c12",
            pause_enabled=False,
            pause_style="background-color: #7f8c8d;",
            resume_enabled=True,
            resume_style=""
        )

    def _set_task_ui_running(self, task_frame):
        _update_task_ui_status(
            task_frame,
            "状态: 运行中",
            "#2ecc71",
            pause_enabled=True,
            pause_style="background-color: #3498db;",
            resume_enabled=False,
            resume_style="background-color: #7f8c8d;"
        )

    def _set_task_ui_pending(self, task_frame):
        _update_task_ui_status(
            task_frame,
            "状态: 待处理",
            "#7f8c8d",
            pause_enabled=True,
            pause_style="",
            resume_enabled=False,
            resume_style="background-color: #7f8c8d;"
        )

    def _set_task_ui_failed(self, task_frame):
        _update_task_ui_status(
            task_frame,
            "状态: 失败",
            "#e74c3c",
            pause_enabled=False,
            pause_style="background-color: #7f8c8d;",
            resume_enabled=True,
            resume_style=""
        )

    def _set_task_ui_stopped(self, task_frame):
        _update_task_ui_status(
            task_frame,
            "状态: 已停止",
            "#95a5a6",
            pause_enabled=False,
            pause_style="background-color: #7f8c8d;",
            resume_enabled=False,
            resume_style="background-color: #7f8c8d;",
            stop_enabled=False   # 停止按钮禁用
        )

    def pause_all_tasks(self):
        for task_info in self.task_info_map.values():
            if task_info['status'] == self.STATUS_ACTIVE:
                task_info['worker'].pause()
                task_info['status'] = self.STATUS_PAUSED
                if self.task_logger:
                    self.task_logger.update_task_status(task_info['task_id'], 'paused')
                self._set_task_ui_paused(task_info['task_frame'])

    def resume_all_tasks(self, force_retry_failed=False):
        for task_info in self.task_info_map.values():
            if task_info['status'] in [self.STATUS_PAUSED, self.STATUS_FAILED]:
                if force_retry_failed:
                    worker = task_info['worker']
                    if hasattr(worker, 'reload_failed_links_from_logger'):
                        worker.reload_failed_links_from_logger()
                    worker.retry_failed_links = True
                task_info['status'] = self.STATUS_PENDING
                if self.task_logger:
                    self.task_logger.update_task_status(task_info['task_id'], 'waiting')
                self._set_task_ui_pending(task_info['task_frame'])
        self._try_start_pending_tasks()

    def stop_all_tasks(self):
        logger.info(f"停止所有任务 - 活跃: {len(self._get_tasks_by_status(self.STATUS_ACTIVE))}, "
                    f"待处理: {len(self._get_tasks_by_status(self.STATUS_PENDING))}, "
                    f"暂停: {len(self._get_tasks_by_status(self.STATUS_PAUSED))}")

        for task_info in self.task_info_map.values():
            if hasattr(task_info['worker'], 'stop'):
                task_info['worker'].stop()
            if hasattr(task_info['worker'], '_pause_event'):
                task_info['worker']._pause_event.set()

        for task_info in self.task_info_map.values():
            if task_info['worker'].isRunning():
                if not task_info['worker'].wait(5000):
                    logger.warning(f"任务 {task_info['task_id']} 线程未能在5秒内停止")

        for task_info in self.task_info_map.values():
            task_info['status'] = self.STATUS_STOPPED
            self._set_task_ui_stopped(task_info['task_frame'])
            # 注意：不再在这里调用 task_logger.update_task_status，由 GUI 层统一处理

    def clear_all_tasks(self):
        try:
            self.stop_all_tasks()
            try:
                if self.task_logger:
                    self.task_logger.clear_all_tasks()
            except Exception as e:
                logger.error(f"清空TaskLogger时出错: {e}")
            self.task_info_map.clear()
        except Exception as e:
            logger.error(f"清空所有任务时发生未知错误: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        print("1. 开始初始化配置")
        self.config_manager = ConfigManager()
        print("2. 配置加载完成")
        self.task_logger = TaskLogger()
        print("3. TaskLogger初始化完成")
        self.step_trackers: Dict[str, List[StepTracker]] = {}
        print("4. 步骤追踪器初始化完成")
        self.performance_optimizer = PerformanceOptimizer()

        window_pos = self.config_manager.get("window_position", [100, 100])
        window_size = self.config_manager.get("window_size", [1000, 800])
        print(f"5. 设置窗口位置: {window_pos}, 窗口大小: {window_size}")
        self.setGeometry(window_pos[0], window_pos[1], window_size[0], window_size[1])
        print("6. 设置窗口完成")
        self.setWindowTitle("Hanime视频下载器")
        print("7. 设置窗口标题完成")
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
                padding: 9px 16px;
                font-weight: bold;
                font-size: 16px;
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
                font-size: 20px;
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
        print("8. 设置样式表完成")
        self.download_dir = self.config_manager.get("download_dir", os.path.join(os.getcwd(), "Download"))
        print(f"9. 设置下载目录为: {self.download_dir}")
        self.headless_mode = self.config_manager.get("headless_mode", True)
        print(f"10. 设置无头模式为: {self.headless_mode}")
        self.bypass_mode = self.config_manager.get("bypass_mode", False)  # 读取Bypass设置
        print(f"11. 读取Bypass设置: {self.bypass_mode}")
        self.task_manager = TaskManager(max_active_tasks=2, task_logger=self.task_logger)
        print("12. 初始化任务管理器完成")
        self.init_ui()
        print("13. 初始化UI完成")
        self.restore_incomplete_tasks()

    def update_task_count(self, worker: DownloadWorker):
        for tid, info in self.task_manager.task_info_map.items():
            if info['worker'] == worker:
                task_info = info
                break
        else:
            return
        progress_count_label = task_info['task_frame'].findChild(QLabel, "progress_count_label")
        if progress_count_label:
            self._update_progress_count_display(task_info, progress_count_label)

    def restore_incomplete_tasks(self):
        incomplete_tasks = self.task_logger.get_incomplete_tasks()
        if incomplete_tasks:
            self.log_message(f"找到 {len(incomplete_tasks)} 个未完成的任务，正在恢复...")
            for task_id, task_info in incomplete_tasks.items():
                url = task_info.get("url", "")
                download_dir = task_info.get("download_dir", self.download_dir)
                status = task_info.get("status", "waiting")

                downloading_files = self.task_logger.get_downloading_files(download_dir)
                if downloading_files:
                    self.log_message(f"发现未完成的下载文件: {len(downloading_files)} 个")

                if url:
                    self.restore_task(task_id, url, download_dir, status)
            self.log_message("任务恢复完成")
        else:
            self.log_message("没有发现未完成的任务")

    def restore_task(self, task_id: str, url: str, download_dir: str, status: str = "paused"):
        self._check_latest_headless_setting()
        # 从配置读取当前的Bypass模式
        use_bypass = self.config_manager.get("bypass_mode", False)
        worker = DownloadWorker(url, download_dir, self.headless_mode, use_bypass, task_logger=self.task_logger, task_id=task_id, config_manager=self.config_manager, is_restored=True)
        worker.log_signal.connect(self.log_message)
        worker.finished_signal.connect(self.on_download_finished)
        worker.progress_signal.connect(lambda progress, w=worker: self.update_task_progress(w, progress))

        task_frame = self.create_task_frame(url, worker, is_resume=(status != TaskManager.STATUS_PENDING))

        # 根据传入的状态设置 UI
        if status == TaskManager.STATUS_FAILED:
            self.task_manager._set_task_ui_failed(task_frame)
        elif status == TaskManager.STATUS_STOPPED:
            self.task_manager._set_task_ui_stopped(task_frame)
        elif status == TaskManager.STATUS_PAUSED:
            self.task_manager._set_task_ui_paused(task_frame)
        else:  # PENDING 或默认
            self.task_manager._set_task_ui_pending(task_frame)

        self.task_manager.add_task(worker, task_frame, url, task_id=task_id,
                                   is_resume=True, status=status)
        self.log_message(f"已恢复任务: {url} (原状态: {status})")

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(15, 15, 15, 15)

        title_label = QLabel("Hanime视频下载器")
        title_label.setFont(QFont("Arial", 18, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #3498db; margin-bottom: 10px;")
        main_layout.addWidget(title_label)

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

        open_dir_btn = QPushButton("打开目录")
        open_dir_btn.clicked.connect(self.open_download_directory)
        path_control_layout.addWidget(open_dir_btn)

        path_layout.addLayout(path_control_layout)
        main_layout.addWidget(path_group)

        input_group = QGroupBox("输入视频列表链接")
        input_layout = QVBoxLayout(input_group)
        input_group.setMaximumHeight(180)

        url_input_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("例如: https://hanime1.me/watch?v=????")
        self.url_input.setMinimumHeight(50)
        url_input_layout.addWidget(self.url_input, 1)

        paste_btn = QPushButton("粘贴")
        paste_btn.setFixedWidth(80)
        paste_btn.clicked.connect(self.paste_clipboard)
        url_input_layout.addWidget(paste_btn)

        # 新增：Bypass模式复选框
        self.bypass_checkbox = QCheckBox("启用Bypass")
        self.bypass_checkbox.setChecked(self.bypass_mode)
        self.bypass_checkbox.stateChanged.connect(self.on_bypass_changed)
        url_input_layout.addWidget(self.bypass_checkbox)

        self.headless_checkbox = QCheckBox("无头模式")
        self.headless_checkbox.setChecked(self.headless_mode)
        self.headless_checkbox.stateChanged.connect(self.on_headless_changed)
        url_input_layout.addWidget(self.headless_checkbox)

        input_layout.addLayout(url_input_layout)
        input_layout.addSpacing(10)

        button_layout = QHBoxLayout()
        self.download_btn = QPushButton("开始下载")
        self.download_btn.clicked.connect(self.start_download)
        button_layout.addWidget(self.download_btn)

        self.pause_btn = QPushButton("暂停全部")
        self.pause_btn.clicked.connect(self.pause_download)
        button_layout.addWidget(self.pause_btn)

        self.stop_all_btn = QPushButton("停止全部")
        self.stop_all_btn.clicked.connect(self.stop_all_tasks)
        button_layout.addWidget(self.stop_all_btn)

        self.clear_all_btn = QPushButton("删除全部")
        self.clear_all_btn.clicked.connect(self.clear_all_tasks)
        button_layout.addWidget(self.clear_all_btn)

        input_layout.addLayout(button_layout)
        main_layout.addWidget(input_group)

        log_group = QGroupBox("下载日志")
        log_layout = QVBoxLayout(log_group)
        log_group.setMaximumHeight(200)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        log_layout.addWidget(self.log_area)

        main_layout.addWidget(log_group)

        tasks_group = QGroupBox("活动下载任务")
        tasks_layout = QVBoxLayout(tasks_group)

        self.tasks_scroll = QScrollArea()
        self.tasks_scroll.setWidgetResizable(True)
        self.tasks_container = QWidget()
        self.tasks_container.setObjectName("tasks_container")
        self.tasks_layout = QVBoxLayout(self.tasks_container)
        self.tasks_layout.setAlignment(Qt.AlignTop)
        self.tasks_layout.setSpacing(8)
        self.tasks_layout.setContentsMargins(5, 5, 5, 5)

        self.tasks_scroll.setWidget(self.tasks_container)
        tasks_layout.addWidget(self.tasks_scroll)

        main_layout.addWidget(tasks_group, 1)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("就绪")

    def on_headless_changed(self, state: int) -> None:
        self.headless_mode = (state == Qt.Checked)
        if self.headless_mode:
            self.log_message("已启用无头模式（不显示浏览器界面）")
        else:
            self.log_message("已禁用无头模式（将显示浏览器界面）")
        self.config_manager.set("headless_mode", self.headless_mode)

    def on_bypass_changed(self, state: int) -> None:
        self.bypass_mode = (state == Qt.Checked)
        if self.bypass_mode:
            self.log_message("已启用Bypass模式（处理Cloudflare验证）")
        else:
            self.log_message("已禁用Bypass模式（普通访问）")
        self.config_manager.set("bypass_mode", self.bypass_mode)

    def paste_clipboard(self) -> None:
        try:
            from PyQt5.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            text = clipboard.text().strip()
            if text:
                self.url_input.setText(text)
                self.log_message(f"已粘贴剪贴板内容: {text[:50]}{'...' if len(text) > 50 else ''}")
            else:
                self.log_message("剪贴板为空或内容不是文本")
        except Exception as e:
            self.log_message(f"粘贴失败: {str(e)}")

    def log_message(self, message: str, level=None) -> None:
        if level is None:
            level = "INFO"
            if "错误" in message or "失败" in message or "exception" in message.lower():
                level = "ERROR"
            elif "警告" in message or "注意" in message:
                level = "WARNING"
            elif "成功" in message or "完成" in message:
                level = "SUCCESS"
            elif "调试" in message or "debug" in message.lower():
                level = "DEBUG"

        timestamp = time.strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {level} - {message}"
        self.log_area.append(log_message)
        self.log_area.verticalScrollBar().setValue(
            self.log_area.verticalScrollBar().maximum()
        )

    def start_step_log(self, task_id: str, step_name: str) -> StepTracker:
        step_tracker = StepTracker(task_id, step_name)
        if task_id not in self.step_trackers:
            self.step_trackers[task_id] = []
        self.step_trackers[task_id].append(step_tracker)

        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_area.append(f"[{timestamp}] STEP_START - 任务 {task_id[:8]} 开始步骤: {step_name}")
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())
        return step_tracker

    def complete_step_log(self, step_tracker: StepTracker, details: Optional[Dict] = None):
        step_tracker.complete(details)
        duration = step_tracker.get_duration()
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_area.append(f"[{timestamp}] STEP_COMPLETE - 任务 {step_tracker.task_id[:8]} "
                             f"完成步骤: {step_tracker.step_name} (耗时: {duration:.2f}秒)")
        if details:
            detail_str = ", ".join([f"{k}: {v}" for k, v in details.items()])
            self.log_area.append(f"[{timestamp}] STEP_DETAILS - {detail_str}")
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    def fail_step_log(self, step_tracker: StepTracker, error_message: str, details: Optional[Dict] = None):
        step_tracker.fail(error_message, details)
        duration = step_tracker.get_duration()
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_area.append(f"[{timestamp}] STEP_FAILED - 任务 {step_tracker.task_id[:8]} "
                             f"步骤失败: {step_tracker.step_name} (耗时: {duration:.2f}秒) - {error_message}")
        self.log_area.verticalScrollBar().setValue(self.log_area.verticalScrollBar().maximum())

    def log_detailed_message(self, message: str, level=None, details=None) -> None:
        self.log_message(message, level)
        if details and isinstance(details, dict):
            timestamp = time.strftime("%H:%M:%S")
            detail_lines = []
            for key, value in details.items():
                if isinstance(value, (list, dict)):
                    formatted_value = str(value)
                    if len(formatted_value) > 100:
                        formatted_value = formatted_value[:100] + "..."
                    detail_lines.append(f"  {key}: {formatted_value}")
                else:
                    detail_lines.append(f"  {key}: {value}")
            if detail_lines:
                detail_message = f"[{timestamp}] DETAIL - 详细信息:\n" + "\n".join(detail_lines)
                self.log_area.append(detail_message)
                self.log_area.verticalScrollBar().setValue(
                    self.log_area.verticalScrollBar().maximum()
                )

    def start_download(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self.log_message("正在恢复所有暂停、失败和停止的任务...")
            # 获取所有未完成任务（状态不为 completed）
            all_tasks = self.task_logger.get_all_tasks()
            for task_id, task_info in all_tasks.items():
                if task_info.get("status") not in ["completed", "running"]:
                    # 如果任务已不在 UI 中（可能已停止且被清理），则重新创建
                    if task_id not in self.task_manager.task_info_map:
                        self.restore_task(task_id, task_info["url"],
                                          task_info.get("download_dir", self.download_dir),
                                          status=task_info.get("status", TaskManager.STATUS_PAUSED))
                    else:
                        # 对于已存在但暂停/失败的任务，恢复它们
                        existing_info = self.task_manager.task_info_map[task_id]
                        self.resume_task(existing_info['worker'], force_retry=True)
            return

        self.url_input.clear()

        task_creation_step = self.start_step_log("main", "创建下载任务")
        try:
            opt_settings = self.performance_optimizer.optimize_browser_settings()
            self.log_message(f"应用性能优化设置: 页面加载超时{opt_settings['page_load_timeout']}秒, "
                             f"元素等待{opt_settings['element_wait_timeout']}秒")

            task_id = str(uuid.uuid4())
            # 读取当前的Bypass选项
            use_bypass = self.bypass_checkbox.isChecked()
            worker = DownloadWorker(url, self.download_dir, self.headless_mode, use_bypass, task_logger=self.task_logger,
                                    task_id=task_id, config_manager=self.config_manager)
            worker.log_signal.connect(self.log_message)
            worker.finished_signal.connect(self.on_download_finished)
            worker.progress_signal.connect(lambda progress, w=worker: self.update_task_progress(w, progress))
            worker.count_updated_signal.connect(lambda: self.update_task_count(worker))

            task_frame = self.create_task_frame(url, worker)
            task_id = self.task_manager.add_task(worker, task_frame, url, task_id=task_id, status=TaskManager.STATUS_PENDING)

            self.complete_step_log(task_creation_step, {
                "任务ID": task_id[:8],
                "URL": url[:50] + ("..." if len(url) > 50 else ""),
                "下载目录": self.download_dir
            })

            task_details = {
                "任务ID": task_id,
                "下载目录": self.download_dir,
                "无头模式": self.headless_mode,
                "Bypass模式": use_bypass,
                "最大并发数": self.task_manager.max_active_tasks,
                "性能优化": opt_settings
            }
            self.log_detailed_message(f"已添加下载任务: {url}", "INFO", task_details)

        except Exception as e:
            self.fail_step_log(task_creation_step, str(e))
            self.log_message(f"创建下载任务失败: {str(e)}", "ERROR")

    def on_download_finished(self, success: bool) -> None:
        sender = self.sender()
        task_id = None
        task_info = None
        for tid, info in self.task_manager.task_info_map.items():
            if info['worker'] == sender:
                task_id = tid
                task_info = info
                break

        if task_id is not None and task_info:
            url = task_info['url']

            if success:
                self.tasks_layout.removeWidget(task_info['task_frame'])
                task_info['task_frame'].deleteLater()
                self.task_manager.remove_task(sender)
                self.log_message(f"下载任务已完成并已移除: {url}", "SUCCESS")
            else:
                self.log_message(f"下载任务失败: {url}")
                worker = task_info['worker']
                if hasattr(worker, 'failed_links_to_retry') and worker.failed_links_to_retry:
                    self.log_message(f"任务包含失败链接，已暂停等待重试")
                    task_info['status'] = TaskManager.STATUS_PAUSED
                    self.task_manager._set_task_ui_paused(task_info['task_frame'])
                    progress_frame = task_info['task_frame'].findChild(QFrame, "progress_frame")
                    if progress_frame:
                        left_progress = progress_frame.findChild(QLabel, "left_progress")
                        if left_progress:
                            left_progress.setText(f"{len(worker.failed_links_to_retry)}个链接失败，点击继续重试")
                    if self.task_manager.task_logger:
                        self.task_manager.task_logger.update_task_status(task_id, 'paused')
                else:
                    if self.task_manager.task_logger:
                        self.task_manager.task_logger.update_task_status(task_id, 'failed')
                    self.task_manager._set_task_ui_failed(task_info['task_frame'])
                    progress_frame = task_info['task_frame'].findChild(QFrame, "progress_frame")
                    if progress_frame:
                        left_progress = progress_frame.findChild(QLabel, "left_progress")
                        if left_progress:
                            left_progress.setText("下载失败")
                    task_info['status'] = TaskManager.STATUS_FAILED

                self.task_manager._try_start_pending_tasks()

    def update_task_progress(self, worker, progress_text: str):
        task_frame = None
        task_info = None
        for tid, info in self.task_manager.task_info_map.items():
            if info['worker'] == worker:
                task_frame = info['task_frame']
                task_info = info
                break

        if task_frame:
            progress_lines = progress_text.split('\n')
            progress_frame = task_frame.findChild(QFrame, "progress_frame")
            if not progress_frame:
                return

            left_progress = progress_frame.findChild(QLabel, "left_progress")
            right_progress = progress_frame.findChild(QLabel, "right_progress")
            progress_count_label = task_frame.findChild(QLabel, "progress_count_label")

            if not left_progress or not right_progress:
                return

            if len(progress_lines) == 1:
                left_progress.setText(progress_lines[0])
                right_progress.setText("")
            elif len(progress_lines) >= 2:
                left_progress.setText(progress_lines[0])
                right_progress.setText(progress_lines[1])
            else:
                current_processing = self._get_current_processing_info(worker)
                if current_processing:
                    left_progress.setText(current_processing)
                    right_progress.setText("")
                else:
                    left_progress.setText("等待开始...")
                    right_progress.setText("")

            if progress_count_label and task_info:
                self._update_progress_count_display(task_info, progress_count_label)

    def _get_current_processing_info(self, worker) -> str:
        try:
            if hasattr(worker, 'current_filename') and worker.current_filename:
                return f"正在处理: {worker.current_filename}"
            elif hasattr(worker, 'scraper') and worker.scraper:
                if hasattr(worker.scraper, 'downloading_files') and worker.scraper.downloading_files:
                    for filename, progress in worker.scraper.downloading_files.items():
                        return f"正在处理: {filename}"
            return ""
        except Exception as e:
            logger.error(f"获取当前处理信息时出错: {e}")
            return ""

    def _update_progress_count_display(self, task_info: dict, progress_count_label: QLabel) -> None:
        try:
            if self.task_logger and task_info.get('task_id'):
                task_id = task_info['task_id']
                all_tasks = self.task_logger.get_all_tasks()
                if task_id in all_tasks:
                    task_data = all_tasks[task_id]
                    downloaded_count = len(task_data.get('downloaded_videos', []))
                    total_count = task_data.get('total_video_count', 0)  # 使用保存的总数
                    if total_count == 0:
                        # 兼容旧数据，计算总数（但可能不准）
                        video_links = task_data.get('video_links', [])
                        failed_links = task_data.get('failed_links', [])
                        total_count = len(video_links) + len(failed_links)
                    progress_count_label.setText(f"{downloaded_count}/{total_count}")
                    if total_count > 0:
                        completion_rate = downloaded_count / total_count
                        if completion_rate >= 1.0:
                            progress_count_label.setStyleSheet(
                                "color: #2ecc71; font-weight: bold; font-size: 20px; padding: 0 10px;")
                        elif completion_rate >= 0.5:
                            progress_count_label.setStyleSheet(
                                "color: #f1c40f; font-weight: bold; font-size: 20px; padding: 0 10px;")
                        else:
                            progress_count_label.setStyleSheet(
                                "color: #3498db; font-weight: bold; font-size: 20px; padding: 0 10px;")
                else:
                    progress_count_label.setText("0/0")
                    progress_count_label.setStyleSheet(
                        "color: #3498db; font-weight: bold; font-size: 20px; padding: 0 10px;")
            else:
                progress_count_label.setText("0/0")
                progress_count_label.setStyleSheet(
                    "color: #3498db; font-weight: bold; font-size: 20px; padding: 0 10px;")
        except Exception as e:
            progress_count_label.setText("0/0")
            progress_count_label.setStyleSheet("color: #e74c3c; font-weight: bold; font-size: 20px; padding: 0 10px;")
            logger.error(f"更新进度计数显示时出错: {e}")

    def pause_download(self) -> None:
        if self.task_manager.task_info_map:
            self.task_manager.pause_all_tasks()
            self.log_message("已暂停所有下载任务")
        else:
            self.log_message("没有正在运行的任务可以暂停")

    def resume_all_tasks(self, force_retry_failed=False):
        self.task_manager.resume_all_tasks(force_retry_failed=force_retry_failed)
        self.log_message("已恢复所有暂停和失败的任务")

    def clear_log(self) -> None:
        self.log_area.clear()
        self.log_message("日志已清空")

    def _cleanup_all_tasks_completely(self) -> None:
        try:
            total_temp_files_deleted = 0
            cleaned_tasks = 0
            for task_id, task_info in list(self.task_manager.task_info_map.items()):
                worker = task_info.get('worker')
                if worker:
                    try:
                        self._cleanup_task_completely(worker, task_id)
                        temp_files_count = self._count_temporary_files(worker, task_id)
                        total_temp_files_deleted += temp_files_count
                        cleaned_tasks += 1
                    except Exception as e:
                        self.log_message(f"清理任务 {task_id[:8]} 时出错: {str(e)}", "WARNING")
                        logger.warning(f"清理任务 {task_id} 时出错", exc_info=True)

            global_temp_deleted = self._delete_global_temporary_files()
            total_temp_files_deleted += global_temp_deleted

            if cleaned_tasks > 0:
                self.log_message(f"共清理 {cleaned_tasks} 个任务的资源")
            if total_temp_files_deleted > 0:
                self.log_message(f"共清理 {total_temp_files_deleted} 个临时文件")
        except Exception as e:
            self.log_message(f"彻底清理所有任务资源时出错: {str(e)}", "ERROR")
            logger.error("彻底清理所有任务资源时出错", exc_info=True)

    def _count_temporary_files(self, worker: DownloadWorker, task_id: str) -> int:
        try:
            count = 0
            download_dir = getattr(worker, 'download_dir', None)
            if not download_dir:
                if self.task_logger and task_id:
                    all_tasks = self.task_logger.get_all_tasks()
                    if task_id in all_tasks:
                        download_dir = all_tasks[task_id].get('download_dir')
            if download_dir and os.path.exists(download_dir):
                try:
                    files = os.listdir(download_dir)
                    count = len([f for f in files if f.startswith('下载中_') and f.endswith('.mp4')])
                except Exception:
                    pass
            return count
        except Exception:
            return 0

    def _delete_global_temporary_files(self) -> int:
        try:
            deleted_count = 0
            if os.path.exists(self.download_dir):
                try:
                    files = os.listdir(self.download_dir)
                    for filename in files:
                        if filename.startswith('下载中_') and filename.endswith('.mp4'):
                            temp_file_path = os.path.join(self.download_dir, filename)
                            try:
                                os.remove(temp_file_path)
                                deleted_count += 1
                                self.log_message(f"已删除全局临时文件: {filename}")
                            except Exception as e:
                                self.log_message(f"删除全局临时文件失败 {filename}: {str(e)}", "WARNING")
                except Exception as e:
                    self.log_message(f"扫描全局临时文件时出错: {str(e)}", "WARNING")
            return deleted_count
        except Exception as e:
            self.log_message(f"删除全局临时文件时出错: {str(e)}", "ERROR")
            return 0

    def clear_all_tasks(self) -> None:
        try:
            self.stop_all_tasks()
            self._delete_global_temporary_files()
            try:
                if self.task_logger:
                    self.task_logger.clear_all_tasks()
            except Exception as e:
                logger.error(f"清空TaskLogger时出错: {e}")

            while self.tasks_layout.count():
                child = self.tasks_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            self.task_manager.task_info_map.clear()

            self.log_message("已清空所有任务")
        except Exception as e:
            logger.error(f"清空所有任务时出错: {e}")
            self.log_message(f"清空任务失败: {str(e)}")

    def create_task_frame(self, url: str, worker: DownloadWorker, is_resume: bool = False) -> QFrame:
        task_frame = QFrame()
        task_frame.setFrameShape(QFrame.StyledPanel)
        task_layout = QVBoxLayout(task_frame)
        task_layout.setSpacing(8)
        task_layout.setContentsMargins(10, 10, 10, 10)

        first_row_layout = QHBoxLayout()
        url_text = url
        if len(url) > 50:
            url_text = url[:47] + "..."
        task_label = QLabel(f"URL: {url_text}")
        task_label.setStyleSheet("color: #ecf0f1; font-weight: bold; font-size: 20px;")
        task_label.setWordWrap(True)
        first_row_layout.addWidget(task_label, 1)

        progress_count_label = QLabel("0/0")
        progress_count_label.setObjectName("progress_count_label")
        progress_count_label.setStyleSheet("color: #3498db; font-weight: bold; font-size: 20px; padding: 0 10px;")
        progress_count_label.setAlignment(Qt.AlignCenter)
        first_row_layout.addWidget(progress_count_label)

        status_text = "状态: 已暂停" if is_resume else "状态: 待处理"
        status_color = "#f39c12" if is_resume else "#7f8c8d"
        status_label = QLabel(status_text)
        status_label.setObjectName("status_label")
        status_label.setStyleSheet(f"color: {status_color}; font-weight: bold; font-size: 20px;")
        first_row_layout.addWidget(status_label)

        task_layout.addLayout(first_row_layout)

        progress_frame = QFrame()
        progress_frame.setObjectName("progress_frame")
        progress_layout = QHBoxLayout(progress_frame)
        progress_layout.setSpacing(20)
        progress_layout.setContentsMargins(5, 5, 5, 5)

        left_progress = QLabel("等待开始...")
        left_progress.setObjectName("left_progress")
        left_progress.setStyleSheet("color: #3498db; font-weight: bold; font-size: 20px;")
        left_progress.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        left_progress.setWordWrap(True)
        left_progress.setMinimumHeight(30)
        progress_layout.addWidget(left_progress, 1)

        right_progress = QLabel("")
        right_progress.setObjectName("right_progress")
        right_progress.setStyleSheet("color: #3498db; font-weight: bold; font-size: 20px;")
        right_progress.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        right_progress.setWordWrap(True)
        right_progress.setMinimumHeight(30)
        progress_layout.addWidget(right_progress, 1)

        task_layout.addWidget(progress_frame)

        button_layout = QHBoxLayout()

        resume_btn = QPushButton("继续")
        resume_btn.setObjectName("resume_btn")
        resume_btn.setEnabled(is_resume)
        resume_btn.setStyleSheet("background-color: #7f8c8d;" if not is_resume else "")
        resume_btn.clicked.connect(lambda: self.resume_task(worker, force_retry=True))
        button_layout.addWidget(resume_btn)

        pause_btn = QPushButton("暂停")
        pause_btn.setObjectName("pause_btn")
        pause_btn.setEnabled(not is_resume)
        pause_btn.setStyleSheet("" if not is_resume else "background-color: #7f8c8d;")
        pause_btn.clicked.connect(lambda: self.pause_task(worker))
        button_layout.addWidget(pause_btn)

        stop_btn = QPushButton("停止")
        stop_btn.setObjectName("stop_btn")   # 为停止按钮设置对象名
        stop_btn.clicked.connect(lambda: self.stop_task(worker))
        button_layout.addWidget(stop_btn)

        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(lambda: self.delete_task(task_frame, worker))
        button_layout.addWidget(delete_btn)

        task_layout.addLayout(button_layout)

        self.tasks_layout.addWidget(task_frame)

        return task_frame

    def resume_task(self, worker: DownloadWorker, force_retry=False):
        task_id = None
        task_info = None
        for tid, info in self.task_manager.task_info_map.items():
            if info['worker'] == worker:
                task_id = tid
                task_info = info
                break

        if not task_info:
            self.log_message("未找到要恢复的任务")
            return

        # 如果任务是已停止状态，则重新创建 worker 并替换（清空所有状态重新开始）
        if task_info['status'] == TaskManager.STATUS_STOPPED:
            self.log_message(f"任务已停止，正在清空所有状态并重新启动...")
            # 获取原任务信息
            url = task_info['url']
            download_dir = task_info['worker'].download_dir
            # 删除旧的 task_frame 和 worker（从 UI 和映射中移除）
            self.delete_task(task_info['task_frame'], task_info['worker'])
            # 以 PENDING 状态重新创建任务，使用当前配置（完全重新开始）
            self.restore_task(task_id, url, download_dir, status=TaskManager.STATUS_PENDING)
            self.log_message(f"任务已清空所有状态并重新开始 (ID: {task_id[:8]})")
            return

        # 原有逻辑：处理暂停或失败的任务
        self.task_manager.resume_task(worker, force_retry=force_retry)
        self.log_message(f"已恢复任务 (ID: {task_id[:8]})")

    def pause_task(self, worker: DownloadWorker):
        self.task_manager.pause_task(worker)
        self.log_message(f"已暂停任务")

    def stop_task(self, worker: DownloadWorker):
        task_id = None
        task_info = None
        for tid, info in self.task_manager.task_info_map.items():
            if info['worker'] == worker:
                task_id = tid
                task_info = info
                break

        if task_info:
            # 停止任务并清理资源
            self.task_manager.stop_task(worker)
            self._cleanup_task_completely(worker, task_id)
            
            # 清空任务的所有状态和信息
            self._clear_task_state_completely(task_id)
            
            task_info['status'] = TaskManager.STATUS_STOPPED
            self.task_manager._set_task_ui_stopped(task_info['task_frame'])
            self.log_message(f"已停止任务，所有状态和信息已清空")
        else:
            self.log_message("未找到要停止的任务")

    def stop_all_tasks(self):
        has_tasks_to_stop = bool(self.task_manager.task_info_map)

        if has_tasks_to_stop:
            self.log_message("正在停止所有任务，请稍候...")
            # 先停止所有任务
            self.task_manager.stop_all_tasks()
            # 然后清理每个任务的资源和状态
            for task_id, task_info in list(self.task_manager.task_info_map.items()):
                self._cleanup_task_completely(task_info['worker'], task_id)
                self._clear_task_state_completely(task_id)
            self.log_message("已停止所有下载任务，所有状态和信息已清空")
        else:
            self.log_message("没有可停止的任务")

    def _clear_task_state_completely(self, task_id: str) -> None:
        """清空任务的所有状态和信息"""
        try:
            if self.task_logger and task_id:
                # 从 TaskLogger 中删除任务
                self.task_logger.remove_task(task_id)
                self.log_message(f"已从 TaskLogger 删除任务 {task_id[:8]}")
        except Exception as e:
            logger.error(f"清空任务状态时出错：{e}")
            self.log_message(f"清空任务状态时出错：{str(e)}", "ERROR")
    
    def _cleanup_task_completely(self, worker: DownloadWorker, task_id: str) -> None:
        try:
            if hasattr(worker, 'stop'):
                worker.stop()
            if worker.isRunning():
                worker.wait(5000)
    
            self._close_browser_instances(worker)
            self._delete_temporary_files(worker, task_id)
    
            if hasattr(worker, 'scraper') and worker.scraper:
                if hasattr(worker.scraper, 'downloading_files'):
                    worker.scraper.downloading_files.clear()
                if hasattr(worker.scraper, 'failed_links'):
                    worker.scraper.failed_links.clear()
                if hasattr(worker.scraper, 'results'):
                    worker.scraper.results.clear()
    
            self.log_message(f"任务 {task_id[:8]} 的资源已彻底清理")
        except Exception as e:
            self.log_message(f"清理任务资源时出错：{str(e)}", "ERROR")
            logger.error("清理任务资源时出错", exc_info=True)

    def _close_browser_instances(self, worker: DownloadWorker) -> None:
        try:
            if hasattr(worker, 'scraper') and worker.scraper:
                if hasattr(worker.scraper, 'close_all_browsers'):
                    import asyncio
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(worker.scraper.close_all_browsers())
                        loop.close()
                    except Exception as e:
                        logger.error(f"关闭浏览器管理器时出错: {e}")
        except Exception as e:
            logger.error(f"关闭浏览器实例时出错: {e}")

    def _delete_temporary_files(self, worker: DownloadWorker, task_id: str) -> None:
        try:
            download_dir = getattr(worker, 'download_dir', None)
            if not download_dir:
                if self.task_logger and task_id:
                    all_tasks = self.task_logger.get_all_tasks()
                    if task_id in all_tasks:
                        download_dir = all_tasks[task_id].get('download_dir')

            if download_dir and os.path.exists(download_dir):
                temp_files_deleted = 0
                try:
                    files = os.listdir(download_dir)
                    for filename in files:
                        if filename.startswith('下载中_') and filename.endswith('.mp4'):
                            temp_file_path = os.path.join(download_dir, filename)
                            try:
                                os.remove(temp_file_path)
                                temp_files_deleted += 1
                                self.log_message(f"已删除临时文件: {filename}")
                            except Exception as e:
                                self.log_message(f"删除临时文件失败 {filename}: {str(e)}", "WARNING")
                except Exception as e:
                    self.log_message(f"扫描临时文件时出错: {str(e)}", "WARNING")

                if temp_files_deleted > 0:
                    self.log_message(f"共删除 {temp_files_deleted} 个临时文件")
        except Exception as e:
            self.log_message(f"删除临时文件时出错: {str(e)}", "ERROR")

    def delete_task(self, task_frame: QFrame, worker: DownloadWorker):
        try:
            task_id = None
            for tid, info in self.task_manager.task_info_map.items():
                if info['worker'] == worker:
                    task_id = tid
                    break

            if task_id:
                self._cleanup_task_completely(worker, task_id)

            try:
                self.tasks_layout.removeWidget(task_frame)
                task_frame.deleteLater()
            except Exception as e:
                logger.error(f"从UI移除任务框架时出错: {e}")

            try:
                self.task_manager.remove_task(worker)
                self.log_message(f"已删除任务")
            except Exception as e:
                logger.error(f"从任务管理器移除任务时出错: {e}")
                self.log_message(f"删除任务时出错: {str(e)}")

        except Exception as e:
            logger.error(f"删除任务时发生未知错误: {e}")
            self.log_message(f"删除任务失败: {str(e)}")

    def change_download_path(self) -> None:
        new_path = QFileDialog.getExistingDirectory(
            self,
            "选择下载目录",
            self.download_dir,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )

        if new_path:
            self.download_dir = new_path
            self.download_path_label.setText(f"当前下载路径: {self.download_dir}")
            self.log_message(f"下载路径已更新为: {self.download_dir}")
            self.config_manager.set("download_dir", self.download_dir)

    def open_download_directory(self) -> None:
        try:
            import platform
            import os

            current_download_dir = self.download_dir
            if not os.path.exists(current_download_dir):
                os.makedirs(current_download_dir, exist_ok=True)
                self.log_message(f"创建下载目录: {current_download_dir}")
                dir_step = self.start_step_log("main", "创建下载目录")
                self.complete_step_log(dir_step, {"目录路径": current_download_dir})

            open_step = self.start_step_log("main", "打开下载目录")
            system = platform.system()
            if system == "Windows":
                os.startfile(current_download_dir)
            elif system == "Darwin":  # macOS
                import subprocess
                subprocess.Popen(["open", current_download_dir])
            else:  # Linux
                import subprocess
                subprocess.Popen(["xdg-open", current_download_dir])

            self.complete_step_log(open_step, {"目标目录": current_download_dir})
            self.log_message(f"已打开下载目录: {current_download_dir}")

        except Exception as e:
            self.log_message(f"打开目录失败: {str(e)}", "ERROR")
            if 'open_step' in locals():
                self.fail_step_log(open_step, str(e))

    def _check_latest_headless_setting(self) -> None:
        try:
            latest_headless = self.config_manager.get("headless_mode", True)
            if self.headless_mode != latest_headless:
                self.log_message(f"检测到无头模式设置更新: {self.headless_mode} -> {latest_headless}")
                self.headless_mode = latest_headless
                self.headless_checkbox.setChecked(self.headless_mode)
        except Exception as e:
            self.log_message(f"检查无头模式设置时出错: {str(e)}，使用当前设置: {self.headless_mode}")

    def closeEvent(self, event):
        logger.info("程序正在关闭...")
        pos = self.pos()
        size = self.size()
        window_pos = [pos.x(), pos.y()]
        window_size = [size.width(), size.height()]
        self.config_manager.set("window_position", window_pos)
        self.config_manager.set("window_size", window_size)
        logger.info("已保存窗口配置")

        if hasattr(self, 'task_manager'):
            logger.info("正在停止所有下载任务...")
            self.task_manager.stop_all_tasks()
            import time
            time.sleep(1)
            logger.info("任务已停止")

        logger.info("程序关闭完成")
        event.accept()


def run_gui():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())