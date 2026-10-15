"""Собственные SQL-миграции схемы БД «Казначей».

Правила (см. ТЗ, раздел 6):
- имена файлов: NNN_описание.sql;
- перед каждой миграцией создаётся бэкап;
- каждый файл выполняется в одной транзакции;
- при ошибке — откат транзакции;
- версия пишется в schema_version и дублируется в app_settings.db_version.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.dal.database import Database
from app.errors import BackupError, MigrationError  # noqa: F401 (BackupError пробрасывается вызывающему коду)
from app.services.backup import create_backup

MIGRATION_NAME_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class MigrationFile:
    version: int
    description: str
    path: Path


def find_migrations(migrations_dir: str | Path) -> list[MigrationFile]:
    """Находит и сортирует файлы миграций; проверяет строго последовательную нумерацию."""
    d = Path(migrations_dir)
    if not d.is_dir():
        raise MigrationError(f"Папка миграций не найдена: {d}")
    result: list[MigrationFile] = []
    for entry in sorted(d.iterdir()):
        m = MIGRATION_NAME_RE.match(entry.name)
        if m:
            result.append(MigrationFile(int(m.group(1)), m.group(2), entry))
    result.sort(key=lambda x: x.version)
    versions = [mf.version for mf in result]
    if versions and (versions[0] != 1 or any(b - a != 1 for a, b in zip(versions, versions[1:]))):
        raise MigrationError(f"Нумерация миграций не строго последовательная: {versions}")
    return result


def ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER NOT NULL PRIMARY KEY,"
        " applied_at TEXT NOT NULL)"
    )


def get_current_version(db: Database) -> int:
    """Текущая версия схемы; если таблицы нет — 0."""
    conn = db.connection
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    r = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(r["v"]) if r and r["v"] is not None else 0


def run_migrations(
    db: Database,
    migrations_dir: str | Path,
    backup_dir: str | Path,
) -> list[int]:
    """Применяет все миграции с версией выше текущей. Возвращает список применённых версий.

    Перед каждой миграцией создаётся бэкап kaznachei_backup_YYYYMMDD_HHMMSS_v<current>.db.
    Если бэкап создать не удалось — миграция НЕ выполняется, поднимается BackupError.
    """
    applied: list[int] = []
    migrations = find_migrations(migrations_dir)
    ensure_schema_version_table(db.connection)
    current = get_current_version(db)
    pending = [mf for mf in migrations if mf.version > current]

    for mf in pending:
        # 1. бэкап перед миграцией (при неудаче — BackupError, миграция не выполняется)
        create_backup(db.path, backup_dir, current_version=current)
        # 2. SQL в одной транзакции + запись версии
        try:
            sql = mf.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MigrationError(f"Не удалось прочитать {mf.path.name}: {exc}") from exc
        conn = db.connection
        try:
            conn.execute("BEGIN")
            cur = conn.cursor()
            # executescript делает скрытый COMMIT, поэтому режем скрипт и исполняем вручную
            _exec_sql_in_transaction(cur, sql)
            now = dt.datetime.now().isoformat(timespec="seconds")
            cur.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (mf.version, now),
            )
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise MigrationError(
                f"Миграция {mf.path.name} не выполнена, изменения откачены: {exc}"
            ) from exc
        # 3. дублируем версию в app_settings (таблица может ещё не существовать —
        # например, в тестовых миграциях без 001_init.sql; версия в таком случае
        # остаётся только в schema_version)
        try:
            db.set_setting("db_version", str(mf.version))
        except Exception:
            pass
        applied.append(mf.version)
        current = mf.version
    return applied


def _exec_sql_in_transaction(cursor: sqlite3.Cursor, sql: str) -> None:
    """Выполняет несколько SQL-операторов внутри уже открытой транзакции.

    Не использует executescript (он делает скрытый COMMIT), а режет по ';' вне
    строк/комментариев — достаточно для простых DDL/DML-скриптов миграций.
    """
    stmt: list[str] = []
    in_str: str | None = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_str:
            stmt.append(ch)
            if ch == in_str:
                # двойная кавычка внутри строки — экранирование
                if i + 1 < len(sql) and sql[i + 1] == in_str:
                    stmt.append(sql[i + 1])
                    i += 1
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
            stmt.append(ch)
        elif ch == "-" and sql[i:i + 2] == "--":
            nl = sql.find("\n", i)
            i = nl if nl != -1 else len(sql)
            continue
        elif ch == ";":
            text = "".join(stmt).strip()
            if text:
                cursor.execute(text)
            stmt = []
        else:
            stmt.append(ch)
        i += 1
    text = "".join(stmt).strip()
    if text:
        cursor.execute(text)
