import os
import sys
import time
import uuid

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QTextEdit, QGroupBox,
                             QScrollArea, QFrame, QFileDialog, QCheckBox)

from ToolPart.DownLoad import DownloadWorker
from ToolPart.Config import ConfigManager
from ToolPart.TaskLogger import TaskLogger


def _update_task_ui_status(task_frame, status_text, status_color,
                           pause_enabled=True, pause_style="",
                           resume_enabled=False, resume_style=""):
    """更新任务UI状态的通用方法"""
    # 更新状态标签
    status_label = task_frame.findChild(QLabel, "status_label")
    if status_label:
        status_label.setText(status_text)
        status_label.setStyleSheet(f"color: {status_color};")

    # 更新暂停按钮
    pause_btn = task_frame.findChild(QPushButton, "pause_btn")
    if pause_btn:
        pause_btn.setEnabled(pause_enabled)
        if pause_style:
            pause_btn.setStyleSheet(pause_style)
        else:
            pause_btn.setStyleSheet("")

    # 更新继续按钮
    resume_btn = task_frame.findChild(QPushButton, "resume_btn")
    if resume_btn:
        resume_btn.setEnabled(resume_enabled)
        if resume_style:
            resume_btn.setStyleSheet(resume_style)
        else:
            resume_btn.setStyleSheet("")


class TaskManager:
    """任务管理器，控制并发数量"""

    def __init__(self, max_active_tasks=2, task_logger=None):
        self.max_active_tasks = max_active_tasks
        self.active_tasks = []  # 正在运行的任务
        self.pending_tasks = []  # 待处理的任务
        self.paused_tasks = []  # 暂停的任务
        self.task_info_map = {}  # 存储任务信息映射
        self.task_logger = task_logger  # TaskLogger 实例

    def add_task(self, worker, task_frame, url, task_id=None, is_resume=False):
        """添加新任务"""
        if task_id is None:
            task_id = str(uuid.uuid4())

        task_info = {
            'worker': worker,
            'task_frame': task_frame,
            'url': url,
            'task_id': task_id,
            'status': 'pending'  # pending, active, paused, finished
        }

        # 添加到待处理队列
        self.pending_tasks.append(task_info)
        self.task_info_map[task_id] = task_info

        # 记录到TaskLogger
        if self.task_logger and not is_resume:
            self.task_logger.add_task(task_id, url, worker.download_dir)

        # 尝试启动任务
        self._try_start_pending_tasks()

        return task_id

    def _try_start_pending_tasks(self):
        """尝试启动待处理的任务"""
        while len(self.active_tasks) < self.max_active_tasks and self.pending_tasks:
            task_info = self.pending_tasks.pop(0)
            self.active_tasks.append(task_info)
            task_info['status'] = 'active'
            task_info['worker'].start()

            # 更新TaskLogger为运行状态
            if self.task_logger:
                self.task_logger.update_task_status(task_info['task_id'], 'running')

            # 更新任务状态显示
            status_label = task_info['task_frame'].findChild(QLabel, "status_label")
            if status_label:
                status_label.setText("状态: 运行中")
                status_label.setStyleSheet("color: #2ecc71;")

            # 更新暂停按钮状态
            pause_btn = task_info['task_frame'].findChild(QPushButton, "pause_btn")
            if pause_btn:
                pause_btn.setEnabled(True)
                pause_btn.setStyleSheet("")

    def pause_task(self, worker):
        """暂停指定任务"""
        # 通过worker找到task_id
        task_id = None
        for tid, task_info in self.task_info_map.items():
            if task_info['worker'] == worker:
                task_id = tid
                break

        if task_id and task_id in self.task_info_map:
            task_info = self.task_info_map[task_id]
            if task_info['status'] == 'active':
                task_info['worker'].pause()
                task_info['status'] = 'paused'

                # 更新TaskLogger
                if self.task_logger:
                    self.task_logger.update_task_status(task_id, 'paused')

                # 更新任务状态显示
                status_label = task_info['task_frame'].findChild(QLabel, "status_label")
                if status_label:
                    status_label.setText("状态: 已暂停")
                    status_label.setStyleSheet("color: #f39c12;")

                # 更新暂停按钮状态
                pause_btn = task_info['task_frame'].findChild(QPushButton, "pause_btn")
                if pause_btn:
                    pause_btn.setEnabled(False)  # 暂停按钮变灰
                    pause_btn.setStyleSheet("background-color: #7f8c8d;")

                # 启用继续按钮
                resume_btn = task_info['task_frame'].findChild(QPushButton, "resume_btn")
                if resume_btn:
                    resume_btn.setEnabled(True)
                    resume_btn.setStyleSheet("")

                # 从活跃任务中移除，加入暂停队列
                if task_info in self.active_tasks:
                    self.active_tasks.remove(task_info)
                    self.paused_tasks.append(task_info)
                    # 从暂停任务中恢复一个任务
                    self._try_start_pending_tasks()

    def resume_task(self, worker):
        """恢复指定任务"""
        # 通过worker找到task_id
        task_id = None
        for tid, task_info in self.task_info_map.items():
            if task_info['worker'] == worker:
                task_id = tid
                break

        if task_id and task_id in self.task_info_map:
            task_info = self.task_info_map[task_id]
            if task_info['status'] == 'paused':
                task_info['worker'].resume()
                task_info['status'] = 'active'

                # 更新TaskLogger
                if self.task_logger:
                    self.task_logger.update_task_status(task_id, 'running')

                # 更新任务状态显示
                status_label = task_info['task_frame'].findChild(QLabel, "status_label")
                if status_label:
                    status_label.setText("状态: 运行中")
                    status_label.setStyleSheet("color: #2ecc71;")

                # 更新暂停按钮状态
                pause_btn = task_info['task_frame'].findChild(QPushButton, "pause_btn")
                if pause_btn:
                    pause_btn.setEnabled(True)
                    pause_btn.setStyleSheet("background-color: #3498db;")

                # 恢复任务后，由于任务正在运行，继续按钮应保持禁用
                resume_btn = task_info['task_frame'].findChild(QPushButton, "resume_btn")
                if resume_btn:
                    resume_btn.setEnabled(False)
                    resume_btn.setStyleSheet("background-color: #7f8c8d;")

                # 将任务从暂停队列移到活跃队列
                if task_info in self.paused_tasks:
                    self.paused_tasks.remove(task_info)

                # 只有当活跃任务数小于最大限制时才真正开始
                if len(self.active_tasks) < self.max_active_tasks:
                    self.active_tasks.append(task_info)
                    task_info['worker'].start()  # 重新启动线程
                else:
                    # 如果达到最大活跃任务数，放入待处理队列
                    self.pending_tasks.append(task_info)
                    task_info['status'] = 'pending'

    def remove_task(self, worker):
        """删除指定任务"""
        try:
            # 通过worker找到task_id
            task_id = None
            for tid, task_info in self.task_info_map.items():
                if task_info['worker'] == worker:
                    task_id = tid
                    break

            if task_id and task_id in self.task_info_map:
                task_info = self.task_info_map[task_id]

                # 停止任务
                try:
                    task_info['worker'].stop()
                    # 等待线程停止
                    if task_info['worker'].isRunning():
                        task_info['worker'].wait(3000)  # 等待最多3秒
                except Exception as e:
                    print(f"停止任务线程时出错: {e}")

                # 从TaskLogger中删除
                try:
                    if self.task_logger:
                        self.task_logger.remove_task(task_id)
                except Exception as e:
                    print(f"从TaskLogger删除任务时出错: {e}")

                # 从各个列表中移除
                try:
                    if task_info in self.active_tasks:
                        self.active_tasks.remove(task_info)
                    elif task_info in self.pending_tasks:
                        self.pending_tasks.remove(task_info)
                    elif task_info in self.paused_tasks:
                        self.paused_tasks.remove(task_info)
                except Exception as e:
                    print(f"从任务列表移除时出错: {e}")

                # 删除映射
                try:
                    del self.task_info_map[task_id]
                except Exception as e:
                    print(f"删除任务映射时出错: {e}")

                # 从暂停任务中恢复一个任务
                try:
                    self._try_start_pending_tasks()
                except Exception as e:
                    print(f"尝试启动待处理任务时出错: {e}")
                    
        except Exception as e:
            print(f"删除任务时发生未知错误: {e}")

    def pause_all_tasks(self):
        """暂停所有任务"""
        # 收集所有需要暂停的任务
        tasks_to_pause = []
        
        # 先收集所有活跃任务
        for task_info in self.active_tasks[:]:  # 使用副本避免修改时出现问题
            if task_info['status'] == 'active':
                tasks_to_pause.append(task_info)
        
        # 暂停所有收集到的任务
        for task_info in tasks_to_pause:
            task_info['worker'].pause()
            task_info['status'] = 'paused'

            # 更新TaskLogger
            if self.task_logger:
                self.task_logger.update_task_status(task_info['task_id'], 'paused')

            # 更新任务状态显示
            status_label = task_info['task_frame'].findChild(QLabel, "status_label")
            if status_label:
                status_label.setText("状态: 已暂停")
                status_label.setStyleSheet("color: #f39c12;")

            # 更新暂停按钮状态
            pause_btn = task_info['task_frame'].findChild(QPushButton, "pause_btn")
            if pause_btn:
                pause_btn.setEnabled(False)  # 暂停按钮变灰
                pause_btn.setStyleSheet("background-color: #7f8c8d;")

            # 启用继续按钮
            resume_btn = task_info['task_frame'].findChild(QPushButton, "resume_btn")
            if resume_btn:
                resume_btn.setEnabled(True)
                resume_btn.setStyleSheet("")

        # 移动活跃任务到暂停任务列表
        for task_info in tasks_to_pause:
            if task_info in self.active_tasks:
                self.active_tasks.remove(task_info)
                self.paused_tasks.append(task_info)

    def resume_all_tasks(self):
        """恢复所有暂停的任务"""
        # 收集所有需要恢复的任务
        tasks_to_resume = []
        
        # 先收集所有暂停任务
        for task_info in self.paused_tasks[:]:  # 使用副本避免修改时出现问题
            if task_info['status'] == 'paused':
                tasks_to_resume.append(task_info)
        
        # 恢复所有收集到的任务
        for task_info in tasks_to_resume:
            task_info['worker'].resume()
            task_info['status'] = 'active'

            # 更新TaskLogger
            if self.task_logger:
                self.task_logger.update_task_status(task_info['task_id'], 'running')

            # 更新任务UI状态
            _update_task_ui_status(
                task_info['task_frame'],
                "状态: 运行中",
                "#2ecc71",
                pause_enabled=True,
                pause_style="background-color: #3498db;",
                resume_enabled=False,
                resume_style="background-color: #7f8c8d;"
            )

            # 将任务从暂停队列移到活跃队列
            if task_info in self.paused_tasks:
                self.paused_tasks.remove(task_info)
            
            # 只有当活跃任务数小于最大限制时才真正开始
            if len(self.active_tasks) < self.max_active_tasks:
                self.active_tasks.append(task_info)
                # 重新启动线程（因为暂停时线程可能已经结束）
                if not task_info['worker'].isRunning():
                    task_info['worker'].start()
            else:
                # 如果达到最大活跃任务数，放入待处理队列
                self.pending_tasks.append(task_info)
                task_info['status'] = 'pending'

        # 清空暂停任务列表
        self.paused_tasks.clear()

    def stop_all_tasks(self):
        """停止所有任务（包括未完成的）"""
        print(f"停止所有任务 - 活跃: {len(self.active_tasks)}, 待处理: {len(self.pending_tasks)}, 暂停: {len(self.paused_tasks)}")
        
        # 停止所有活跃任务
        for task_info in self.active_tasks[:]:  # 使用副本避免迭代时修改
            print(f"停止活跃任务: {task_info['task_id']}")
            task_info['worker'].stop()
            # 确保线程真正停止
            if task_info['worker'].isRunning():
                task_info['worker'].wait(3000)  # 等待最多3秒

        # 停止所有待处理任务
        for task_info in self.pending_tasks[:]:
            print(f"停止待处理任务: {task_info['task_id']}")
            task_info['worker'].stop()

        # 停止所有暂停任务
        for task_info in self.paused_tasks[:]:
            print(f"停止暂停任务: {task_info['task_id']}")
            task_info['worker'].stop()
            # 唤醒可能处于暂停状态的线程
            task_info['worker'].resume()
            if task_info['worker'].isRunning():
                task_info['worker'].wait(3000)

    def clear_all_tasks(self):
        """清空所有任务"""
        try:
            # 停止所有任务
            self.stop_all_tasks()

            # 清空TaskLogger中的所有任务
            try:
                if self.task_logger:
                    self.task_logger.clear_all_tasks()
            except Exception as e:
                print(f"清空TaskLogger时出错: {e}")

            # 清空所有列表
            try:
                self.active_tasks.clear()
                self.pending_tasks.clear()
                self.paused_tasks.clear()
                self.task_info_map.clear()
            except Exception as e:
                print(f"清空任务列表时出错: {e}")
                
        except Exception as e:
            print(f"清空所有任务时发生未知错误: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # 初始化配置管理器
        self.status_bar = None
        self.log_area = None
        self.tasks_container = None
        self.tasks_layout = None
        self.tasks_scroll = None
        self.clear_all_btn = None
        self.pause_btn = None
        self.download_btn = None
        self.headless_checkbox = None
        self.url_input = None
        self.download_path_label = None
        self.config_manager = ConfigManager()

        # 初始化TaskLogger
        self.task_logger = TaskLogger()

        # 从配置中读取窗口位置和大小
        window_pos = self.config_manager.get("window_position", [100, 100])
        window_size = self.config_manager.get("window_size", [1000, 800])

        # 设置窗口位置和大小
        self.setGeometry(window_pos[0], window_pos[1], window_size[0], window_size[1])

        self.setWindowTitle("Hanime视频下载器")

        # 样式表
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

        # 成员变量
        self.download_dir = self.config_manager.get("download_dir", os.path.join(os.getcwd(), "Download"))
        self.headless_mode = self.config_manager.get("headless_mode", True)

        # 使用任务管理器
        self.task_manager = TaskManager(max_active_tasks=2, task_logger=self.task_logger)

        # 初始化UI
        self.init_ui()

        # 启动时恢复未完成的任务
        self.restore_incomplete_tasks()

    def restore_incomplete_tasks(self):
        """恢复未完成的任务"""
        incomplete_tasks = self.task_logger.get_incomplete_tasks()

        if incomplete_tasks:
            self.log_message(f"找到 {len(incomplete_tasks)} 个未完成的任务，正在恢复...")

            for task_id, task_info in incomplete_tasks.items():
                url = task_info.get("url", "")
                download_dir = task_info.get("download_dir", self.download_dir)

                if url:
                    # 创建恢复的任务
                    self.restore_task(task_id, url, download_dir)

        self.log_message("任务恢复完成")

    def restore_task(self, task_id: str, url: str, download_dir: str):
        """恢复一个暂停的任务"""
        # 创建下载工作线程
        worker = DownloadWorker(url, download_dir, self.headless_mode)
        worker.log_signal.connect(self.log_message)
        worker.finished_signal.connect(self.on_download_finished)

        # 创建任务显示框
        task_frame = self.create_task_frame(url, worker, is_resume=True)

        # 添加到任务管理器（标记为恢复的任务）
        self.task_manager.add_task(worker, task_frame, url, task_id=task_id, is_resume=True)

        # 设置任务状态为暂停
        self.task_manager.pause_task(worker)

        self.log_message(f"已恢复任务: {url}")

    def init_ui(self):
        """初始化用户界面"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(10)
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

        self.pause_btn = QPushButton("暂停下载")
        self.pause_btn.clicked.connect(self.pause_download)
        button_layout.addWidget(self.pause_btn)

        self.clear_all_btn = QPushButton("删除全部")
        self.clear_all_btn.clicked.connect(self.clear_all_tasks)
        button_layout.addWidget(self.clear_all_btn)

        input_layout.addLayout(button_layout)
        main_layout.addWidget(input_group)

        # 日志区域
        log_group = QGroupBox("下载日志")
        log_layout = QVBoxLayout(log_group)
        log_group.setMaximumHeight(200)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        log_layout.addWidget(self.log_area)

        main_layout.addWidget(log_group)

        # 任务列表区域
        tasks_group = QGroupBox("活动下载任务")
        tasks_layout = QVBoxLayout(tasks_group)

        self.tasks_scroll = QScrollArea()
        self.tasks_scroll.setWidgetResizable(True)
        self.tasks_container = QWidget()
        self.tasks_container.setObjectName("tasks_container")
        self.tasks_layout = QVBoxLayout(self.tasks_container)
        self.tasks_layout.setAlignment(Qt.AlignTop)
        self.tasks_layout.setSpacing(5)
        self.tasks_layout.setContentsMargins(5, 5, 5, 5)

        self.tasks_scroll.setWidget(self.tasks_container)
        tasks_layout.addWidget(self.tasks_scroll)

        main_layout.addWidget(tasks_group, 1)

        # 状态栏
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("就绪")

    def on_headless_changed(self, state: int) -> None:
        """无头模式复选框状态改变"""
        self.headless_mode = (state == Qt.Checked)

        if self.headless_mode:
            self.log_message("已启用无头模式（不显示浏览器界面）")
        else:
            self.log_message("已禁用无头模式（将显示浏览器界面）")

        # 自动保存配置
        self.config_manager.set("headless_mode", self.headless_mode)

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
        timestamp = time.strftime("%H:%M:%S")
        self.log_area.append(f"[{timestamp}] {message}")
        # 自动滚动到底部
        self.log_area.verticalScrollBar().setValue(
            self.log_area.verticalScrollBar().maximum()
        )

    def start_download(self) -> None:
        """开始新的下载任务"""
        url = self.url_input.text().strip()

        # 如果输入框为空，视为继续所有下载
        if not url:
            self.resume_all_tasks()
            return

        self.url_input.clear()

        # 创建下载工作线程
        worker = DownloadWorker(url, self.download_dir, self.headless_mode)
        worker.log_signal.connect(self.log_message)
        worker.finished_signal.connect(self.on_download_finished)

        # 创建任务显示框
        task_frame = self.create_task_frame(url, worker)

        # 添加到任务管理器
        self.task_manager.add_task(worker, task_frame, url)

        self.log_message(f"已添加下载任务: {url} (状态: 待处理)")

    def on_download_finished(self, success: bool) -> None:
        """下载完成处理"""
        sender = self.sender()

        # 找到对应的线程和任务
        task_id = None
        for tid, task_info in self.task_manager.task_info_map.items():
            if task_info['worker'] == sender:
                task_id = tid
                break

        if task_id:
            task_info = self.task_manager.task_info_map[task_id]
            url = task_info['url']

            if success:
                self.log_message(f"下载任务完成: {url}")

                # 更新TaskLogger
                if self.task_manager.task_logger:
                    self.task_manager.task_logger.update_task_status(task_id, 'completed')

                # 更新任务状态
                status_label = task_info['task_frame'].findChild(QLabel, "status_label")
                if status_label:
                    status_label.setText("状态: 已完成")
                    status_label.setStyleSheet("color: #2ecc71;")
            else:
                self.log_message(f"下载任务失败: {url}")

                # 更新TaskLogger
                if self.task_manager.task_logger:
                    self.task_manager.task_logger.update_task_status(task_id, 'failed')

                # 更新任务状态
                status_label = task_info['task_frame'].findChild(QLabel, "status_label")
                if status_label:
                    status_label.setText("状态: 失败")
                    status_label.setStyleSheet("color: #e74c3c;")

                # 下载失败后，任务应该可以重新开始
                # 启用继续按钮，禁用暂停按钮
                resume_btn = task_info['task_frame'].findChild(QPushButton, "resume_btn")
                if resume_btn:
                    resume_btn.setEnabled(True)
                    resume_btn.setStyleSheet("")
                
                pause_btn = task_info['task_frame'].findChild(QPushButton, "pause_btn")
                if pause_btn:
                    pause_btn.setEnabled(False)
                    pause_btn.setStyleSheet("background-color: #7f8c8d;")
                
                # 将任务状态设置为暂停，允许用户重新开始
                task_info['status'] = 'paused'

            # 从任务管理器中移除已完成的任务
            # 注意：我们不从task_info_map中删除，因为用户可能还需要查看任务记录
            # 只是从活动任务列表中移除
            if task_info in self.task_manager.active_tasks:
                self.task_manager.active_tasks.remove(task_info)
            elif task_info in self.task_manager.pending_tasks:
                self.task_manager.pending_tasks.remove(task_info)
            elif task_info in self.task_manager.paused_tasks:
                self.task_manager.paused_tasks.remove(task_info)

            # 尝试启动等待中的任务
            self.task_manager._try_start_pending_tasks()

    def pause_download(self) -> None:
        """暂停所有下载任务"""
        self.task_manager.pause_all_tasks()
        self.log_message("已暂停所有下载任务")

    def resume_all_tasks(self) -> None:
        """恢复所有任务"""
        # 检查是否有暂停的任务
        if self.task_manager.paused_tasks:
            self.task_manager.resume_all_tasks()
            self.log_message("已恢复所有暂停的任务")
        else:
            self.log_message("没有暂停的任务需要恢复")

    def clear_all_tasks(self) -> None:
        """清空所有任务"""
        try:
            # 停止所有正在运行的任务
            self.task_manager.clear_all_tasks()
            
            # 清空UI中的任务显示
            try:
                while self.tasks_layout.count():
                    child = self.tasks_layout.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
            except Exception as e:
                print(f"清空UI任务显示时出错: {e}")
                
            self.log_message("已清空所有任务")
        except Exception as e:
            print(f"清空所有任务时出错: {e}")
            self.log_message(f"清空任务失败: {str(e)}")

    def create_task_frame(self, url: str, worker: DownloadWorker, is_resume: bool = False) -> QFrame:
        """创建任务显示框"""
        task_frame = QFrame()
        task_frame.setFrameShape(QFrame.StyledPanel)
        task_layout = QVBoxLayout(task_frame)

        # 任务标签
        task_label = QLabel(f"任务: {url}")
        task_label.setStyleSheet("color: #ecf0f1; font-weight: bold;")
        task_label.setWordWrap(True)
        task_layout.addWidget(task_label)

        # 状态标签
        status_text = "状态: 暂停中" if is_resume else "状态: 待处理"
        status_label = QLabel(status_text)
        status_label.setObjectName("status_label")
        status_label.setStyleSheet("color: #7f8c8d;" if not is_resume else "color: #f39c12;")
        task_layout.addWidget(status_label)

        # 按钮布局
        button_layout = QHBoxLayout()

        # 继续按钮
        resume_btn = QPushButton("继续")
        resume_btn.setObjectName("resume_btn")  # 设置对象名称以便查找
        resume_btn.setEnabled(is_resume)  # 如果是恢复的任务，继续按钮可用
        resume_btn.setStyleSheet("background-color: #7f8c8d;" if not is_resume else "")
        resume_btn.clicked.connect(lambda: self.resume_task(worker))
        button_layout.addWidget(resume_btn)

        # 暂停按钮
        pause_btn = QPushButton("暂停")
        pause_btn.setObjectName("pause_btn")  # 设置对象名称以便查找
        pause_btn.setEnabled(not is_resume)  # 如果是恢复的任务，暂停按钮不可用
        pause_btn.setStyleSheet("" if not is_resume else "background-color: #7f8c8d;")
        pause_btn.clicked.connect(lambda: self.pause_task(worker))
        button_layout.addWidget(pause_btn)

        # 删除按钮
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(lambda: self.delete_task(task_frame, worker))
        button_layout.addWidget(delete_btn)

        task_layout.addLayout(button_layout)

        # 添加到任务布局
        self.tasks_layout.addWidget(task_frame)

        return task_frame

    def resume_task(self, worker: DownloadWorker):
        """恢复特定任务"""
        self.task_manager.resume_task(worker)
        self.log_message(f"已恢复任务")

    def pause_task(self, worker: DownloadWorker):
        """暂停特定任务"""
        self.task_manager.pause_task(worker)
        self.log_message(f"已暂停任务")

    def delete_task(self, task_frame: QFrame, worker: DownloadWorker):
        """删除特定任务"""
        try:
            # 从UI中移除任务框架
            try:
                self.tasks_layout.removeWidget(task_frame)
                task_frame.deleteLater()
            except Exception as e:
                print(f"从UI移除任务框架时出错: {e}")

            # 从任务管理器中移除
            try:
                self.task_manager.remove_task(worker)
                self.log_message(f"已删除任务")
            except Exception as e:
                print(f"从任务管理器移除任务时出错: {e}")
                self.log_message(f"删除任务时出错: {str(e)}")
                
        except Exception as e:
            print(f"删除任务时发生未知错误: {e}")
            self.log_message(f"删除任务失败: {str(e)}")

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
            self.log_message(f"下载路径已更新为: {self.download_dir}")
            # 自动保存配置
            self.config_manager.set("download_dir", self.download_dir)

    def closeEvent(self, event):
        """窗口关闭事件，保存窗口位置和大小"""
        print("程序正在关闭...")
        
        # 获取当前窗口的位置和大小
        pos = self.pos()
        size = self.size()

        # 保存到配置文件
        window_pos = [pos.x(), pos.y()]
        window_size = [size.width(), size.height()]

        self.config_manager.set("window_position", window_pos)
        self.config_manager.set("window_size", window_size)
        print(f"已保存窗口配置")

        # 停止所有正在进行的任务
        if hasattr(self, 'task_manager'):
            print("正在停止所有下载任务...")
            self.task_manager.stop_all_tasks()
            
            # 等待一小段时间让任务停止
            import time
            time.sleep(1)
            print("任务已停止")

        # 不再清理文件，保留所有下载状态
        print("程序关闭完成，所有文件已保留")
        # 接受关闭事件
        event.accept()


def run_gui():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())