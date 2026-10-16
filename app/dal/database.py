"""Слой доступа к данным (DAL): SQLite, транзакции, выборки."""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from app.errors import DatabaseError

DB_FILENAME = "kaznachei.db"


def default_db_path() -> Path:
    """Путь к БД в пользовательском каталоге (без прав администратора)."""
    base = Path.home() / "Kaznachei"
    base.mkdir(parents=True, exist_ok=True)
    return base / DB_FILENAME


@dataclass
class Category:
    id: int | None = None
    name: str = ""
    type: str = "expense"  # income / expense
    is_active: bool = True
    monthly_limit: float = 0.0
    created_at: str = ""


@dataclass
class Transaction:
    id: int | None = None
    type: str = "expense"
    amount: float = 0.0
    category_id: int = 0
    date: str = ""
    description: str = ""
    currency: str = "RUB"
    is_template: bool = False
    created_at: str = ""
    updated_at: str | None = None
    # присоединяемые поля для отображения
    category_name: str = field(default="")
    category_type: str = field(default="")


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
                # Встроенные lower()/UPPER() в SQLite работают только для
                # ASCII — для регистронезависимого поиска (в т.ч. кириллицы)
                # регистрируем детерминированные Python-функции; поиск
                # по-прежнему выполняется на стороне SQL.
                self._conn.create_function(
                    "UNISTR_LOWER", 1, lambda s: (s or "").lower(), deterministic=True)
                self._conn.create_function(
                    "UNISTR_UPPER", 1, lambda s: (s or "").upper(), deterministic=True)
            except sqlite3.Error as exc:
                raise DatabaseError(str(exc)) from exc
        return self._conn

    def connect(self) -> None:
        """Открыть соединение; при удержании файла другим процессом — RuntimeError."""
        conn = self.connection
        try:
            # попытка эксклюзивной блокировки: второй экземпляр не должен работать с той же БД
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                raise RuntimeError("Приложение уже открыто.") from exc
            raise DatabaseError(str(exc)) from exc

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
                "INSERT INTO categories (name, type, is_active, monthly_limit, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (cat.name, cat.type, int(cat.is_active), cat.monthly_limit, cat.created_at),
            )
            cat.id = cur.lastrowid
        return cat

    def update_category(self, cat: Category) -> None:
        if cat.id is None:
            raise DatabaseError("category.id отсутствует")
        with self.transaction() as conn:
            conn.execute(
                "UPDATE categories SET name=?, type=?, is_active=?, monthly_limit=?"
                " WHERE id=?",
                (cat.name, cat.type, int(cat.is_active), cat.monthly_limit, cat.id),
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
            monthly_limit=float(row["monthly_limit"] or 0),
            created_at=row["created_at"],
        )

    # ---------- операции и шаблоны ----------
    def add_transaction(self, tr: Transaction) -> Transaction:
        tr.created_at = tr.created_at or self._now()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO transactions"
                " (type, amount, category_id, date, description, currency,"
                "  is_template, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tr.type, tr.amount, tr.category_id, tr.date, tr.description,
                    tr.currency, int(tr.is_template), tr.created_at, tr.updated_at,
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
                "UPDATE transactions SET type=?, amount=?, category_id=?, date=?,"
                " description=?, currency=?, is_template=?, updated_at=?"
                " WHERE id=?",
                (
                    tr.type, tr.amount, tr.category_id, tr.date, tr.description,
                    tr.currency, int(tr.is_template), tr.updated_at, tr.id,
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

    def list_transactions(
        self,
        *,
        templates: bool = False,
        search: str = "",
        date_from: str = "",
        date_to: str = "",
        tx_type: str = "",
        category_id: int | None = None,
        order_by: str = "date DESC, id DESC",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[Transaction], int]:
        """Возвращает (страница записей, общее количество). Шаблоны исключены, если templates=False."""
        where = ["t.is_template = ?"]
        params: list = [1 if templates else 0]
        if search:
            # Регистронезависимый поиск выполняется на стороне SQL.
            # Встроенная lower() SQLite работает только для ASCII, поэтому
            # используется зарегистрированная Python-функция UNISTR_LOWER
            # (полный Unicode-lower, включая кириллицу).
            where.append(
                "(UNISTR_LOWER(t.description) LIKE ?"
                " OR UNISTR_LOWER(c.name) LIKE ?)"
            )
            like = f"%{search.lower()}%"
            params += [like, like]
        if date_from:
            where.append("t.date >= ?")
            params.append(date_from)
        if date_to:
            where.append("t.date <= ?")
            params.append(date_to)
        if tx_type in ("income", "expense"):
            where.append("t.type = ?")
            params.append(tx_type)
        if category_id is not None:
            where.append("t.category_id = ?")
            params.append(category_id)

        allowed_orders = {
            "date DESC, id DESC", "date ASC, id ASC",
            "amount DESC, id DESC", "amount ASC, id ASC",
            "c.name COLLATE NOCASE ASC, t.date DESC", "c.name COLLATE NOCASE DESC, t.date DESC",
        }
        if order_by not in allowed_orders:
            order_by = "date DESC, id DESC"

        where_sql = " AND ".join(where)
        total = self.connection.execute(
            f"SELECT COUNT(*) AS c FROM transactions t JOIN categories c ON c.id = t.category_id"
            f" WHERE {where_sql}",
            params,
        ).fetchone()["c"]

        q = (
            "SELECT t.*, c.name AS category_name, c.type AS category_type"
            " FROM transactions t JOIN categories c ON c.id = t.category_id"
            f" WHERE {where_sql} ORDER BY {order_by}"
        )
        page_params = list(params)
        if limit is not None:
            q += " LIMIT ? OFFSET ?"
            page_params += [limit, offset]
        rows = self.connection.execute(q, page_params).fetchall()
        return [self._row_to_transaction(r) for r in rows], int(total)

    @staticmethod
    def _row_to_transaction(row: sqlite3.Row) -> Transaction:
        keys = row.keys()
        return Transaction(
            id=row["id"],
            type=row["type"],
            amount=float(row["amount"]),
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

    # ---------- агрегаты ----------
    def period_totals(self, *, date_from: str, date_to: str, templates: bool = False) -> dict:
        """Доходы/расходы/баланс за период (по операциям в базовой валюте)."""
        row = self.connection.execute(
            "SELECT"
            " COALESCE(SUM(CASE WHEN t.type='income' THEN t.amount ELSE 0 END), 0) AS income,"
            " COALESCE(SUM(CASE WHEN t.type='expense' THEN t.amount ELSE 0 END), 0) AS expense"
            " FROM transactions t WHERE t.is_template = ? AND t.date >= ? AND t.date <= ?",
            (1 if templates else 0, date_from, date_to),
        ).fetchone()
        income = float(row["income"])
        expense = float(row["expense"])
        return {"income": income, "expense": expense, "balance": income - expense}

    def daily_totals(self, *, date_from: str, date_to: str) -> list[dict]:
        """Разбивка доходов/расходов по дням периода (шаблоны не учитываются)."""
        rows = self.connection.execute(
            "SELECT date,"
            " COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0) AS income,"
            " COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0) AS expense"
            " FROM transactions WHERE is_template = 0 AND date >= ? AND date <= ?"
            " GROUP BY date ORDER BY date",
            (date_from, date_to),
        ).fetchall()
        return [
            {"date": r["date"], "income": float(r["income"]),
             "expense": float(r["expense"])}
            for r in rows
        ]

    def spent_by_category(self, month: str) -> dict[int, float]:
        """Суммы расходов по категориям за месяц 'YYYY-MM' (шаблоны не учитываются)."""
        rows = self.connection.execute(
            "SELECT category_id, SUM(amount) AS s FROM transactions"
            " WHERE is_template = 0 AND type = 'expense' AND substr(date, 1, 7) = ?"
            " GROUP BY category_id",
            (month,),
        ).fetchall()
        return {int(r["category_id"]): float(r["s"]) for r in rows}

    def category_report(self, date_from: str, date_to: str) -> list[dict]:
        """Сумма по каждой категории (тип/категория) за период."""
        rows = self.connection.execute(
            "SELECT c.id AS category_id, c.name AS name, c.type AS type,"
            " c.monthly_limit AS monthly_limit, SUM(t.amount) AS total"
            " FROM transactions t JOIN categories c ON c.id = t.category_id"
            " WHERE t.is_template = 0 AND t.date >= ? AND t.date <= ?"
            " GROUP BY c.id ORDER BY total DESC",
            (date_from, date_to),
        ).fetchall()
        return [
            {
                "category_id": int(r["category_id"]),
                "name": r["name"],
                "type": r["type"],
                "monthly_limit": float(r["monthly_limit"] or 0),
                "total": float(r["total"]),
            }
            for r in rows
        ]
