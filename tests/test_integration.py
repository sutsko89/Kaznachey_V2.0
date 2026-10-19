"""Интеграционный сценарий (ТЗ 7.2).

Создать тестовую БД версии 1 -> применить все миграции -> проверить схему ->
добавить категорию/операцию/шаблон -> шаблон не влияет на баланс ->
бэкап -> восстановление -> проверка данных.
"""
from __future__ import annotations

import sqlite3

from app.dal.database import Database
from app.domain.service import TreasuryService
from app.services.backup import create_backup, restore_backup
from app.services.migrations import get_current_version, run_migrations


def test_full_scenario(tmp_path):
    db_file = tmp_path / "kaznachei.db"
    backup_dir = tmp_path / "backups"
    migrations_dir = tmp_path.parent.parent / "migrations"
    if not migrations_dir.is_dir():
        from pathlib import Path as P
        migrations_dir = P(__file__).resolve().parents[1] / "migrations"

    # 1. Тестовая БД «версии 1»: только 001_init.sql + schema_version(1)
    conn = sqlite3.connect(str(db_file))
    conn.executescript((migrations_dir / "001_init.sql").read_text(encoding="utf-8"))
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version ("
                 "version INTEGER NOT NULL PRIMARY KEY, applied_at TEXT NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (1, '2026-01-01T00:00:00')")
    conn.commit()
    conn.close()

    # 2. Применяем все миграции — должна добавиться только 002
    db = Database(db_file)
    db.connect()
    applied = run_migrations(db, migrations_dir, backup_dir)
    assert applied == [2] and get_current_version(db) == 2
    assert db.get_setting("db_version") == "2"

    # 3. Проверка наличия таблиц и колонок
    tables = {r["name"] for r in db.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"categories", "transactions", "app_settings", "schema_version"} <= tables
    tx_cols = {r["name"] for r in db.connection.execute("PRAGMA table_info(transactions)")}
    assert {"is_template", "monthly_limit"} | {"currency", "updated_at"} <= \
        tx_cols | {r["name"] for r in db.connection.execute("PRAGMA table_info(categories)")}

    # 4-6. Категория, операция, шаблон
    service = TreasuryService(db, migrations_dir)
    cat = service.add_category("ЖКХ", "expense", monthly_limit=2000)
    tr = service.create_transaction(type_="expense", amount=1500,
                                    category_id=cat.id, date="2026-10-10",
                                    description="Октябрь")
    tpl = service.create_template_from_data(type_="expense", amount=9999,
                                            category_id=cat.id,
                                            description="Шаблон ЖКХ")

    # 7. Шаблон не попадает в баланс
    s = service.period_summary("2026-10-01", "2026-10-31")
    assert s["expense"] == 1500  # без 9999 из шаблона
    rows, total = service.list_transactions()
    assert total == 1 and rows[0].id == tr.id

    # 8. Бэкап
    backup = create_backup(db.path, backup_dir, current_version=2)
    assert backup.is_file()

    # 9. После бэкапа появились новые данные, затем откат
    service.create_transaction(type_="expense", amount=777, category_id=cat.id,
                               date="2026-10-11", description="После бэкапа")
    db.close()
    restore_backup(backup, db.path)

    # 10. Проверка данных после восстановления: операции «после бэкапа» нет
    db2 = Database(db_file)
    db2.connect()
    run_migrations(db2, migrations_dir, backup_dir)  # при следующем запуске
    service2 = TreasuryService(db2, migrations_dir)
    rows, total = service2.list_transactions(search="октябр")
    assert total == 1
    rows, total = service2.list_transactions(search="После бэкапа")
    assert total == 0
    templates = service2.list_templates()
    assert [t.id for t in templates] == [tpl.id]
    assert get_current_version(db2) == 2
    db2.close()
