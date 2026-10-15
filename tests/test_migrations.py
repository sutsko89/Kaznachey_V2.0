"""Тесты миграций схемы БД (ТЗ, раздел 6).

Покрытие: применение на пустой и старой БД, проверка итоговой схемы,
идемпотентность, откат при ошибке, бэкап перед миграцией, версия в app_settings.
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from app.dal.database import Database
from app.errors import BackupError, MigrationError
from app.services.migrations import (
    find_migrations,
    get_current_version,
    run_migrations,
)

EXPECTED_TABLES = {"categories", "transactions", "app_settings", "schema_version"}


def table_names(db: Database) -> set[str]:
    rows = db.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r["name"] for r in rows}


def make_migrations(tmp_path, files: dict[str, str]):
    """Создаёт папку миграций с указанными файлами."""
    d = tmp_path / "migrations"
    d.mkdir(exist_ok=True)
    for name, sql in files.items():
        (d / name).write_text(sql, encoding="utf-8")
    return d


class TestFindMigrations:
    def test_finds_and_sorts(self, tmp_path):
        d = make_migrations(tmp_path, {
            "002_b.sql": "SELECT 1;",
            "001_a.sql": "SELECT 1;",
        })
        mfs = find_migrations(d)
        assert [m.version for m in mfs] == [1, 2]

    def test_ignores_foreign_files(self, tmp_path):
        d = make_migrations(tmp_path, {"001_a.sql": "SELECT 1;"})
        (d / "readme.txt").write_text("не миграция", encoding="utf-8")
        (d / "01_bad.sql").write_text("SELECT 1;", encoding="utf-8")
        assert len(find_migrations(d)) == 1

    def test_non_sequential_numbering_rejected(self, tmp_path):
        d = make_migrations(tmp_path, {
            "001_a.sql": "SELECT 1;",
            "003_c.sql": "SELECT 1;",
        })
        with pytest.raises(MigrationError, match="последовательная"):
            find_migrations(d)

    def test_missing_dir(self, tmp_path):
        with pytest.raises(MigrationError):
            find_migrations(tmp_path / "nope")


class TestApplyOnEmptyDb:
    def test_all_migrations_applied(self, db_path, backup_dir):
        db = Database(db_path)
        applied = run_migrations(db, MIG := (pytest.importorskip("pathlib").Path(__file__).resolve().parents[1] / "migrations"), backup_dir)
        assert applied == [1, 2]
        assert table_names(db) >= EXPECTED_TABLES
        assert get_current_version(db) == 2
        assert db.get_setting("db_version") == "2"
        # записи о версиях в schema_version
        rows = db.connection.execute(
            "SELECT version FROM schema_version ORDER BY version").fetchall()
        assert [r["version"] for r in rows] == [1, 2]
        db.close()

    def test_final_schema_matches_tz(self, db: Database):
        """Итоговая схема соответствует ТЗ (раздел 8)."""
        cols = {c["name"] for c in db.connection.execute("PRAGMA table_info(categories)")}
        assert cols == {"id", "name", "type", "is_active", "monthly_limit", "created_at"}
        cols = {c["name"] for c in db.connection.execute("PRAGMA table_info(transactions)")}
        assert cols == {"id", "type", "amount", "category_id", "date", "description",
                        "currency", "is_template", "created_at", "updated_at"}
        cols = {c["name"] for c in db.connection.execute("PRAGMA table_info(app_settings)")}
        assert cols == {"key", "value"}
        cols = {c["name"] for c in db.connection.execute("PRAGMA table_info(schema_version)")}
        assert cols == {"version", "applied_at"}

    def test_backup_created_per_migration(self, db_path, backup_dir):
        db = Database(db_path)
        run_migrations(db, ROOT_MIGRATIONS(), backup_dir)
        backups = list(backup_dir.glob("kaznachei_backup_*.db"))
        # по одному бэкапу перед каждой из двух миграций
        assert len(backups) == 2
        names = sorted(b.name for b in backups)
        assert all(re.match(r"kaznachei_backup_\d{8}_\d{6}_v\d+\.db$", n) for n in names)
        assert names[0].endswith("_v0.db") and names[1].endswith("_v1.db")
        db.close()

    def test_backup_failure_blocks_migration(self, tmp_path, db_path):
        """Если бэкап создать не удалось — миграция НЕ выполняется (ТЗ 9.10)."""
        bad_backup_dir = tmp_path / "file-not-dir"
        bad_backup_dir.write_text("я файл, а не папка", encoding="utf-8")
        db = Database(db_path)
        with pytest.raises(BackupError):
            run_migrations(db, ROOT_MIGRATIONS(), bad_backup_dir)
        assert "categories" not in table_names(db)
        assert get_current_version(db) == 0
        db.close()


def ROOT_MIGRATIONS():
    from pathlib import Path
    return Path(__file__).resolve().parents[1] / "migrations"


class TestOldDatabaseUpgrade:
    def test_upgrade_from_partial_v1(self, db_path, backup_dir, tmp_path):
        """Старая БД версии 1 без индексов лимитов — применяется только 002."""
        conn = sqlite3.connect(str(db_path))
        v1_sql = (ROOT_MIGRATIONS() / "001_init.sql").read_text(encoding="utf-8")
        conn.executescript(v1_sql)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            " version INTEGER NOT NULL PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (1, '2026-01-01T00:00:00')")
        conn.commit()
        conn.close()

        db = Database(db_path)
        applied = run_migrations(db, ROOT_MIGRATIONS(), backup_dir)
        assert applied == [2]
        assert get_current_version(db) == 2
        assert db.get_setting("db_version") == "2"
        idx = {r["name"] for r in db.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")}
        assert "idx_transactions_type_date" in idx
        db.close()

    def test_noop_when_up_to_date(self, db: Database, backup_dir):
        before = get_current_version(db)
        files_before = list(backup_dir.glob("kaznachei_backup_*"))
        applied = run_migrations(db, ROOT_MIGRATIONS(), backup_dir)
        assert applied == []
        assert get_current_version(db) == before
        # идемпотентность: новых бэкапов не создаётся, схема не меняется
        assert list(backup_dir.glob("kaznachei_backup_*")) == files_before
        assert table_names(db) >= EXPECTED_TABLES


class TestRollbackOnError:
    def test_failed_migration_rolls_back(self, tmp_path, db_path, backup_dir):
        """Миграция с ошибочным SQL в середине скрипта: транзакция откачена."""
        mig_dir = tmp_path / "mig"
        mig_dir.mkdir()
        (mig_dir / "001_good.sql").write_text(
            "CREATE TABLE alpha (id INTEGER PRIMARY KEY);", encoding="utf-8")
        (mig_dir / "002_bad.sql").write_text(
            "CREATE TABLE beta (id INTEGER PRIMARY KEY);\n"
            "INSERT INTO nonexistent_table VALUES (1);", encoding="utf-8")

        db = Database(db_path)
        applied = run_migrations(db, mig_dir, backup_dir)
        assert applied == [1]
        with pytest.raises(MigrationError, match="002_bad"):
            run_migrations(db, mig_dir, backup_dir)
        names = table_names(db)
        assert "beta" not in names  # откат всей транзакции миграции
        assert get_current_version(db) == 1
        assert db.get_setting("db_version") == "1"
        db.close()
