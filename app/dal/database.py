"""Слой доступа к данным (DAL): SQLite, транзакции, выборки.

Этап 1 «восстановление доверия к данным»:
- денежные значения хранятся как целые минимальные единицы (minor, копейки);
  старые REAL-колонки amount / monthly_limit — deprecated, не читаются и не пишутся;
- единый построитель WHERE для TransactionFilter: список операций, подсчёт строк,
  агрегаты (доход/расход/баланс) и CSV-экспорт используют один и тот же фильтр;
- блокировка второго экземпляра перенесена из SQLite-транзакции в QLockFile
  (см. app/main.py); здесь BEGIN IMMEDIATE больше не используется.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.errors import DatabaseError

DB_FILENAME = "kaznachei.db"


def default_db_path() -> Path:
    """Путь к БД в пользовательском каталоге (без прав администратора)."""
    base = Path.home() / "Kaznachei"
    base.mkdir(parents=True, exist_ok=True)
    return base / DB_FILENAME


@dataclass(frozen=True)
class TransactionFilter:
    """Единая модель фильтра операций (FR-01).

    Один экземпляр передаётся в список, подсчёт, сводку и экспорт.
    date_from/date_to включаются в диапазон; None — граница не задана.
    """
    search: str = ""
    date_from: str | None = None
    date_to: str | None = None
    tx_type: str | None = None          # "income" | "expense" | None
    category_id: int | None = None
    templates: bool = False             # True — только шаблоны, False — без них


@dataclass
class Category:
    id: int | None = None
    name: str = ""
    type: str = "expense"               # income / expense
    is_active: bool = True
    monthly_limit_minor: int = 0        # копейки; 0 = без лимита
    created_at: str = ""


@dataclass
class Transaction:
    id: int | None = None
    type: str = "expense"
    amount_minor: int = 0               # копейки, строго > 0 для операций
    category_id: int = 0
    date: str = ""                      # YYYY-MM-DD
    description: str = ""
    currency: str = "RUB"               # техническое значение (ложная мультивалютность удалена)
    is_template: bool = False
    created_at: str = ""
    updated_at: str | None = None
    # присоединяемые поля для отображения
    category_name: str = ""
    category_type: str = ""


# ---------- служебное для SQL ----------

_ALLOWED_ORDERS = {
    "date DESC, id DESC", "date ASC, id ASC",
    "amount_minor DESC, id DESC", "amount_minor ASC, id ASC",
    "c.name COLLATE NOCASE ASC, t.date DESC",
    "c.name COLLATE NOCASE DESC, t.date DESC",
}


def build_filter_sql(f: TransactionFilter) -> tuple[str, list]:
    """Единственный построитель WHERE+параметров для всех запросов по фильтру.

    Возвращает ('условия', params) БЕЗ префикса WHERE. Копирование этой логики
    в отдельные SQL-методы запрещено — иначе список и агрегаты разойдутся.
    """
    where = ["t.is_template = ?"]
    params: list = [1 if f.templates else 0]
    if f.search:
        # Регистронезависимый поиск на стороне SQL, включая кириллицу:
        # встроенная lower() SQLite работает только для ASCII, поэтому
        # используется зарегистрированная Python-функция UNISTR_LOWER.
        where.append(
            "(UNISTR_LOWER(t.description) LIKE ? OR UNISTR_LOWER(c.name) LIKE ?)"
        )
        like = f"%{f.search.lower()}%"
        params += [like, like]
    if f.date_from:
        where.append("t.date >= ?")
        params.append(f.date_from)
    if f.date_to:
        where.append("t.date <= ?")
        params.append(f.date_to)
    if f.tx_type in ("income", "expense"):
        where.append("t.type = ?")
        params.append(f.tx_type)
    if f.category_id is not None:
        where.append("t.category_id = ?")
        params.append(f.category_id)
    return " AND ".join(where), params


class Database:
    """Обёртка над одним файлом SQLite."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    # ---------- служебное ----------
    @property
    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(str(self.path))
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA foreign_keys = ON")
                self._conn.create_function(
                    "UNISTR_LOWER", 1, lambda s: (s or "").lower(), deterministic=True)
                self._conn.create_function(
                    "UNISTR_UPPER", 1, lambda s: (s or "").upper(), deterministic=True)
            except sqlite3.Error as exc:
                raise DatabaseError(str(exc)) from exc
        return self._conn

    def connect(self) -> None:
        """Открыть соединение. Межпроцессная блокировка — не здесь (QLockFile в main)."""
        _ = self.connection

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    @contextmanager
    def transaction(self):
        """Все изменения БД выполняются в транзакциях."""
        conn = self.connection
        try:
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise DatabaseError(str(exc)) from exc
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def _now() -> str:
        return dt.datetime.now().isoformat(timespec="seconds")

    # ---------- настройки ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ---------- категории ----------
    def add_category(self, cat: Category) -> Category:
        cat.created_at = cat.created_at or self._now()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO categories (name, type, is_active, monthly_limit_minor,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (cat.name, cat.type, int(cat.is_active),
                 int(cat.monthly_limit_minor), cat.created_at),
            )
            cat.id = cur.lastrowid
        return cat

    def update_category(self, cat: Category) -> None:
        if cat.id is None:
            raise DatabaseError("category.id отсутствует")
        with self.transaction() as conn:
            conn.execute(
                "UPDATE categories SET name=?, type=?, is_active=?, monthly_limit_minor=?"
                " WHERE id=?",
                (cat.name, cat.type, int(cat.is_active),
                 int(cat.monthly_limit_minor), cat.id),
            )

    def delete_category(self, category_id: int) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))

    def get_category(self, category_id: int) -> Category | None:
        row = self.connection.execute(
            "SELECT * FROM categories WHERE id = ?", (category_id,)
        ).fetchone()
        return self._row_to_category(row) if row else None

    def list_categories(self, include_inactive: bool = True) -> list[Category]:
        q = "SELECT * FROM categories"
        if not include_inactive:
            q += " WHERE is_active = 1"
        q += " ORDER BY type, name COLLATE NOCASE"
        rows = self.connection.execute(q).fetchall()
        return [self._row_to_category(r) for r in rows]

    def count_transactions_for_category(self, category_id: int) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS c FROM transactions WHERE category_id = ?",
            (category_id,),
        ).fetchone()
        return int(row["c"])

    @staticmethod
    def _row_to_category(row: sqlite3.Row) -> Category:
        return Category(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            is_active=bool(row["is_active"]),
            monthly_limit_minor=int(row["monthly_limit_minor"] or 0),
            created_at=row["created_at"],
        )

    # ---------- операции и шаблоны ----------
    def add_transaction(self, tr: Transaction) -> Transaction:
        tr.created_at = tr.created_at or self._now()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO transactions"
                " (type, amount_minor, category_id, date, description, currency,"
                "  is_template, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tr.type, int(tr.amount_minor), tr.category_id, tr.date,
                    tr.description, tr.currency, int(tr.is_template),
                    tr.created_at, tr.updated_at,
                ),
            )
            tr.id = cur.lastrowid
        return tr

    def update_transaction(self, tr: Transaction) -> None:
        if tr.id is None:
            raise DatabaseError("transaction.id отсутствует")
        tr.updated_at = self._now()
        with self.transaction() as conn:
            conn.execute(
                "UPDATE transactions SET type=?, amount_minor=?, category_id=?, date=?,"
                " description=?, currency=?, is_template=?, updated_at=?"
                " WHERE id=?",
                (
                    tr.type, int(tr.amount_minor), tr.category_id, tr.date,
                    tr.description, tr.currency, int(tr.is_template),
                    tr.updated_at, tr.id,
                ),
            )

    def delete_transaction(self, transaction_id: int) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))

    def get_transaction(self, transaction_id: int) -> Transaction | None:
        row = self.connection.execute(
            "SELECT t.*, c.name AS category_name, c.type AS category_type"
            " FROM transactions t JOIN categories c ON c.id = t.category_id"
            " WHERE t.id = ?",
            (transaction_id,),
        ).fetchone()
        return self._row_to_transaction(row) if row else None

    # ---------- единый фильтр: строки + агрегаты ----------
    def list_transactions(self, filter: TransactionFilter, *,
                          order_by: str = "date DESC, id DESC",
                          limit: int | None = None,
                          offset: int = 0) -> tuple[list[Transaction], int]:
        """Возвращает (страница записей, общее количество по всему фильтру)."""
        if order_by not in _ALLOWED_ORDERS:
            order_by = "date DESC, id DESC"
        where_sql, params = build_filter_sql(filter)
        base = ("FROM transactions t JOIN categories c ON c.id = t.category_id"
                f" WHERE {where_sql}")
        total = self.connection.execute(
            f"SELECT COUNT(*) AS c {base}", params).fetchone()["c"]
        q = ("SELECT t.*, c.name AS category_name, c.type AS category_type"
             f"{base} ORDER BY {order_by}")
        page_params = list(params)
        if limit is not None:
            q += " LIMIT ? OFFSET ?"
            page_params += [limit, offset]
        rows = self.connection.execute(q, page_params).fetchall()
        return [self._row_to_transaction(r) for r in rows], int(total)

    def summary(self, filter: TransactionFilter) -> dict:
        """Агрегаты по ТОМУ ЖЕ фильтру, что и список (FR-03). Денежные — int minor."""
        where_sql, params = build_filter_sql(filter)
        row = self.connection.execute(
            "SELECT"
            " COALESCE(SUM(CASE WHEN t.type='income' THEN t.amount_minor ELSE 0 END), 0)"
            "   AS income_minor,"
            " COALESCE(SUM(CASE WHEN t.type='expense' THEN t.amount_minor ELSE 0 END), 0)"
            "   AS expense_minor,"
            " COUNT(*) AS cnt"
            " FROM transactions t JOIN categories c ON c.id = t.category_id"
            f" WHERE {where_sql}",
            params,
        ).fetchone()
        income = int(row["income_minor"])
        expense = int(row["expense_minor"])
        return {
            "income_minor": income,
            "expense_minor": expense,
            "balance_minor": income - expense,
            "count": int(row["cnt"]),
        }

    @staticmethod
    def _row_to_transaction(row: sqlite3.Row) -> Transaction:
        keys = row.keys()
        return Transaction(
            id=row["id"],
            type=row["type"],
            amount_minor=int(row["amount_minor"]),
            category_id=row["category_id"],
            date=row["date"],
            description=row["description"] or "",
            currency=row["currency"] or "RUB",
            is_template=bool(row["is_template"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            category_name=row["category_name"] if "category_name" in keys else "",
            category_type=row["category_type"] if "category_type" in keys else "",
        )

    # ---------- агрегаты для дашборда (совместимость с integer money) ----------
    def period_totals(self, *, date_from: str, date_to: str,
                      templates: bool = False) -> dict:
        """Доходы/расходы/баланс за период в minor. Частный случай TransactionFilter."""
        f = TransactionFilter(date_from=date_from, date_to=date_to,
                              templates=templates)
        s = self.summary(f)
        return {
            "income_minor": s["income_minor"],
            "expense_minor": s["expense_minor"],
            "balance_minor": s["balance_minor"],
        }

    def daily_totals(self, *, date_from: str, date_to: str) -> list[dict]:
        """Разбивка доходов/расходов по дням периода (шаблоны исключены), minor."""
        rows = self.connection.execute(
            "SELECT date,"
            " COALESCE(SUM(CASE WHEN type='income' THEN amount_minor ELSE 0 END), 0)"
            "   AS income_minor,"
            " COALESCE(SUM(CASE WHEN type='expense' THEN amount_minor ELSE 0 END), 0)"
            "   AS expense_minor"
            " FROM transactions WHERE is_template = 0 AND date >= ? AND date <= ?"
            " GROUP BY date ORDER BY date",
            (date_from, date_to),
        ).fetchall()
        return [
            {"date": r["date"], "income_minor": int(r["income_minor"]),
             "expense_minor": int(r["expense_minor"])}
            for r in rows
        ]

    def spent_by_category(self, month: str) -> dict[int, int]:
        """Суммы расходов по категориям за месяц 'YYYY-MM' в minor (без шаблонов)."""
        rows = self.connection.execute(
            "SELECT category_id, SUM(amount_minor) AS s FROM transactions"
            " WHERE is_template = 0 AND type = 'expense' AND substr(date, 1, 7) = ?"
            " GROUP BY category_id",
            (month,),
        ).fetchall()
        return {int(r["category_id"]): int(r["s"]) for r in rows}

    def category_report(self, date_from: str, date_to: str) -> list[dict]:
        """Сумма по каждой категории (тип/категория) за период; деньги — minor."""
        rows = self.connection.execute(
            "SELECT c.id AS category_id, c.name AS name, c.type AS type,"
            " c.monthly_limit_minor AS monthly_limit_minor,"
            " SUM(t.amount_minor) AS total_minor"
            " FROM transactions t JOIN categories c ON c.id = t.category_id"
            " WHERE t.is_template = 0 AND t.date >= ? AND t.date <= ?"
            " GROUP BY c.id ORDER BY total_minor DESC",
            (date_from, date_to),
        ).fetchall()
        return [
            {
                "category_id": int(r["category_id"]),
                "name": r["name"],
                "type": r["type"],
                "monthly_limit_minor": int(r["monthly_limit_minor"] or 0),
                "total_minor": int(r["total_minor"]),
            }
            for r in rows
        ]
