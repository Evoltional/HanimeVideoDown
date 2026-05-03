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
            "storage_dir": os.path.join(os.getcwd(), "Download"),  # 保留用于兼容
            "storage_dirs": [os.path.join(os.getcwd(), "Download")],  # 新增：支持多个存储目录
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
                print(f"当前工作目录：{os.getcwd()}")
                # 注意：这里不能在锁内部调用 save_config，会导致死锁
                # 所以先释放锁，再保存配置
                config_to_save = self.default_config.copy()
        
        # 在锁外部调用 save_config
        if not os.path.exists(self.config_file):
            try:
                self._save_config_without_lock(config_to_save)
            except Exception as e:
                print(f"致命错误：无法创建配置文件 - {e}")
                print("\n建议解决方案：")
                print("1. 确保程序有权限在当前目录写入文件")
                print(f"2. 当前目录 '{os.getcwd()}' 可能不可写")
                print("3. 尝试以管理员身份运行程序")
                print("4. 检查杀毒软件是否阻止了文件创建\n")
                raise
        
        return self.default_config.copy()

    def _save_config_without_lock(self, config: Dict[str, Any]) -> None:
        """内部方法：不获取锁，直接在已持有锁的情况下调用"""
        try:
            # 获取绝对路径以便调试
            abs_path = os.path.abspath(self.config_file)
            print(f"尝试保存配置文件到：{abs_path}")
                
            # 检查目录是否存在以及是否可写
            config_dir = os.path.dirname(abs_path)
            print(f"配置目录：{config_dir}")
            print(f"目录存在：{os.path.exists(config_dir)}")
            print(f"目录可写：{os.access(config_dir, os.W_OK)}")
                
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            print(f"配置已保存到 {self.config_file}")
            print(f"文件存在检查：{os.path.exists(self.config_file)}")
        except PermissionError as e:
            print(f"保存配置文件失败：权限错误 - {e}")
            print(f"请确保程序有权限在以下目录写入文件：{os.getcwd()}")
            raise
        except Exception as e:
            print(f"保存配置文件失败：{e}")
            import traceback
            traceback.print_exc()
            raise
    
    def save_config(self, config: Dict[str, Any] = None) -> None:
        """保存配置文件（线程安全）"""
        with self._lock:
            if config is None:
                config = self.config
            # 调用内部方法，避免重复获取锁
            self._save_config_without_lock(config)

    def get(self, key: str, default=None):
        with self._lock:
            return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置配置项并保存（线程安全）"""
        with self._lock:
            self.config[key] = value
            # 在锁内部直接保存，避免重复获取锁
            self._save_config_without_lock(self.config)

    def update(self, updates: Dict[str, Any]) -> None:
        """批量更新配置并保存（线程安全）"""
        with self._lock:
            self.config.update(updates)
            # 在锁内部直接保存，避免重复获取锁
            self._save_config_without_lock(self.config)