"""运行时配置。

数据库路径由 `app.db.resolve_db_path()` 解析：优先 `EXAM_DB_PATH` 环境变量，
默认 <repo>/data/exam.db。所有取值都在调用时读取环境变量，便于测试切库。
"""
from __future__ import annotations

import os
from pathlib import Path

from app.db import REPO_ROOT

STATIC_DIR = REPO_ROOT / "static"
FIXTURES_DIR = REPO_ROOT / "fixtures"
TEMPLATES_YAML = FIXTURES_DIR / "paper_templates.yaml"
SPECIALTIES_YAML = FIXTURES_DIR / "specialties.yaml"

COOKIE_NAME = os.getenv("EXAM_SESSION_COOKIE", "exam_session")
SESSION_TTL_SECONDS = int(os.getenv("EXAM_SESSION_TTL", str(12 * 3600)))
COOKIE_SECURE = os.getenv("EXAM_COOKIE_SECURE", "0") == "1"

# 空库启动时创建的第一个管理员；生产环境务必用环境变量覆盖。
BOOTSTRAP_ADMIN_NO = os.getenv("EXAM_ADMIN_NO", "admin")
BOOTSTRAP_ADMIN_PASSWORD = os.getenv("EXAM_ADMIN_PASSWORD", "Admin@12345")
BOOTSTRAP_ADMIN_NAME = os.getenv("EXAM_ADMIN_NAME", "系统管理员")

# 会话与判分相关的常量
EXAM_AUTO_SUBMIT_ON_DEADLINE = True
MAX_PRACTICE_COUNT = 200
WRITE_RETRY = 6
WRITE_RETRY_SLEEP = 0.05


def db_path() -> Path:
    from app.db import resolve_db_path

    return resolve_db_path()
