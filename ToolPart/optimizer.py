"""性能优化器。

集中管理重试退避与浏览器相关默认参数，供 DownLoad / GUI 复用，
避免之前散落在各处的"死代码"常量。
"""

import logging

logger = logging.getLogger(__name__)


class PerformanceOptimizer:
    """性能优化器（唯一实现，已被重试逻辑真正调用）。"""

    PAGE_LOAD_TIMEOUT = 60
    ELEMENT_WAIT_TIMEOUT = 10
    RETRY_ATTEMPTS = 5

    @staticmethod
    def calculate_optimal_delay(base_delay: float, attempt: int) -> float:
        """指数退避：base_delay * 1.5 ** attempt。

        attempt 从 1 开始计数，因此第 1 次重试等待 base_delay * 1.5。
        与 DownLoad.download_video 的重试循环联动。
        """
        return base_delay * (1.5 ** max(attempt, 0))
