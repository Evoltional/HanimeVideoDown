import asyncio
import os
from typing import List, Optional, Tuple
from urllib.parse import unquote

import requests
from pydoll.browser import Chrome


class VideoDownloader:
    def __init__(self, download_dir="./downloads", max_retries=3):
        self.download_dir = download_dir
        self.max_retries = max_retries
        # 创建下载目录
        os.makedirs(download_dir, exist_ok=True)

    async def extract_download_info(self, download_page_url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        从下载页面提取视频下载链接和文件名
        """
        async with Chrome() as browser:
            tab = await browser.start()

            print(f"访问下载页面: {download_page_url}")
            await tab.go_to(download_page_url)
            await asyncio.sleep(5)  # 等待页面完全加载

            try:
                # 查找下载链接列表表格
                table_element = await tab.query('//*[@id="content-div"]/div[1]/div[4]/div/div/table',
                                                timeout=10, raise_exc=False)

                if not table_element:
                    print("未找到下载链接列表表格")
                    return None, None

                print("找到下载链接列表表格")

                # 查找第一个下载按钮
                first_download_btn = await tab.query(
                    '//*[@id="content-div"]/div[1]/div[4]/div/div/table/tbody/tr[2]/td[5]/a',
                    timeout=10, raise_exc=False)

                if not first_download_btn:
                    print("未找到第一个下载按钮")
                    return None, None

                # 获取下载链接
                download_url =  first_download_btn.get_attribute('data-url')

                # 获取文件名
                filename =  first_download_btn.get_attribute('download')
                if not filename:
                    # 如果没有download属性，则获取按钮的文本内容
                    filename = await first_download_btn.text

                # 清理文件名，确保扩展名
                if filename:
                    # 移除首尾空格
                    filename = filename.strip()
                    # 确保文件扩展名为.mp4
                    if not filename.endswith('.mp4'):
                        filename += '.mp4'

                print(f"提取到下载链接: {download_url}")
                print(f"提取到文件名: {filename}")

                return download_url, filename

            except Exception as e:
                print(f"提取下载信息时出错: {e}")
                import traceback
                traceback.print_exc()
                return None, None

    def download_video(self, video_url: str, filename: str) -> bool:
        """
        下载视频文件
        """
        if not video_url or not filename:
            print("下载链接或文件名为空，跳过下载")
            return False

        # 清理文件名，移除非法字符
        safe_filename = self._sanitize_filename(filename)
        file_path = os.path.join(self.download_dir, safe_filename)

        print(f"开始下载: {safe_filename}")
        print(f"下载链接: {video_url}")

        retry_count = 0
        while retry_count < self.max_retries:
            try:
                response = requests.get(video_url, stream=True, timeout=30)
                response.raise_for_status()

                total_size = int(response.headers.get('content-length', 0))
                downloaded_size = 0

                with open(file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)

                            # 显示下载进度
                            if total_size > 0:
                                progress = (downloaded_size / total_size) * 100
                                print(f"\r下载进度: {progress:.1f}% ({downloaded_size}/{total_size} bytes)", end='')

                print(f"\n下载完成: {file_path}")
                return True

            except Exception as e:
                retry_count += 1
                print(f"下载失败 (重试 {retry_count}/{self.max_retries}): {e}")
                if retry_count >= self.max_retries:
                    print(f"下载最终失败: {e}")
                    import traceback
                    traceback.print_exc()
                    return False
                else:
                    # 等待一段时间后重试
                    asyncio.sleep(2)

        return False

    def _sanitize_filename(self, filename: str) -> str:
        """
        清理文件名，移除非法字符
        """
        # 替换Windows/Linux不支持的字符
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')

        # 解码URL编码的字符（如果有）
        filename = unquote(filename)

        return filename

    async def download_from_page(self, download_page_url: str) -> bool:
        """
        从下载页面获取信息并下载视频
        """
        # 提取下载信息
        video_url, filename = await self.extract_download_info(download_page_url)

        if video_url and filename:
            # 执行下载
            success = self.download_video(video_url, filename)
            return success
        else:
            print("无法提取下载信息")
            return False


class EnhancedHanime1Scraper:
    def __init__(self, max_workers=5):
        self.all_video_links = set()
        self.download_links = []
        self.downloader = VideoDownloader()
        self.max_workers = max_workers

    async def get_video_links(self, start_url: str) -> List[str]:
        """获取所有视频链接"""
        async with Chrome() as browser:
            tab = await browser.start()

            await tab.go_to(start_url)
            await asyncio.sleep(3)

            overlay_elements = await tab.find(class_name='overlay', find_all=True, timeout=10, raise_exc=False)

            if overlay_elements:
                for element in overlay_elements:
                    # 检查元素类型，确定是否需要await
                    if hasattr(element, 'get_attribute') and callable(getattr(element, 'get_attribute')):
                        # 元素是异步对象，需要await
                        href =  element.get_attribute('href')
                    else:
                        # 元素已经是字符串，直接使用
                        href = element.get_attribute('href')

                    if href:
                        self.all_video_links.add(href)

            return list(self.all_video_links)

    async def process_single_link(self, video_url: str) -> None:
        """处理单个视频链接，提取下载链接"""
        print(f"处理链接: {video_url}")

        async with Chrome() as browser:
            tab = await browser.start()

            try:
                await tab.go_to(video_url)
                await asyncio.sleep(3)

                download_btn = await tab.find(id='downloadBtn', timeout=10, raise_exc=False)

                if download_btn:
                    # 同样处理download_btn的get_attribute调用
                    if hasattr(download_btn, 'get_attribute') and callable(getattr(download_btn, 'get_attribute')):
                        download_href =  download_btn.get_attribute('href')
                    else:
                        download_href = download_btn.get_attribute('href')

                    if download_href:
                        self.download_links.append(download_href)
                        print(f"✓ 找到下载链接: {download_href}")

                        # 直接下载该视频
                        await self.downloader.download_from_page(download_href)
                    else:
                        print(f"✗ 未找到下载链接: {video_url}")
                else:
                    print(f"✗ 未找到下载按钮: {video_url}")

            except Exception as e:
                print(f"处理链接 {video_url} 时出错: {e}")

    async def process_links_batch(self, links_batch: List[str]) -> None:
        """批量处理链接"""
        semaphore = asyncio.Semaphore(self.max_workers)  # 限制并发数

        async def process_with_semaphore(link: str):
            async with semaphore:
                return await self.process_single_link(link)

        tasks = [process_with_semaphore(link) for link in links_batch]
        await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    scraper = EnhancedHanime1Scraper(max_workers=3)  # 限制并发浏览器数量
    video_links = await scraper.get_video_links('https://hanime1.me/watch?v=22602')

    if not video_links:
        print("没有找到任何视频链接")
        return

    print(f"开始处理 {len(video_links)} 个视频链接...")
    await scraper.process_links_batch(video_links)

    print(f"处理完成！共找到 {len(scraper.download_links)} 个下载链接")


if __name__ == "__main__":
    asyncio.run(main())
