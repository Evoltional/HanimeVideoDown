"""存储/临时文件管理（纯逻辑，不依赖 UI，便于复用与测试）。

原实现中散落在 MainWindow 的临时文件统计与清理逻辑抽取到此模块，
MainWindow 仅作为薄封装调用这些方法。
"""

import os
import logging

logger = logging.getLogger(__name__)

TEMP_PREFIX = "下载中_"
TEMP_SUFFIX = ".mp4"


def count_temporary_files(download_dir: str) -> int:
    """统计下载目录中以 `下载中_` 开头、`.mp4` 结尾的临时文件数量。"""
    try:
        if download_dir and os.path.exists(download_dir):
            files = os.listdir(download_dir)
            return len([f for f in files if f.startswith(TEMP_PREFIX) and f.endswith(TEMP_SUFFIX)])
    except Exception:
        pass
    return 0


def delete_temporary_files(download_dir: str, log_func=None) -> int:
    """删除下载目录中所有临时文件，返回删除数量。

    :param log_func: 可选的日志回调，签名为 (message, level=None)
    """
    deleted = 0
    try:
        if os.path.exists(download_dir):
            for filename in os.listdir(download_dir):
                if filename.startswith(TEMP_PREFIX) and filename.endswith(TEMP_SUFFIX):
                    path = os.path.join(download_dir, filename)
                    try:
                        os.remove(path)
                        deleted += 1
                        if log_func:
                            log_func(f"已删除临时文件: {filename}")
                    except Exception as e:
                        if log_func:
                            log_func(f"删除临时文件失败 {filename}: {str(e)}", "WARNING")
    except Exception as e:
        if log_func:
            log_func(f"扫描临时文件时出错: {str(e)}", "WARNING")
    return deleted
