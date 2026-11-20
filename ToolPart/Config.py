import json
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

CONFIG_FILE_PATH = "config.json"

logger = logging.getLogger(__name__)

_LOG_CONFIGURED = False


def configure_logging(log_dir: str = "Logger") -> None:
    """一次性配置全局日志：滚动文件 + 控制台。

    应在程序入口（VideoDown.main / GUI）尽早调用一次。
    """
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception as exc:  # 日志本身失败不应影响主流程
        print(f"警告：无法初始化日志文件处理器 - {exc}")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)

    _LOG_CONFIGURED = True


class ConfigManager:
    """配置管理器（线程安全）。"""

    def __init__(self, config_file: str = CONFIG_FILE_PATH):
        self.config_file = config_file
        self._lock = threading.RLock()
        self.default_config = {
            "download_dir": os.path.join(os.getcwd(), "Download"),
            "storage_dir": os.path.join(os.getcwd(), "Download"),  # 保留用于兼容
            "storage_dirs": [os.path.join(os.getcwd(), "Download")],  # 新增：支持多个存储目录
            "headless_mode": True,
            "bypass_mode": False,  # 新增：是否启用Bypass
            "single_video_mode": False,  # 新增：仅下载输入链接的视频（不抓取整个播放列表）
            "max_workers": 2,  # 单个任务内并发抓取/下载的工作线程数
            "max_active_tasks": 2,  # 全局同时运行的任务数（任务级并发）
            "window_position": [100, 100],
            "window_size": [1000, 800],
            "cloudflare_timeout": 15,
            "cloudflare_max_retries": 3,
            "page_load_timeout": 60,
            "element_wait_timeout": 10
        }
        self.config = self.load_config()

    def load_config(self) -> Dict[str, Any]:
        with self._lock:
            if os.path.exists(self.config_file):
                try:
                    with open(self.config_file, 'r', encoding='utf-8') as f:
                        loaded_config = json.load(f)
                        # 合并默认配置，确保所有键存在（兼容旧配置缺键）
                        for key, value in self.default_config.items():
                            if key not in loaded_config:
                                loaded_config[key] = value
                    return loaded_config
                except (json.JSONDecodeError, FileNotFoundError):
                    logger.error(f"配置文件 {self.config_file} 损坏，使用默认配置")
                    return self.default_config.copy()
            else:
                logger.info(f"配置文件 {self.config_file} 不存在，创建默认配置")
                logger.info(f"当前工作目录：{os.getcwd()}")
                # 注意：这里不能在锁内部调用 save_config，会导致死锁
                # 所以先释放锁，再保存配置
                config_to_save = self.default_config.copy()

        # 在锁外部调用 save_config
        if not os.path.exists(self.config_file):
            try:
                self._save_config_without_lock(config_to_save)
            except Exception as e:
                logger.error(f"致命错误：无法创建配置文件 - {e}")
                logger.error("\n建议解决方案：")
                logger.error("1. 确保程序有权限在当前目录写入文件")
                logger.error(f"2. 当前目录 '{os.getcwd()}' 可能不可写")
                logger.error("3. 尝试以管理员身份运行程序")
                logger.error("4. 检查杀毒软件是否阻止了文件创建\n")
                raise

        return self.default_config.copy()

    def _save_config_without_lock(self, config: Dict[str, Any]) -> None:
        """内部方法：不获取锁，直接在已持有锁的情况下调用。"""
        try:
            abs_path = os.path.abspath(self.config_file)
            logger.info(f"尝试保存配置文件到：{abs_path}")

            config_dir = os.path.dirname(abs_path)
            logger.info(f"配置目录：{config_dir}")
            logger.info(f"目录存在：{os.path.exists(config_dir)}")
            logger.info(f"目录可写：{os.access(config_dir, os.W_OK)}")

            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            logger.info(f"配置已保存到 {self.config_file}")
            logger.info(f"文件存在检查：{os.path.exists(self.config_file)}")
        except PermissionError as e:
            logger.error(f"保存配置文件失败：权限错误 - {e}")
            logger.error(f"请确保程序有权限在以下目录写入文件：{os.getcwd()}")
            raise
        except Exception as e:
            logger.error(f"保存配置文件失败：{e}")
            import traceback
            traceback.print_exc()
            raise

    def save_config(self, config: Dict[str, Any] = None) -> None:
        """保存配置文件（线程安全）。"""
        with self._lock:
            if config is None:
                config = self.config
            self._save_config_without_lock(config)

    def get(self, key: str, default=None):
        with self._lock:
            return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置配置项并保存（线程安全）。"""
        with self._lock:
            self.config[key] = value
            self._save_config_without_lock(self.config)

    def update(self, updates: Dict[str, Any]) -> None:
        """批量更新配置并保存（线程安全）。"""
        with self._lock:
            self.config.update(updates)
            self._save_config_without_lock(self.config)
