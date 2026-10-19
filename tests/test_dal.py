"""Unit-тесты DAL: чтение, запись, транзакции, выборки, внешние ключи, ошибки БД.

Этап 1: деньги — целые минимальные единицы (minor); единый TransactionFilter;
single-instance реализован через QLockFile (см. tests/test_single_instance.py),
поэтому тест блокировки через BEGIN EXCLUSIVE удалён.
"""
from __future__ import annotations

import pytest

from app.dal.database import (Category, Database, Transaction,
                              TransactionFilter)
from app.errors import DatabaseError


def make_tr(category_id: int, amount_minor: int = 10000, date: str = "2026-10-01",
            tx_type: str = "expense", template: bool = False,
            description: str = "тест") -> Transaction:
    return Transaction(type=tx_type, amount_minor=amount_minor,
                       category_id=category_id, date=date,
                       description=description, currency="RUB",
                       is_template=template)


class TestCategoriesCrud:
    def test_add_and_get(self, db: Database):
        cat = db.add_category(Category(name="Транспорт", type="expense",
                                       monthly_limit_minor=50000))
        assert cat.id is not None
        got = db.get_category(cat.id)
        assert got.name == "Транспорт"
        assert got.monthly_limit_minor == 50000
        assert got.is_active is True

    def test_unique_name_type(self, db: Database):
        db.add_category(Category(name="Связь", type="expense"))
        with pytest.raises(DatabaseError):
            db.add_category(Category(name="Связь", type="expense"))
        # тот же name, другой type — разрешено
        other = db.add_category(Category(name="Связь", type="income"))
        assert other.id is not None

    def test_update_and_list(self, db: Database):
        cat = db.add_category(Category(name="Еда", type="expense"))
        cat.name = "Кафе"
        cat.is_active = False
        db.update_category(cat)
        cats = db.list_categories()
        assert len(cats) == 1
        assert cats[0].name == "Кафе"
        assert cats[0].is_active is False
        assert db.list_categories(include_inactive=False) == []

    def test_delete(self, db: Database):
        cat = db.add_category(Category(name="Временная", type="expense"))
        db.delete_category(cat.id)
        assert db.get_category(cat.id) is None


class TestTransactionsCrud:
    def test_add_get_update_delete(self, db: Database, category: Category):
        tr = db.add_transaction(make_tr(category.id))
        assert tr.id is not None
        got = db.get_transaction(tr.id)
        assert got.amount_minor == 10000
        assert got.category_name == "Продукты"

        got.amount_minor = 25000
        db.update_transaction(got)
        again = db.get_transaction(tr.id)
        assert again.amount_minor == 25000
        assert again.updated_at is not None

        db.delete_transaction(tr.id)
        assert db.get_transaction(tr.id) is None

    def test_foreign_key_enforced(self, db: Database):
        with pytest.raises(DatabaseError):
            db.add_transaction(make_tr(999_999))

    def test_templates_flag(self, db: Database, category: Category):
        db.add_transaction(make_tr(category.id, template=True))
        rows, total = db.list_transactions(TransactionFilter(templates=False))
        assert total == 0 and rows == []
        rows, total = db.list_transactions(TransactionFilter(templates=True))
        assert total == 1


class TestQueries:
    @pytest.fixture()
    def populated(self, db: Database, category: Category, income_category: Category):
        for i in range(1, 6):
            db.add_transaction(make_tr(category.id, amount_minor=1000 * i,
                                       date=f"2026-10-0{i}"))
        db.add_transaction(make_tr(income_category.id, amount_minor=100000,
                                   date="2026-10-03", tx_type="income"))
        db.add_transaction(make_tr(category.id, amount_minor=77700, template=True))
        return db

    def test_search_by_description_case_insensitive(self, populated: Database):
        populated.add_transaction(Transaction(
            type="expense", amount_minor=500,
            category_id=populated.list_categories()[0].id,
            date="2026-10-05", description="Такси до дома"))
        rows, total = populated.list_transactions(TransactionFilter(search="такси"))
        assert total == 1
        assert rows[0].description == "Такси до дома"

    def test_search_by_category_name(self, populated: Database):
        rows, total = populated.list_transactions(TransactionFilter(search="продукты"))
        assert total == 5  # только операции, шаблон не попал

    def test_filters_combined(self, populated: Database):
        rows, total = populated.list_transactions(TransactionFilter(
            date_from="2026-10-02", date_to="2026-10-04", tx_type="expense"))
        assert total == 3
        rows, total = populated.list_transactions(TransactionFilter(tx_type="income"))
        assert total == 1

    def test_pagination(self, populated: Database):
        rows, total = populated.list_transactions(
            TransactionFilter(), limit=2, offset=0, order_by="date ASC, id ASC")
        assert total == 6 and len(rows) == 2
        assert rows[0].date == "2026-10-01"
        rows2, _ = populated.list_transactions(
            TransactionFilter(), limit=2, offset=4, order_by="date ASC, id ASC")
        assert [r.date for r in rows2] == ["2026-10-04", "2026-10-05"]

    def test_invalid_order_by_falls_back(self, populated: Database):
        rows, total = populated.list_transactions(
            TransactionFilter(), order_by="; DROP TABLE transactions; --")
        assert total == 6  # безопасное значение по умолчанию

    def test_summary_equals_rows_for_every_filter(self, populated: Database,
                                                  category: Category,
                                                  income_category: Category):
        """FR-02/FR-03: список, count и агрегаты считаются одним фильтром."""
        filters = [
            TransactionFilter(),
            TransactionFilter(tx_type="expense"),
            TransactionFilter(tx_type="income"),
            TransactionFilter(category_id=category.id),
            TransactionFilter(date_from="2026-10-02", date_to="2026-10-04"),
            TransactionFilter(search="продукты", tx_type="expense"),
            TransactionFilter(tx_type="expense", category_id=category.id,
                              search="тест", date_from="2026-10-01",
                              date_to="2026-10-31"),
        ]
        for f in filters:
            rows, total = populated.list_transactions(f)
            s = populated.summary(f)
            assert s["count"] == total
            assert sum(r.amount_minor for r in rows if r.type == "income") \
                == s["income_minor"]
            assert sum(r.amount_minor for r in rows if r.type == "expense") \
                == s["expense_minor"]
            assert s["balance_minor"] == s["income_minor"] - s["expense_minor"]

    def test_spent_by_category(self, populated: Database, category: Category):
        spent = populated.spent_by_category("2026-10")
        assert spent[category.id] == 15000


class TestSettingsAndErrors:
    def test_settings_roundtrip(self, db: Database):
        assert db.get_setting("base_currency") == "RUB"
        db.set_setting("backup_path", "/tmp/x")
        assert db.get_setting("backup_path") == "/tmp/x"
        assert db.get_setting("nonexistent", "d") == "d"

    def test_transaction_context_rolls_back_on_error(self, db: Database, category: Category):
        with pytest.raises(ValueError):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO transactions (type, amount_minor, category_id,"
                    " date, created_at) VALUES ('expense', 100, ?, '2026-01-01','now')",
                    (category.id,))
                raise ValueError("boom")
        rows, total = db.list_transactions(TransactionFilter())
        assert total == 0

    def test_database_error_on_bad_sql(self, db: Database):
        with pytest.raises(DatabaseError):
            with db.transaction() as conn:
                conn.execute("INSERT INTO nonexistent_table VALUES (1)")
