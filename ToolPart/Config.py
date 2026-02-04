import json
import os
from typing import Dict, Any

CONFIG_FILE_PATH = "config.json"


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, config_file: str = CONFIG_FILE_PATH):
        self.config_file = config_file
        self.default_config = {
            "download_dir": os.path.join(os.getcwd(), "Download"),  # 默认下载目录为当前目录下的Download文件夹
            "headless_mode": True,
            "window_position": [100, 100],  # 窗口位置 [x, y]
            "window_size": [1000, 800]      # 窗口大小 [width, height]
        }
        self.config = self.load_config()
    
    def load_config(self) -> Dict[str, Any]:
        """加载配置文件，如果不存在则创建默认配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    # 确保所有默认配置项都存在
                    for key, value in self.default_config.items():
                        if key not in loaded_config:
                            loaded_config[key] = value
                return loaded_config
            except (json.JSONDecodeError, FileNotFoundError):
                # 如果配置文件损坏，则使用默认配置
                print(f"配置文件 {self.config_file} 损坏，使用默认配置")
                return self.default_config.copy()
        else:
            # 配置文件不存在，创建默认配置
            print(f"配置文件 {self.config_file} 不存在，创建默认配置")
            # 不再自动创建下载目录，只在开始下载时创建
            self.save_config(self.default_config)
            return self.default_config.copy()
    
    def save_config(self, config: Dict[str, Any] = None) -> None:
        """保存配置到文件"""
        if config is None:
            config = self.config
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            print(f"配置已保存到 {self.config_file}")
        except Exception as e:
            print(f"保存配置文件失败: {e}")
    
    def get(self, key: str, default=None):
        """获取配置值"""
        return self.config.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """设置配置值并保存"""
        self.config[key] = value
        self.save_config()
    
    def update(self, updates: Dict[str, Any]) -> None:
        """批量更新配置值并保存"""
        self.config.update(updates)
        self.save_config()