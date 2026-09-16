"""Local web search: technical payload bounds, no usage quotas.

2026-09-16: the first live probe overflowed ctx 8192 (6,219 prompt tokens + a 4,500-char
block = 8,913). Bounds lowered; P8.1 sizes the block from the first call's prompt_n.
"""

ENABLED = True
SEARXNG_URL = "http://127.0.0.1:8888"
MAX_RESULTS = 5
MAX_PAGES = 2
PAGE_CHARS = 700
BLOCK_CHARS = 2200
TIMEOUT_SECONDS = 8
