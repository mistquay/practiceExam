"""连接与写事务。

SQLite WAL 只有一个写者。集中考试 50 人同时交卷时，如果每个请求各自
`BEGIN DEFERRED`，会在升级为写锁时撞 `database is locked`。这里把所有写事务
收敛到一个入口：进程内互斥锁 + `BEGIN IMMEDIATE` + busy_timeout + 有限重试，
保证并发交卷 0 锁错、0 丢分（跨进程时靠 busy_timeout 与重试）。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from app.config import WRITE_RETRY, WRITE_RETRY_SLEEP
from app.db import connect

_WRITE_LOCK = threading.RLock()


def connection() -> sqlite3.Connection:
    return connect()


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """在显式 IMMEDIATE 事务里执行写操作；异常回滚并原样抛出。"""
    last: Exception | None = None
    for attempt in range(WRITE_RETRY):
        with _WRITE_LOCK:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                last = exc
                time.sleep(WRITE_RETRY_SLEEP * (attempt + 1))
                continue
            try:
                yield conn
                conn.execute("COMMIT")
                return
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
    raise RuntimeError("数据库写事务重试耗尽") from last


@contextmanager
def read_conn() -> Iterator[sqlite3.Connection]:
    conn = connection()
    try:
        yield conn
    finally:
        conn.close()
