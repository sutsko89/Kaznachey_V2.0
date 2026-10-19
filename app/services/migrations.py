"""Механизм миграций схемы БД.

Миграции — SQL-файлы migrations/NNN_*.sql с непрерывной нумерацией, начиная с 001.
Версия хранится в таблице app_settings (ключ db_version) и изменяется только
этим механизмом. Перед каждой ожидающей миграцией автоматически создаётся
резервная копия; если файла БД ещё нет (первый запуск) — копировать нечего,
миграции применяются к новой базе после её создания.

FR-16: перед изменением БД проверяется содержимое устаревшей колонки currency:
записи с валютой, отличной от RUB, блокируют автоматическое продолжение
(MigrationError) — молча пересчитывать USD/EUR в рубли запрещено (FR-13).

Встроенные ресурсы: в frozen-сборке PyInstaller миграции читаются из
sys._MEIPASS (каталог, распакованный из datas), а не из папки рядом с exe;
в режиме разработки — из каталога репозитория. Подмена штатных SQL-файлов
файлами рядом с исполняемым файлом невозможна: поиск вне бандла не выполняется.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
from pathlib import Path

from app.errors import BackupError, DatabaseError, MigrationError

MIGRATION_RE = re.compile(r"^(\d{3})_[A-Za-z0-9_]+\.sql$")


def bundled_migrations_dir() -> Path:
    """Каталог миграций: ресурс пакета в dev или распакованный бандл в frozen."""
    import sys

    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        cand = base / "migrations"
        if not cand.is_dir():
            raise MigrationError(
                "Внутренний ресурс «migrations» отсутствует в сборке. "
                "Пересоберите приложение: запуск с неполной сборкой прерван, "
                "частично инициализированная база данных не создана."
            )
        return cand
    return Path(__file__).resolve().parent.parent.parent / "migrations"


def load_migrations(migrations_dir: str | Path) -> list[tuple[int, Path]]:
    d = Path(migrations_dir)
    if not d.is_dir():
        raise MigrationError(f"Каталог миграций не найден: {d}")
    result: list[tuple[int, Path]] = []
    for p in sorted(d.glob("*.sql")):
        m = MIGRATION_RE.match(p.name)
        if m:
            result.append((int(m.group(1)), p))
    versions = [v for v, _ in result]
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        raise MigrationError(
            f"Нарушена непрерывная нумерация миграций: найдено {versions}, "
            f"ожидалось {expected}"
        )
    return result


def get_current_version(db_path: str | Path) -> int:
    """Текущая версия схемы БД (0, если таблицы настроек ещё нет)."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='db_version'"
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def run_migrations(db, migrations_dir: str | Path, backup_dir: str | Path) -> list[int]:
    """Применяет все ожидающие миграции. Возвращает список применённых версий.

    Каждая ожидающая миграция выполняется в одной транзакции SQLite
    (SQLite поддерживает атомарные ALTER TABLE + UPDATE внутри BEGIN...COMMIT);
    при ошибке — полный rollback без частичных изменений схемы.
    """
    from app.services.backup import create_backup

    migrations = load_migrations(migrations_dir)
    conn = db.connection
    current = _read_version(conn)
    applied: list[int] = []
    for version, path in migrations:
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        # 1. Резервная копия до изменения (если файл БД уже существует).
        if Path(db.path).is_file() and Path(db.path).stat().st_size > 0:
            try:
                create_backup(db.path, backup_dir, current_version=current)
            except BackupError:
                raise
            except Exception as exc:
                raise BackupError(str(exc)) from exc
        # 2. FR-13: проверка foreign-currency до изменения схемы.
        _assert_rub_only(conn, version, path.name)
        # 3. Атомарное применение.
        try:
            conn.execute("BEGIN")
            try:
                _exec_migration_sql(conn, sql)
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES ('db_version', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(version),),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        except sqlite3.Error as exc:
            raise MigrationError(
                f"Миграция {path.name} не выполнена; изменения откачены: {exc}"
            ) from exc
        applied.append(version)
    return applied


def _exec_migration_sql(conn: sqlite3.Connection, sql: str) -> None:
    """Выполняет скрипт миграции внутри уже открытой транзакции.

    executescript самовольно делает COMMIT, поэтому инструкции разделяются
    вручную (миграции содержат только простые statements без триггеров).
    """
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for stmt in statements:
        conn.execute(stmt)


def _read_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='db_version'").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def _assert_rub_only(conn: sqlite3.Connection, target_version: int,
                     filename: str) -> None:
    """Если в таблице operations есть currency != 'RUB' — блокируем миграцию."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "transactions" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "currency" not in cols:
        return
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM transactions"
            " WHERE currency IS NOT NULL AND currency <> 'RUB'").fetchone()[0]
    except sqlite3.Error:
        return
    if n:
        raise MigrationError(
            f"Найдено записей с валютой, отличной от RUB: {n}. "
            "Автоматическая миграция заблокирована: мультивалютность не поддерживается, "
            "молча пересчитывать USD/EUR в рубли запрещено. "
            "Создайте резервную копию или CSV-экспорт и обратитесь к разработчику."
        )
