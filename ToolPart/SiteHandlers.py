"""站点解析抽象层。

将 Hanime 站点相关的 URL 构造、链接校验与 XPath 选择器集中到此处，
避免脆弱的字符串散落在 DownLoad 各处。后续若需支持其它站点，
只需新增一个 Handler 并在 DownLoad 中切换即可。
"""

import re
from typing import Optional, Tuple

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


async def extract_button_info(button_element) -> Tuple[Optional[str], Optional[str]]:
    """从下载按钮元素中提取 (直接下载地址, 文件名)。

    button_element 为 pydoll 元素对象；此处统一异步获取属性，
    避免原先 `try/except` 中混用同步 get_attribute 的脆弱写法。
    """
    if button_element is None:
        return None, None
    download_url = await button_element.get_attribute('data-url')
    filename = await button_element.get_attribute('download')
    if not filename:
        filename = await button_element.text
    if filename:
        filename = filename.strip()
        if not filename.endswith('.mp4'):
            filename += '.mp4'
    return download_url, filename
