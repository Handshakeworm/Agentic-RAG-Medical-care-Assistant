"""Redis 客户端层(spec §5.1 / §8.4 H1 / H6)。

仅 `cache.py` 一个模块,实现:
- 配置缓存(`get_config_cached` / `invalidate_config`)
- 限流后端(H6 追加,文件:`rate_limit_backend.py`)
"""
