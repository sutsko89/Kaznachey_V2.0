"""Тесты производительности (ТЗ 7.3): 10 000 операций, 50 категорий.

Целевые значения: поиск/фильтрация < 0,5 с; старт приложения < 3 с.
Результаты пишутся в perf_report.txt для отслеживания деградации между версиями.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pytest

from app.dal.database import Category, Database, Transaction
from app.services.migrations import run_migrations

TX_COUNT = 10_000
CAT_COUNT = 50
SEARCH_BUDGET_S = 0.5
FILTER_BUDGET_S = 0.5
STARTUP_BUDGET_S = 3.0

REPORT_PATH = Path(__file__).resolve().parents[1] / "perf_report.txt"


@pytest.fixture(scope="module")
def big_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bigdb")
    db_path = tmp / "kaznachei.db"
    backup_dir = tmp / "backups"
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"

    db = Database(db_path)
    db.connect()
    run_migrations(db, migrations_dir, backup_dir)

    cats = []
    for i in range(CAT_COUNT):
        c = Category(name=f"Категория {i:02d}", type="expense" if i % 5 else "income",
                     monthly_limit_minor=1000000 if i % 5 == 0 else 0)
        cats.append(db.add_category(c))
    words = ["такси", "продукты", "аренда", "кофе", "зарплата", "принтер", "отчёт"]
    base = dt.date(2025, 1, 1)
    for i in range(TX_COUNT):
        cat = cats[i % CAT_COUNT]
        db.add_transaction(Transaction(
            type=cat.type,
            amount_minor=1000 + (i % 990) * 101,
            category_id=cat.id,
            date=(base + dt.timedelta(days=i % 730)).isoformat(),
            description=f"Запись {i} слово {words[i % len(words)]}",
            currency="RUB",
            is_template=(i % 500 == 0),
        ))
    yield db
    db.close()


def _timed(fn):
    t0 = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - t0


def _write_report(lines: list[str]):
    header = (
        "Отчёт о производительности «Казначей»\n"
        f"Дата: {dt.date.today().isoformat()}\n"
        f"Объём: {TX_COUNT} операций, {CAT_COUNT} категорий\n"
        "Целевая конфигурация (ТЗ): Windows 10, SSD, 8 ГБ RAM.\n"
        "Бюджеты: старт < 3 с, поиск/фильтрация < 0,5 с.\n\n"
    )
    REPORT_PATH.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def test_startup_under_budget(tmp_path):
    """Время «старта»: открыть БД + применить миграции до готовности главного окна."""
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    db_path = tmp_path / "kaznachei.db"

    def startup():
        fresh = Database(db_path)
        fresh.connect()
        run_migrations(fresh, migrations_dir, tmp_path / "backups")
        n = fresh.connection.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"]
        fresh.close()
        return n

    _, elapsed = _timed(startup)
    # второй прогон на уже мигрированной БД — типичный старт
    _, elapsed2 = _timed(startup)
    _write_report([f"Старт приложения (миграции + подготовка): {elapsed:.3f} с",
                   f"Старт приложения (без миграций): {elapsed2:.3f} с"])
    assert max(elapsed, elapsed2) < STARTUP_BUDGET_S


def test_search_fast(big_db: Database):
    (rows, total), elapsed = _timed(lambda: big_db.list_transactions(search="ТАКСИ"))
    _append(f"Поиск подстроки по {TX_COUNT} записей: {elapsed:.3f} с")
    assert total > 0
    assert elapsed < SEARCH_BUDGET_S


def test_search_mixed_case_cyrillic(big_db: Database):
    """Регистронезависимость кириллицы в поиске на стороне SQL (ТЗ 9.5)."""
    _, t_upper = big_db.list_transactions(search="ТАКСИ")
    _, t_mixed = big_db.list_transactions(search="ТакСИ")
    _, t_lower = big_db.list_transactions(search="такси")
    assert t_upper == t_mixed == t_lower > 0


def test_search_cyrillic_by_category(big_db: Database):
    _, elapsed = _timed(lambda: big_db.list_transactions(search="категория 07"))
    _append(f"Поиск по имени категории (кириллица): {elapsed:.3f} с")
    assert elapsed < SEARCH_BUDGET_S


def test_filter_combined(big_db: Database):
    def filtered():
        return big_db.list_transactions(
            date_from="2025-06-01", date_to="2026-06-01",
            tx_type="expense", limit=50, offset=0)

    (rows, total), elapsed = _timed(filtered)
    _append(f"Комбинированный фильтр + пагинация: {elapsed:.3f} с")
    assert 0 < total < TX_COUNT
    assert len(rows) == 50
    assert elapsed < FILTER_BUDGET_S


def test_table_refresh_page(big_db: Database):
    def page():
        return big_db.list_transactions(order_by="date DESC, id DESC",
                                       limit=50, offset=100)

    (rows, _), elapsed = _timed(page)
    _append(f"Обновление таблицы (страница 50 строк): {elapsed:.3f} с")
    assert len(rows) == 50
    assert elapsed < FILTER_BUDGET_S


LINES: list[str] = []


def _append(line: str):
    LINES.append(line)


@pytest.fixture(scope="module", autouse=True)
def _flush_report(request):
    yield
    if LINES:
        existing = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else ""
        REPORT_PATH.write_text(existing + "\n".join(LINES) + "\n", encoding="utf-8")
