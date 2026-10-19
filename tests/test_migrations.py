"""Тесты миграций схемы БД (ТЗ, раздел 6).

Покрытие: применение на пустой и старой БД, проверка итоговой схемы,
идемпотентность, откат при ошибке, бэкап перед миграцией, версия в app_settings.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from app.dal.database import Database
from app.errors import BackupError, MigrationError
from app.services.migrations import (
    get_current_version,
    load_migrations,
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
        mfs = load_migrations(d)
        assert [v for v, _ in mfs] == [1, 2]

    def test_ignores_foreign_files(self, tmp_path):
        d = make_migrations(tmp_path, {"001_a.sql": "SELECT 1;"})
        (d / "readme.txt").write_text("не миграция", encoding="utf-8")
        (d / "01_bad.sql").write_text("SELECT 1;", encoding="utf-8")
        assert len(load_migrations(d)) == 1

    def test_non_sequential_numbering_rejected(self, tmp_path):
        d = make_migrations(tmp_path, {
            "001_a.sql": "SELECT 1;",
            "003_c.sql": "SELECT 1;",
        })
        with pytest.raises(MigrationError, match="нумерация"):
            load_migrations(d)

    def test_missing_dir(self, tmp_path):
        with pytest.raises(MigrationError):
            load_migrations(tmp_path / "nope")


class TestApplyOnEmptyDb:
    def test_all_migrations_applied(self, db_path, backup_dir):
        db = Database(db_path)
        applied = run_migrations(db, ROOT_MIGRATIONS(), backup_dir)
        assert applied == [1, 2, 3]
        assert table_names(db) >= EXPECTED_TABLES
        assert get_current_version(db.path) == 3
        assert db.get_setting("db_version") == "3"
        # записи о версиях в schema_version
        rows = db.connection.execute(
            "SELECT version FROM schema_version ORDER BY version").fetchall()
        assert [r["version"] for r in rows] == [1, 2, 3]
        db.close()

    def test_final_schema_matches_tz(self, db: Database):
        """Итоговая схема соответствует ТЗ (раздел 8)."""
        # Миграция 003 пересоздаёт таблицы и удаляет deprecated REAL-колонки
        # (amount, monthly_limit): приложение не должно их читать/писать.
        cols = {c["name"] for c in db.connection.execute("PRAGMA table_info(categories)")}
        assert cols == {"id", "name", "type", "is_active", "created_at",
                        "monthly_limit_minor"}
        cols = {c["name"] for c in db.connection.execute("PRAGMA table_info(transactions)")}
        assert cols == {"id", "type", "category_id", "date", "description",
                        "currency", "is_template", "created_at", "updated_at",
                        "amount_minor"}
        cols = {c["name"] for c in db.connection.execute("PRAGMA table_info(app_settings)")}
        assert cols == {"key", "value"}
        cols = {c["name"] for c in db.connection.execute("PRAGMA table_info(schema_version)")}
        assert cols == {"version", "applied_at"}

    def test_backup_created_per_migration(self, db_path, backup_dir):
        db = Database(db_path)
        run_migrations(db, ROOT_MIGRATIONS(), backup_dir)
        backups = list(backup_dir.glob("kaznachei_backup_*.db"))
        # по одному бэкапу перед каждой из трёх миграций
        assert len(backups) == 3
        names = sorted(b.name for b in backups)
        assert all(re.match(r"kaznachei_backup_\d{8}_\d{6}_v\d+\.db$", n) for n in names)
        assert [n.rsplit("_v", 1)[1] for n in names] == ["0.db", "1.db", "2.db"]
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
        assert applied == [2, 3]
        assert get_current_version(db.path) == 3
        assert db.get_setting("db_version") == "3"
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
        # Первая миграция применяется, вторая падает — run_migrations бросает
        # MigrationError после ROLLBACK; уже применённая 001 остаётся.
        with pytest.raises(MigrationError, match="002_bad"):
            run_migrations(db, mig_dir, backup_dir)
        names = table_names(db)
        assert "alpha" in names  # успешно применённая миграция 001 сохранена
        assert "beta" not in names  # откат всей транзакции неудачной миграции
        assert get_current_version(db) == 1
        # Повторный запуск идемпотентен: та же ошибка, состояние не меняется
        with pytest.raises(MigrationError, match="002_bad"):
            run_migrations(db, mig_dir, backup_dir)
        assert get_current_version(db) == 1
        assert "beta" not in table_names(db)
        db.close()


# ---------------------------------------------------------------------------
# Этап 1, FR-09/FR-12: миграция 003 (деньги в minor) и корректный SQL-парсер.
# Старый split(";") резал скрипт по точке с запятой внутри комментария
# ("...1 копейка; 1 234,56 руб..."), из-за чего новая БД не могла пройти
# миграции, а старая — обновиться до версии 3.
# ---------------------------------------------------------------------------

MIGDIR = Path(__file__).resolve().parent.parent / "migrations"


def _schema_v2(conn):
    """Воспроизводит схему версии 2 без minor-колонок."""
    conn.executescript((MIGDIR / "001_init.sql").read_text(encoding="utf-8"))
    conn.executescript((MIGDIR / "002_add_limits.sql").read_text(encoding="utf-8"))
    conn.execute("INSERT INTO app_settings(key,value) VALUES('db_version','2')")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER NOT NULL PRIMARY KEY, applied_at TEXT NOT NULL)")
    for v in (1, 2):
        conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at)"
            " VALUES (?, '2026-01-01T00:00:00')", (v,))
    conn.commit()


class TestSqlSplitter:
    def test_semicolon_in_comment_does_not_split(self):
        from app.services.migrations import split_sql_statements
        sql = (MIGDIR / "003_money_minor.sql").read_text(encoding="utf-8")
        stmts = split_sql_statements(sql)
        # Миграция пересоздаёт таблицы: комментарии не режутся по ";",
        # каждая инструкция — чистый исполняемый SQL.
        assert len(stmts) == 13
        for st in stmts:
            assert not st.lstrip().startswith("--")
            assert ";" not in st

    def test_semicolon_in_string_literal_preserved(self):
        from app.services.migrations import split_sql_statements
        stmts = split_sql_statements("INSERT INTO t(a) VALUES ('x;y');")
        assert stmts == ["INSERT INTO t(a) VALUES ('x;y')"]

    def test_block_comment_with_semicolon_skipped(self):
        from app.services.migrations import split_sql_statements
        assert split_sql_statements("/* a; b */ SELECT 1;") == ["SELECT 1"]


class TestMigration003MoneyMinor:
    def test_clean_db_passes_1_to_3(self, tmp_path):
        db = Database(tmp_path / "kaznachei.db")
        applied = run_migrations(db, MIGDIR, tmp_path / "backups")
        assert applied == [1, 2, 3]
        cols_t = {r[1] for r in db.connection.execute("PRAGMA table_info(transactions)")}
        cols_c = {r[1] for r in db.connection.execute("PRAGMA table_info(categories)")}
        assert "amount_minor" in cols_t
        assert "monthly_limit_minor" in cols_c

    def test_upgrade_from_v2_exact_copecity(self, tmp_path):
        """Суммы 0.01, 10.10, 999999.99 конвертируются точно; шаблоны мигрируются;
        количество операций/категорий и id сохраняются."""
        db = Database(tmp_path / "kaznachei.db")
        conn = db.connection
        _schema_v2(conn)
        conn.execute(
            "INSERT INTO categories(name,type,is_active,monthly_limit,created_at)"
            " VALUES ('Продукты','expense',1,5000.55,'2026-01-01')")
        cat_id = conn.execute("SELECT id FROM categories").fetchone()[0]
        rows = [(0.01, 0), (10.10, 0), (999999.99, 0), (55.5, 1)]
        for amt, tpl in rows:
            conn.execute(
                "INSERT INTO transactions(type,amount,category_id,date,description,"
                "currency,is_template,created_at)"
                " VALUES('expense',?,?,'2026-01-02','т','RUB',?,'2026-01-02')",
                (amt, cat_id, tpl))
        conn.commit()
        total_before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        cats_before = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        expect = {r[0]: int(round(r[1] * 100))
                  for r in conn.execute("SELECT id, amount FROM transactions")}
        conn.close()
        db._conn = None

        applied = run_migrations(db, MIGDIR, tmp_path / "backups")
        assert applied == [3]
        conn = db.connection
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == total_before
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == cats_before
        got = dict(conn.execute("SELECT id, amount_minor FROM transactions"))
        assert got == expect
        assert conn.execute(
            "SELECT monthly_limit_minor FROM categories").fetchone()[0] == 500055
        # шаблон мигрирован так же, как обычная операция
        assert conn.execute(
            "SELECT amount_minor FROM transactions WHERE is_template=1").fetchone()[0] == 5550

    def test_rerun_is_idempotent(self, tmp_path):
        db = Database(tmp_path / "kaznachei.db")
        run_migrations(db, MIGDIR, tmp_path / "backups")
        before = list(db.connection.execute(
            "SELECT id, amount_minor FROM transactions ORDER BY id"))
        assert run_migrations(db, MIGDIR, tmp_path / "backups") == []
        after = list(db.connection.execute(
            "SELECT id, amount_minor FROM transactions ORDER BY id"))
        assert before == after
        cols = [r[1] for r in db.connection.execute("PRAGMA table_info(transactions)")]
        assert cols.count("amount_minor") == 1

    def test_error_inside_003_rolls_back_columns(self, tmp_path):
        """После ошибки в 003 не должно оставаться колонок amount_minor и
        monthly_limit_minor; версия остаётся 2, данные целы."""
        db = Database(tmp_path / "kaznachei.db")
        conn = db.connection
        _schema_v2(conn)
        conn.execute(
            "INSERT INTO categories(name,type,is_active,monthly_limit,created_at)"
            " VALUES ('Тест','expense',1,10,'2026-01-01')")
        conn.execute(
            "INSERT INTO transactions(type,amount,category_id,date,currency,"
            "is_template,created_at) VALUES ('expense',7.77,1,'2026-01-02','RUB',0,'2026-01-02')")
        conn.commit()
        conn.close()
        db._conn = None

        bad = tmp_path / "migrations_bad"
        bad.mkdir()
        for f in ("001_init.sql", "002_add_limits.sql"):
            (bad / f).write_text((MIGDIR / f).read_text(encoding="utf-8"), encoding="utf-8")
        (bad / "003_money_minor.sql").write_text(
            "ALTER TABLE transactions ADD COLUMN amount_minor INTEGER NOT NULL DEFAULT 0;\n"
            "UPDATE transactions SET amount_minor = CAST(ROUND(amount * 100) AS INTEGER);\n"
            "ALTER TABLE categories ADD COLUMN monthly_limit_minor INTEGER NOT NULL DEFAULT 0;\n"
            "SELECT * FROM no_such_table_zzz;\n", encoding="utf-8")

        with pytest.raises(MigrationError):
            run_migrations(db, bad, tmp_path / "backups")

        conn = db.connection
        cols_t = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
        cols_c = {r[1] for r in conn.execute("PRAGMA table_info(categories)")}
        assert "amount_minor" not in cols_t
        assert "monthly_limit_minor" not in cols_c
        assert conn.execute(
            "SELECT value FROM app_settings WHERE key='db_version'").fetchone()[0] == "2"
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
