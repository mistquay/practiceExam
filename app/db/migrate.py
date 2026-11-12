"""极简迁移器：正向执行 migrations/NNNN_*.sql，反向以「迁移前快照还原」实现。

设计取舍：
  * SQLite 删表会丢数据，本项目约定迁移脚本只增不删（0 次删表语句）。
  * upgrade 每应用一个迁移前，先 `VACUUM INTO` 生成一致性快照（WAL 安全），路径记入 schema_migrations。
  * downgrade 先把当前库 `VACUUM INTO` 归档（回滚后旧数据仍可查），再用 sqlite3 backup API
    把迁移前快照整页写回当前库——不复制 .db 文件、不删除任何文件。

用法：
  python -m app.db.migrate status    [--db data/exam.db]
  python -m app.db.migrate upgrade   [--db ...] [--to 0001]
  python -m app.db.migrate downgrade [--db ...] [--to 0000]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.db import REPO_ROOT, connect, resolve_db_path

MIGRATIONS_DIR = REPO_ROOT / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_([A-Za-z0-9_]+)\.sql$")

_BOOKKEEPING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version      TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    checksum     TEXT NOT NULL,
    applied_at   TEXT NOT NULL,
    pre_snapshot TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return hashlib.sha1(self.path.read_bytes()).hexdigest()


def discover(migrations_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    found = []
    for p in sorted(migrations_dir.glob("*.sql")):
        m = _NAME_RE.match(p.name)
        if m:
            found.append(Migration(m.group(1), m.group(2), p))
    return found


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _backup_dir(db_path: Path) -> Path:
    d = db_path.parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _vacuum_into(conn: sqlite3.Connection, target: Path) -> None:
    conn.execute("VACUUM INTO ?", (str(target),))


def applied(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    conn.execute(_BOOKKEEPING)
    rows = conn.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
    return {r["version"]: r for r in rows}


def status(db_path: str | Path | None = None, migrations_dir: Path = MIGRATIONS_DIR) -> list[dict]:
    db = resolve_db_path(db_path)
    conn = connect(db)
    try:
        done = applied(conn)
        out = []
        for m in discover(migrations_dir):
            row = done.get(m.version)
            out.append({
                "version": m.version,
                "name": m.name,
                "applied": row is not None,
                "checksum_ok": None if row is None else row["checksum"] == m.checksum,
            })
        return out
    finally:
        conn.close()


def upgrade(db_path: str | Path | None = None, to: str | None = None,
            migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """应用全部（或到 `to` 为止的）未执行迁移，返回本次应用的版本号。"""
    db = resolve_db_path(db_path)
    conn = connect(db)
    done_now: list[str] = []
    try:
        done = applied(conn)
        for m in discover(migrations_dir):
            if to is not None and m.version > to:
                break
            if m.version in done:
                if done[m.version]["checksum"] != m.checksum:
                    raise RuntimeError(f"迁移 {m.path.name} 已执行但文件内容已变化（checksum 不一致），禁止改写已发布迁移")
                continue
            snapshot = _backup_dir(db) / f"{db.stem}.pre-{m.version}.{_utc_stamp()}.db"
            _vacuum_into(conn, snapshot)
            record = (
                "INSERT INTO schema_migrations (version, name, checksum, applied_at, pre_snapshot) "
                "VALUES ({v}, {n}, {c}, {a}, {s});"
            ).format(
                v=_q(m.version), n=_q(m.name), c=_q(m.checksum), a=_q(_utc_iso()), s=_q(str(snapshot)),
            )
            try:
                conn.executescript(f"BEGIN;\n{m.sql}\n{record}\nCOMMIT;")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            done_now.append(m.version)
        return done_now
    finally:
        conn.close()


def downgrade(db_path: str | Path | None = None, to: str = "0000") -> list[str]:
    """回滚到版本 `to`（不含）之后的全部迁移，返回被回滚的版本号（降序）。"""
    db = resolve_db_path(db_path)
    rolled: list[str] = []
    conn = connect(db)
    try:
        while True:
            done = applied(conn)
            pending = [v for v in sorted(done, reverse=True) if v > to]
            if not pending:
                return rolled
            version = pending[0]
            snapshot = Path(done[version]["pre_snapshot"])
            if not snapshot.is_file():
                raise FileNotFoundError(f"迁移 {version} 的迁移前快照不存在：{snapshot}")
            archive = _backup_dir(db) / f"{db.stem}.rollback-from-{version}.{_utc_stamp()}.db"
            _vacuum_into(conn, archive)
            src = sqlite3.connect(snapshot)
            try:
                src.backup(conn)
            finally:
                src.close()
            rolled.append(version)
    finally:
        conn.close()


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.db.migrate", description="数据库迁移（正向/回滚）")
    parser.add_argument("command", choices=("status", "upgrade", "downgrade"))
    parser.add_argument("--db", help="数据库路径，默认 EXAM_DB_PATH 或 data/exam.db")
    parser.add_argument("--to", help="目标版本：upgrade 默认最新，downgrade 默认 0000（空库）")
    args = parser.parse_args(argv)
    if args.command == "status":
        for item in status(args.db):
            print(item)
    elif args.command == "upgrade":
        print("applied:", upgrade(args.db, args.to) or "(none)")
    else:
        print("rolled back:", downgrade(args.db, args.to or "0000") or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
