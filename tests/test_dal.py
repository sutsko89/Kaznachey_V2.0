"""Unit-тесты DAL: чтение, запись, транзакции, выборки, внешние ключи, ошибки БД."""
from __future__ import annotations

import pytest

from app.dal.database import Category, Database, Transaction
from app.errors import DatabaseError


def make_tr(category_id: int, amount: float = 100.0, date: str = "2026-10-01",
            tx_type: str = "expense", template: bool = False) -> Transaction:
    return Transaction(type=tx_type, amount=amount, category_id=category_id,
                       date=date, description="тест", currency="RUB",
                       is_template=template)


class TestCategoriesCrud:
    def test_add_and_get(self, db: Database):
        cat = db.add_category(Category(name="Транспорт", type="expense", monthly_limit=500))
        assert cat.id is not None
        got = db.get_category(cat.id)
        assert got.name == "Транспорт"
        assert got.monthly_limit == 500
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
        assert got.amount == 100.0
        assert got.category_name == "Продукты"

        got.amount = 250.0
        db.update_transaction(got)
        again = db.get_transaction(tr.id)
        assert again.amount == 250.0
        assert again.updated_at is not None

        db.delete_transaction(tr.id)
        assert db.get_transaction(tr.id) is None

    def test_foreign_key_enforced(self, db: Database):
        with pytest.raises(DatabaseError):
            db.add_transaction(make_tr(999_999))

    def test_templates_flag(self, db: Database, category: Category):
        db.add_transaction(make_tr(category.id, template=True))
        rows, total = db.list_transactions(templates=False)
        assert total == 0 and rows == []
        rows, total = db.list_transactions(templates=True)
        assert total == 1


class TestQueries:
    @pytest.fixture()
    def populated(self, db: Database, category: Category, income_category: Category):
        for i in range(1, 6):
            db.add_transaction(make_tr(category.id, amount=10 * i, date=f"2026-10-0{i}"))
        db.add_transaction(make_tr(income_category.id, amount=1000,
                                   date="2026-10-03", tx_type="income"))
        db.add_transaction(make_tr(category.id, amount=777, template=True))
        return db

    def test_search_by_description_case_insensitive(self, populated: Database):
        populated.add_transaction(Transaction(
            type="expense", amount=5, category_id=populated.list_categories()[0].id,
            date="2026-10-05", description="Такси до дома"))
        rows, total = populated.list_transactions(search="такси")
        assert total == 1
        assert rows[0].description == "Такси до дома"

    def test_search_by_category_name(self, populated: Database):
        rows, total = populated.list_transactions(search="продукты")
        assert total == 5  # только операции, шаблон не попал

    def test_filters_combined(self, populated: Database):
        rows, total = populated.list_transactions(
            date_from="2026-10-02", date_to="2026-10-04", tx_type="expense")
        assert total == 3
        rows, total = populated.list_transactions(tx_type="income")
        assert total == 1

    def test_pagination(self, populated: Database):
        rows, total = populated.list_transactions(limit=2, offset=0,
                                                  order_by="date ASC, id ASC")
        assert total == 6 and len(rows) == 2
        assert rows[0].date == "2026-10-01"
        rows2, _ = populated.list_transactions(limit=2, offset=4,
                                               order_by="date ASC, id ASC")
        assert [r.date for r in rows2] == ["2026-10-04", "2026-10-05"]

    def test_invalid_order_by_falls_back(self, populated: Database):
        rows, total = populated.list_transactions(order_by="; DROP TABLE transactions; --")
        assert total == 6  # безопасное значение по умолчанию

    def test_period_totals_exclude_templates(self, populated: Database):
        s = populated.period_totals(date_from="2026-10-01", date_to="2026-10-31")
        assert s["income"] == 1000
        assert s["expense"] == 150  # без шаблона 777
        assert s["balance"] == 850

    def test_spent_by_category(self, populated: Database, category: Category):
        spent = populated.spent_by_category("2026-10")
        assert spent[category.id] == 150


class TestSettingsAndErrors:
    def test_settings_roundtrip(self, db: Database):
        assert db.get_setting("base_currency") == "RUB"
        db.set_setting("base_currency", "USD")
        assert db.get_setting("base_currency") == "USD"
        assert db.get_setting("nonexistent", "d") == "d"

    def test_transaction_context_rolls_back_on_error(self, db: Database, category: Category):
        with pytest.raises(ValueError):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO transactions (type, amount, category_id, date, created_at)"
                    " VALUES ('expense', 1, ?, '2026-01-01', 'now')", (category.id,))
                raise ValueError("boom")
        assert db.list_transactions()[1] == 0

    def test_database_error_on_bad_sql(self, db: Database):
        with pytest.raises(DatabaseError):
            with db.transaction() as conn:
                conn.execute("INSERT INTO nonexistent_table VALUES (1)")

    def test_connect_raises_when_locked(self, db_path):
        import sqlite3

        holder = sqlite3.connect(str(db_path))
        try:
            holder.execute("CREATE TABLE t(x)")
            holder.commit()
            # удерживаем эксклюзивную блокировку запуском write-транзакции
            holder.execute("BEGIN EXCLUSIVE")
            other = Database(db_path)
            with pytest.raises(RuntimeError, match="Приложение уже открыто"):
                other.connect()
        finally:
            holder.rollback()
            holder.close()
