import json
import os
import threading
from typing import Dict, Any

CONFIG_FILE_PATH = "config.json"


class ConfigManager:
    """配置管理器（线程安全）"""

    def __init__(self, config_file: str = CONFIG_FILE_PATH):
        self.config_file = config_file
        self._lock = threading.Lock()
        self.default_config = {
            "download_dir": os.path.join(os.getcwd(), "Download"),
            "headless_mode": True,
            "bypass_mode": False,  # 新增：是否启用Bypass
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
                        # 合并默认配置，确保所有键存在
                        for key, value in self.default_config.items():
                            if key not in loaded_config:
                                loaded_config[key] = value
                    return loaded_config
                except (json.JSONDecodeError, FileNotFoundError):
                    print(f"配置文件 {self.config_file} 损坏，使用默认配置")
                    return self.default_config.copy()
            else:
                print(f"配置文件 {self.config_file} 不存在，创建默认配置")
                self.save_config(self.default_config)
                return self.default_config.copy()

    def save_config(self, config: Dict[str, Any] = None) -> None:
        with self._lock:
            if config is None:
                config = self.config
            try:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=4, ensure_ascii=False)
                print(f"配置已保存到 {self.config_file}")
            except Exception as e:
                print(f"保存配置文件失败: {e}")

    def get(self, key: str, default=None):
        with self._lock:
            return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self.config[key] = value
            self.save_config()

    def update(self, updates: Dict[str, Any]) -> None:
        with self._lock:
            self.config.update(updates)
            self.save_config()