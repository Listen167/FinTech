"""测试默认使用内存仓库，避免读写开发者本地数据库。"""

import os


os.environ["DATABASE_ENABLED"] = "false"
os.environ["DEEPSEEK_ENABLED"] = "false"
os.environ["AUTH_COOKIE_SECURE"] = "false"
