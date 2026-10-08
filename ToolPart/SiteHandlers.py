"""站点解析抽象层。

将 Hanime 站点相关的 URL 构造、链接校验与 XPath 选择器集中到此处，
避免脆弱的字符串散落在 DownLoad 各处。后续若需支持其它站点，
只需新增一个 Handler 并在 DownLoad 中切换即可。
"""

import re
import json
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 站点根地址
BASE_URL = "https://hanime1.me"
WATCH_PREFIX = "https://hanime1.me/watch?v="
DOWNLOAD_PREFIX = "https://hanime1.me/download?v="

# 播放列表（视频列表页）的选择器
XPATH_PLAYLIST_CONTAINER = '//*[@id="playlist-scroll"]'
XPATH_PLAYLIST_LINKS = '//*[@id="playlist-scroll"]//a[@href]'

# 下载页中"直接下载链接"表格与按钮的选择器
XPATH_DOWNLOAD_TABLE = '//*[@id="content-div"]/div[1]/div[4]/div/div/table'
XPATH_DOWNLOAD_BUTTON = '//*[@id="content-div"]/div[1]/div[4]/div/div/table/tbody/tr[2]/td[5]/a'

# 从 download 链接中解析视频 id
VIDEO_ID_PATTERN = re.compile(r'download\?v=([^&]+)')


def build_download_url(watch_url: str) -> str:
    """将 watch 链接转换为 download 链接；否则原样返回。"""
    if '/watch?' in watch_url:
        return watch_url.replace('/watch?', '/download?')
    return watch_url


def build_watch_url(download_url: str) -> str:
    """将 download 链接转换为 watch 链接；否则原样返回。"""
    if '/download?' in download_url:
        return download_url.replace('/download?', '/watch?')
    return download_url


def is_video_link(href: str) -> bool:
    """判断该 href 是否为有效的视频观看链接。"""
    return isinstance(href, str) and href.startswith(WATCH_PREFIX)


def parse_video_id(download_link: str) -> Optional[str]:
    """从 download 链接中解析视频 id。"""
    match = VIDEO_ID_PATTERN.search(download_link)
    return match.group(1) if match else None


async def get_attr(element, name: str) -> Optional[str]:
    """异步安全地获取元素属性（pydoll 3.0 兼容）。

    pydoll 3.0 的 ``WebElement.get_attribute`` 是**同步**方法，且只返回
    元素被定位时捕获到的属性；对 JS 动态注入的自定义属性（如 hanime
    下载按钮上的 ``data-url``）可能返回 ``None``/空串。因此这里先用同步
    ``get_attribute`` 取值，取不到（或取到空）再回退到 ``execute_script``
    直接读 DOM 属性（``this.getAttribute(name)``），兼容标准与动态属性。

    关键：绝不能 ``await element.get_attribute(...)`` —— 同步方法不可
    await，会抛 ``TypeError`` 被调用方的 ``except`` 吞掉，导致 0 链接。
    """
    if element is None:
        return None
    # 1) 同步取值（标准反射属性，如 <a href> 通常可命中；空串视为未捕获）
    sync_val = element.get_attribute(name)
    if sync_val:
        return sync_val
    # 2) JS 回退：读取 DOM 上的真实属性（含自定义 data-*）
    try:
        resp = await element.execute_script(
            'return this.getAttribute(%s)' % json.dumps(name),
            return_by_value=True,
        )
        return resp.get('result', {}).get('result', {}).get('value')
    except Exception as exc:
        logger.debug(f"JS 回退获取属性 {name} 失败: {exc}")
        return None


async def extract_button_info(button_element) -> Tuple[Optional[str], Optional[str]]:
    """从下载按钮元素中提取 (直接下载地址, 文件名)。

    button_element 为 pydoll 元素对象。属性读取统一走 :func:`get_attr`
    （同步优先、JS 回退），避免直接 ``await get_attribute`` 的同步/异步
    混用错误；文件名缺失时回退到元素可见文本 ``text``（异步属性）。
    """
    if button_element is None:
        return None, None
    download_url = await get_attr(button_element, 'data-url')
    filename = await get_attr(button_element, 'download')
    if not filename:
        filename = await button_element.text
    if filename:
        filename = filename.strip()
        if not filename.endswith('.mp4'):
            filename += '.mp4'
    return download_url, filename
