"""通用工具函数（不依赖 PyQt / 浏览器，便于单元测试）。"""

import os
import re
from urllib.parse import unquote

# 下载文件名中需要清理的非法字符（Windows 文件系统）
_INVALID_CHARS = '<>:"/\\|?_*'

# Hanime 常见的冗余标记，需从文件名中剔除后再做重复比对
_SUB_MARKETING = re.compile(r'\s*\[中字後補\]\s*')


def sanitize_filename(filename: str) -> str:
    """清理文件名中的非法字符与冗余标记，并做长度截断。

    - 将 Windows 非法字符替换为空格
    - 对 URL 编码进行解码
    - 删除 `[中字後補]` 及其前后空格
    - 长度超过 200 时截断主体（保留扩展名）
    """
    if not filename:
        return filename
    result = filename
    for char in _INVALID_CHARS:
        result = result.replace(char, ' ')
    result = unquote(result)
    result = _SUB_MARKETING.sub('', result)
    if len(result) > 200:
        name, ext = os.path.splitext(result)
        result = name[:190] + ext
    return result
