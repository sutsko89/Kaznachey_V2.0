"""Предметный сервис «Казначей»: операции, категории, шаблоны, лимиты, сводки."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from app.dal.database import Category, Database, Transaction
from app.domain.validators import (
    validate_category_fields,
    validate_date,
    validate_transaction_fields,
)
from app.errors import CategoryInUseError, ValidationError

ALLOWED_SETTINGS_KEYS = {
    "base_currency", "db_version", "date_format", "number_format",
    "ui_scale", "theme", "page_size", "backup_path",
}
FORBIDDEN_SETTINGS_KEYS = {"pin_hash", "password_hash", "login_attempts", "lock_until"}

INCOME_LABEL = "Доход"
EXPENSE_LABEL = "Расход"


def type_label(t: str) -> str:
    return INCOME_LABEL if t == "income" else EXPENSE_LABEL


class TreasuryService:
    """Единая точка входа бизнес-логики поверх DAL."""

    def __init__(self, db: Database, migrations_dir: str | Path):
        self.db = db
        self.migrations_dir = Path(migrations_dir)

    # ---------- даты и периоды ----------
    @staticmethod
    def today() -> str:
        return dt.date.today().isoformat()

    @staticmethod
    def current_month() -> str:
        return dt.date.today().strftime("%Y-%m")

    @staticmethod
    def month_range(month: str) -> tuple[str, str]:
        """'YYYY-MM' -> ('YYYY-MM-01', 'YYYY-MM-последний день')."""
        first = dt.date.fromisoformat(f"{month}-01")
        if first.month == 12:
            last = first.replace(year=first.year + 1, month=1, day=1) - dt.timedelta(days=1)
        else:
            last = first.replace(month=first.month + 1, day=1) - dt.timedelta(days=1)
        return first.isoformat(), last.isoformat()

    # ---------- настройки ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        if key in FORBIDDEN_SETTINGS_KEYS:
            raise ValidationError({"key": "Хранение ключей авторизации запрещено"})
        return self.db.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        if key in FORBIDDEN_SETTINGS_KEYS or key not in ALLOWED_SETTINGS_KEYS:
            raise ValidationError({"key": f"Разрешены только ключи настроек: {sorted(ALLOWED_SETTINGS_KEYS)}"})
        self.db.set_setting(key, value)

    def backup_dir(self, db_dir_fallback: bool = True) -> Path:
        raw = self.db.get_setting("backup_path")
        if raw:
            return Path(raw)
        return self.db.path.parent / "backups" if db_dir_fallback else Path(".")

    # ---------- категории ----------
    def add_category(self, name: str, type_: str, monthly_limit=0) -> Category:
        name, type_, limit = validate_category_fields(name, type_, monthly_limit)
        cat = Category(name=name, type=type_, is_active=True, monthly_limit=limit)
        try:
            return self.db.add_category(cat)
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise ValidationError(
                    {"name": "Категория с таким названием и типом уже существует"}
                ) from exc
            raise

    def update_category(
        self, category_id: int, name: str, type_: str, monthly_limit=0,
        is_active: bool | None = None,
    ) -> Category:
        existing = self.db.get_category(category_id)
        if existing is None:
            raise ValidationError({"id": "Категория не найдена"})
        name, type_, limit = validate_category_fields(name, type_, monthly_limit)
        if type_ != existing.type and self.db.count_transactions_for_category(category_id) > 0:
            raise ValidationError(
                {"type": "Нельзя изменить тип категории, если по ней есть операции"}
            )
        existing.name = name
        existing.type = type_
        existing.monthly_limit = limit
        if is_active is not None:
            existing.is_active = bool(is_active)
        self.db.update_category(existing)
        return existing

    def set_category_active(self, category_id: int, active: bool) -> None:
        cat = self.db.get_category(category_id)
        if cat is None:
            raise ValidationError({"id": "Категория не найдена"})
        cat.is_active = bool(active)
        self.db.update_category(cat)

    def delete_category(self, category_id: int) -> None:
        """Физическое удаление разрешено только для категорий без операций."""
        if self.db.count_transactions_for_category(category_id) > 0:
            raise CategoryInUseError()
        self.db.delete_category(category_id)

    def list_categories(self, include_inactive: bool = True) -> list[Category]:
        return self.db.list_categories(include_inactive=include_inactive)

    # ---------- лимиты ----------
    def budget_status(self, month: str | None = None) -> list[dict]:
        """Статус месячных лимитов расходных категорий.

        Элемент: {category, spent, limit, percent, over}.
        percent=None, если лимит 0 (без лимита).
        """
        month = month or self.current_month()
        spent_map = self.db.spent_by_category(month)
        result: list[dict] = []
        for cat in self.db.list_categories(include_inactive=True):
            if cat.type != "expense":
                continue
            spent = round(spent_map.get(cat.id, 0.0), 2)
            limit = cat.monthly_limit or 0.0
            percent = round(spent / limit * 100.0, 1) if limit > 0 else None
            result.append(
                {
                    "category": cat,
                    "spent": spent,
                    "limit": limit,
                    "percent": percent,
                    "over": bool(limit > 0 and spent > limit),
                }
            )
        # превышения — сверху списка
        result.sort(key=lambda x: (not x["over"], x["category"].name.lower()))
        return result

    def overbudget_categories(self, month: str | None = None) -> list[dict]:
        return [row for row in self.budget_status(month) if row["over"]]

    # ---------- операции ----------
    def _resolve_type(self, category_id: int, requested_type: str) -> str:
        cat = self.db.get_category(category_id)
        if cat is None:
            raise ValidationError({"category_id": "Категория не найдена"})
        if requested_type != cat.type:
            raise ValidationError(
                {"type": f"Тип операции должен совпадать с типом категории «{cat.name}» ({type_label(cat.type)})"}
            )
        return requested_type

    def create_transaction(
        self, *, type_: str, amount, category_id, date: str,
        description: str = "", currency: str = "RUB", as_template: bool = False,
    ) -> Transaction:
        data = validate_transaction_fields(
            type_=type_, amount=amount, category_id=category_id, date=date, currency=currency
        )
        self._resolve_type(data["category_id"], data["type"])
        tr = Transaction(
            type=data["type"],
            amount=data["amount"],
            category_id=data["category_id"],
            date=data["date"],
            description=(description or "").strip(),
            currency=data["currency"],
            is_template=bool(as_template),
        )
        return self.db.add_transaction(tr)

    def edit_transaction(self, transaction_id: int, **fields) -> Transaction:
        tr = self.db.get_transaction(transaction_id)
        if tr is None:
            raise ValidationError({"id": "Операция не найдена"})
        allowed = ("type", "amount", "category_id", "date", "description", "currency")
        merged = {k: getattr(tr, k) for k in allowed}
        merged["description"] = tr.description
        for key, value in fields.items():
            if key in allowed:
                merged[key] = value
        data = validate_transaction_fields(type_=merged["type"], amount=merged["amount"],
                                           category_id=merged["category_id"],
                                           date=merged["date"], currency=merged["currency"])
        self._resolve_type(data["category_id"], data["type"])
        tr.type = data["type"]
        tr.amount = data["amount"]
        tr.category_id = data["category_id"]
        tr.date = data["date"]
        tr.currency = data["currency"]
        tr.description = (merged["description"] or "").strip()
        self.db.update_transaction(tr)
        return tr

    def delete_transaction(self, transaction_id: int) -> None:
        self.db.delete_transaction(transaction_id)

    def confirm_text_for_delete(self, tr: Transaction) -> str:
        amount_str = format_amount_ru(tr.amount)
        date_str = to_display_date(tr.date)
        return f"Удалить операцию на сумму {amount_str} от {date_str}?"

    def list_transactions(self, **kwargs) -> tuple[list[Transaction], int]:
        kwargs.setdefault("templates", False)
        return self.db.list_transactions(**kwargs)

    # ---------- шаблоны ----------
    def list_templates(self) -> list[Transaction]:
        rows, _total = self.db.list_transactions(templates=True, order_by="c.name COLLATE NOCASE ASC, t.date DESC")
        return rows

    def create_template_from_data(self, *, type_: str, amount, category_id, description: str = "", currency: str = "RUB") -> Transaction:
        """Шаблон хранится в transactions с is_template=1; дата шаблона — дата создания."""
        data = validate_transaction_fields(
            type_=type_, amount=amount, category_id=category_id,
            date=self.today(), currency=currency,
        )
        self._resolve_type(data["category_id"], data["type"])
        tr = Transaction(
            type=data["type"], amount=data["amount"], category_id=data["category_id"],
            date=data["date"], description=(description or "").strip(),
            currency=data["currency"], is_template=True,
        )
        return self.db.add_transaction(tr)

    def edit_template(self, template_id: int, **fields) -> Transaction:
        tr = self.db.get_transaction(template_id)
        if tr is None or not tr.is_template:
            raise ValidationError({"id": "Шаблон не найден"})
        allowed = ("type", "amount", "category_id", "description", "currency")
        merged = {k: getattr(tr, k) for k in allowed}
        for key, value in fields.items():
            if key in allowed:
                merged[key] = value
        data = validate_transaction_fields(type_=merged["type"], amount=merged["amount"],
                                           category_id=merged["category_id"],
                                           date=tr.date, currency=merged["currency"])
        self._resolve_type(data["category_id"], data["type"])
        tr.type = data["type"]
        tr.amount = data["amount"]
        tr.category_id = data["category_id"]
        tr.currency = data["currency"]
        tr.description = (merged["description"] or "").strip()
        self.db.update_transaction(tr)
        return tr

    def delete_template(self, template_id: int) -> None:
        tr = self.db.get_transaction(template_id)
        if tr is None or not tr.is_template:
            raise ValidationError({"id": "Шаблон не найден"})
        self.db.delete_transaction(template_id)

    def confirm_text_for_delete_template(self, template: Transaction) -> str:
        name = template.description.strip() or template.category_name
        return f"Удалить шаблон «{name}»? Операции, созданные по нему ранее, останутся."

    def use_template(self, template_id: int, *, date: str | None = None,
                     amount=None, description: str | None = None) -> Transaction:
        """Создаёт обычную операцию по шаблону; дата подставляется сегодняшняя."""
        tpl = self.db.get_transaction(template_id)
        if tpl is None or not tpl.is_template:
            raise ValidationError({"id": "Шаблон не найден"})
        return self.create_transaction(
            type_=tpl.type,
            amount=tpl.amount if amount is None else amount,
            category_id=tpl.category_id,
            date=date or self.today(),
            description=tpl.description if description is None else description,
            currency=tpl.currency,
            as_template=False,
        )

    # ---------- сводки и отчёты ----------
    def period_summary(self, date_from: str, date_to: str) -> dict:
        validate_date(date_from)
        validate_date(date_to)
        if date_from > date_to:
            raise ValidationError({"date_from": "Начало периода позже его конца"})
        return self.db.period_totals(date_from=date_from, date_to=date_to)

    def category_report(self, date_from: str, date_to: str) -> list[dict]:
        """Отчёт по категориям: сумма, доля %, остаток лимита (для расходов за месяц)."""
        rows = self.db.category_report(date_from, date_to)
        total_all = sum(r["total"] for r in rows) or 0.0
        spent_map = self.db.spent_by_category(date_from[:7]) if date_from[:7] == date_to[:7] else {}
        out: list[dict] = []
        for r in rows:
            share = round(r["total"] / total_all * 100.0, 1) if total_all else 0.0
            remaining = None
            if r["type"] == "expense" and r["monthly_limit"] > 0:
                spent = spent_map.get(r["category_id"], r["total"])
                remaining = round(r["monthly_limit"] - spent, 2)
            out.append({**r, "share_percent": share, "remaining_limit": remaining})
        return out


# ---------- форматирование (локализация) ----------
def format_amount_ru(value: float) -> str:
    """12345.67 -> '12 345,67' (неразрывный пробел как разделитель тысяч)."""
    sign = "-" if value < 0 else ""
    whole, frac = divmod(abs(float(value)), 1)
    frac_cents = round(frac * 100)
    if frac_cents == 100:
        whole, frac_cents = whole + 1, 0
    whole_str = f"{int(whole):,}".replace(",", "\u2009")
    return f"{sign}{whole_str},{frac_cents:02d}"


def to_display_date(iso_date: str) -> str:
    """YYYY-MM-DD -> DD.MM.YYYY (формат дат в UI)."""
    y, m, d = iso_date.split("-")
    return f"{d}.{m}.{y}"


def from_display_date(display: str) -> str:
    """DD.MM.YYYY -> YYYY-MM-DD."""
    d, m, y = display.split(".")
    return f"{y}-{m}-{d}"
