"""静态入口回归：从首页提取资源，验证浏览器实际使用的 URL 和 ESM 子模块。"""
from __future__ import annotations

import re
import sqlite3
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.main import app  # noqa: E402


class PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.styles = []
        self.modules = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])
            if attrs.get("type") == "module":
                self.modules.append(attrs["src"])
        if tag == "link" and "stylesheet" in attrs.get("rel", "").split() and attrs.get("href"):
            self.styles.append(attrs["href"])


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 源库只读打开；启动迁移和所有请求只使用临时副本。
    source = REPO_ROOT / "data" / "exam.db"
    database = tmp_path / "exam.db"
    src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(database)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    monkeypatch.setenv("EXAM_DB_PATH", str(database))
    with TestClient(app) as test_client:
        yield test_client


def page_assets(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";", 1)[0] == "text/html"
    assets = PageAssets()
    assets.feed(response.text)
    assert assets.scripts, "首页必须引用脚本"
    assert assets.styles, "首页必须引用样式"
    return assets


def assert_asset(client, url, media_types):
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code)
    assert response.headers["content-type"].split(";", 1)[0] in media_types, (
        url, response.headers["content-type"]
    )
    assert response.content, url
    return response


def test_homepage_script_and_stylesheet_urls(client):
    assets = page_assets(client)
    for src in assets.scripts:
        assert_asset(client, urljoin("/", src), {"text/javascript", "application/javascript"})
    for href in assets.styles:
        assert_asset(client, urljoin("/", href), {"text/css"})


def test_entry_module_relative_imports(client):
    assets = page_assets(client)
    assert assets.modules, "首页必须引用 ESM 入口"
    for src in assets.modules:
        entry_url = urljoin("/", src)
        entry = assert_asset(client, entry_url, {"text/javascript", "application/javascript"})
        # 提取入口实际的静态 import，包括 from 和仅副作用 import。
        imports = re.findall(
            r"\bimport\s+(?:[^;\n]*?\s+from\s+)?[\"'](\.{1,2}/[^\"']+)[\"']",
            entry.text,
        )
        assert imports, (entry_url, "入口必须包含相对 import")
        for specifier in imports:
            assert_asset(client, urljoin(entry_url, specifier),
                         {"text/javascript", "application/javascript"})


def test_root_entry_and_api_keep_priority(client):
    assert client.get("/index.html").content == client.get("/").content
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["content-type"].split(";", 1)[0] == "application/json"
    assert "detail" in response.json()
