"""Тесты резервного копирования, восстановления и CSV-экспорта (ТЗ 9.8, 9.10)."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from app.dal.database import Category, Database, Transaction
from app.errors import BackupError, RestoreError
from app.services.backup import (
    backup_filename,
    create_backup,
    list_backups,
    restore_backup,
)
from app.services.export import export_transactions_csv


class TestBackupNamingAndCreation:
    def test_filename_format(self):
        moment = dt.datetime(2026, 10, 15, 14, 30, 5)
        assert backup_filename(moment, current_version=2) == \
            "kaznachei_backup_20261015_143005_v2.db"

    def test_create_backup_copies_data(self, db, service, category, backup_dir):
        service.create_transaction(type_="expense", amount=111,
                                   category_id=category.id, date="2026-10-01")
        dest = create_backup(db.path, backup_dir, current_version=2)
        assert dest.is_file()
        assert re.match(r"kaznachei_backup_\d{8}_\d{6}_v2\.db$", dest.name)
        # копия — рабочая БД с теми же данными
        other = Database_copy(dest)
        rows, total = other.list_transactions()
        assert total == 1 and rows[0].amount == 111
        other.close()

    def test_missing_db_raises(self, tmp_path, backup_dir):
        with pytest.raises(BackupError):
            create_backup(tmp_path / "nope.db", backup_dir)

    def test_unwritable_dir_raises(self, tmp_path, db_path):
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        with pytest.raises(BackupError):
            create_backup(db_path, blocker)

    def test_unique_names_on_collision(self, db, backup_dir):
        fixed = dt.datetime(2026, 10, 15, 12, 0, 0)
        p1 = create_backup(db.path, backup_dir, current_version=1, moment=fixed)
        p2 = create_backup(db.path, backup_dir, current_version=1, moment=fixed)
        assert p1 != p2 and p1.is_file() and p2.is_file()

    def test_list_backups_sorted_new_first(self, tmp_path):
        # отдельная чистая БД: в backup_dir фикстуры уже лежат миграционные бэкапы
        db = Database(tmp_path / "solo.db")
        bdir = tmp_path / "b"
        run_migrations_fixture = None  # схема не нужна — create_backup копирует файл как есть
        b1 = create_backup(db.path, bdir, current_version=1,
                           moment=dt.datetime(2026, 1, 1))
        b2 = create_backup(db.path, bdir, current_version=1,
                           moment=dt.datetime(2026, 1, 2))
        db.close()
        # mtime реальных файлов может совпадать — подстрахуемся явными times
        import os
        os.utime(b1, (b1.stat().st_atime, 1_700_000_000))
        os.utime(b2, (b2.stat().st_atime, 1_800_000_000))
        items = list_backups(bdir)
        assert [p.name for p in items] == [b2.name, b1.name]
        # посторонние файлы игнорируются
        (bdir / "notes.txt").write_text("x", encoding="utf-8")
        assert len(list_backups(bdir)) == 2

    def test_list_backups_missing_dir(self, tmp_path):
        assert list_backups(tmp_path / "none") == []


def Database_copy(path: Path):
    from app.dal.database import Database
    return Database(path)


class TestRestore:
    def test_restore_renames_current_and_copies_backup(self, db, service, category,
                                                       backup_dir, tmp_path):
        service.create_transaction(type_="expense", amount=100,
                                   category_id=category.id, date="2026-10-01")
        backup = create_backup(db.path, backup_dir, current_version=2)

        # «после бэкапа» добавили операцию — она должна исчезнуть после отката
        service.create_transaction(type_="expense", amount=999,
                                   category_id=category.id, date="2026-10-02")
        db.close()

        old_path = restore_backup(backup, db.path)
        assert old_path.name.startswith("kaznachei_old_")
        assert old_path.is_file()

        restored = Database_copy(db.path)
        rows, total = restored.list_transactions()
        assert total == 1 and rows[0].amount == 100
        restored.close()

    def test_restore_rejects_non_backup_file(self, db, tmp_path):
        fake = tmp_path / "random.db"
        fake.write_bytes(b"xx")
        db.close()
        with pytest.raises(RestoreError):
            restore_backup(fake, db.path)

    def test_restore_missing_backup(self, db, tmp_path):
        db.close()
        with pytest.raises(RestoreError):
            restore_backup(tmp_path / "absent.db", db.path)


class TestCsvExport:
    @pytest.fixture()
    def populated(self, service, category, income_category):
        service.create_transaction(type_="income", amount=1000,
                                   category_id=income_category.id,
                                   date="2026-10-01", description="Зарплата")
        service.create_transaction(type_="expense", amount=1234.5,
                                   category_id=category.id,
                                   date="2026-10-02", description='Такси; "комфорт"')
        service.create_transaction(type_="expense", amount=500,
                                   category_id=category.id, date="2026-10-03",
                                   as_template=True)  # шаблон — не экспортируется
        return service

    def export(self, service, tmp_path, **filters):
        dest = tmp_path / "export.csv"
        count = export_transactions_csv(service.db, dest, **filters)
        raw = dest.read_bytes()
        text = raw.decode("utf-8-sig")
        return count, raw, text

    def test_encoding_bom_and_separator(self, populated, tmp_path):
        count, raw, text = self.export(populated, tmp_path)
        assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM для Excel
        lines = text.splitlines()
        assert lines[0] == "дата;;тип;;категория;;сумма;;валюта;;комментарий"
        assert count == 2  # без шаблона
        assert all(";;" in ln for ln in lines)

    def test_row_content(self, populated, tmp_path):
        _, _, text = self.export(populated, tmp_path)
        data_lines = text.splitlines()[1:]
        assert data_lines[0].startswith("2026-10-01;;доход;;Зарплата;;1000,00;;RUB")
        taxi = next(ln for ln in data_lines if "Такси" in ln)
        assert ";;расход;;Продукты;;1234,50;;RUB;;" in taxi

    def test_export_respects_filters(self, populated, tmp_path):
        count, _, _ = self.export(populated, tmp_path, tx_type="income")
        assert count == 1
        count, _, _ = self.export(populated, tmp_path, search="такси")
        assert count == 1
        count, _, _ = self.export(populated, tmp_path,
                                  date_from="2026-10-02", date_to="2026-10-31")
        assert count == 1

    def test_export_all_pages_not_visible_only(self, service, category, tmp_path):
        """Выгружаются все страницы, а не только видимая таблица (page_size=25)."""
        for i in range(60):
            service.create_transaction(type_="expense", amount=1,
                                       category_id=category.id,
                                       date=f"2026-10-{(i % 28) + 1:02d}")
        dest = tmp_path / "all.csv"
        count = export_transactions_csv(service.db, dest)
        assert count == 60
        assert len(dest.read_text(encoding="utf-8-sig").splitlines()) == 61

    def test_templates_never_exported(self, service, category, tmp_path):
        service.create_transaction(type_="expense", amount=10,
                                   category_id=category.id, date="2026-10-01",
                                   as_template=True)
        dest = tmp_path / "t.csv"
        assert export_transactions_csv(service.db, dest) == 0
