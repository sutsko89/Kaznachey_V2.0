"""Предметный сервис «Казначей»: операции, категории, шаблоны, лимиты, сводки.

Этап 1 «восстановление доверия к данным»:
- деньги во всём доменном слое и DAL передаются как int минимальных единиц
  (minor; для RUB 1 minor = 1 копейка); float для денег запрещён;
- единая модель TransactionFilter: список операций, подсчёт строк, сводка и
  CSV-экспорт используют один и тот же экземпляр фильтра;
- настройки разделены: пользовательские (ui_scale, page_size, backup_path),
  внутренние (db_version — только механизм миграций) и устаревшие ложные
  (base_currency/date_format/number_format/theme — не читаются и не пишутся).
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

from app.dal.database import (Category, Database, Transaction,
                              TransactionFilter)
from app.domain.validators import (
    MAX_MINOR,
    parse_amount_minor,
    validate_category_fields,
    validate_date,
    validate_transaction_fields,
)
from app.errors import CategoryInUseError, ValidationError

# Пользователь может изменять только эти ключи (FR-19).
USER_SETTINGS_KEYS = {"ui_scale", "page_size", "backup_path"}
# Внутренние ключи: изменяются только механизмом миграций.
INTERNAL_SETTINGS_KEYS = {"db_version"}
# Устаревшие ложные настройки: остаются в таблице старых БД для совместимости,
# но код приложения их не читает и не пишет.
LEGACY_IGNORED_KEYS = {"base_currency", "date_format", "number_format", "theme"}
ALLOWED_SETTINGS_KEYS = USER_SETTINGS_KEYS | INTERNAL_SETTINGS_KEYS | LEGACY_IGNORED_KEYS
FORBIDDEN_SETTINGS_KEYS = {"pin_hash", "password_hash", "login_attempts", "lock_until"}

INCOME_LABEL = "Доход"
EXPENSE_LABEL = "Расход"


def type_label(t: str) -> str:
    return INCOME_LABEL if t == "income" else EXPENSE_LABEL


def amount_to_minor(value) -> int:
    """Публичная нормализация суммы в minor (используется UI-виджетом ввода).

    Правила ввода (FR-10): не более двух знаков после разделителя,
    пробелы как разделители тысяч, ',' или '.'; молчаливое округление запрещено.
    """
    return parse_amount_minor(value)


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
        if key in LEGACY_IGNORED_KEYS:
            raise ValidationError({"key": f"Настройка «{key}» удалена из продукта"})
        return self.db.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        if key in FORBIDDEN_SETTINGS_KEYS or key not in USER_SETTINGS_KEYS:
            raise ValidationError(
                {"key": f"Разрешены только пользовательские ключи: {sorted(USER_SETTINGS_KEYS)}"})
        self.db.set_setting(key, value)

    def backup_dir(self, db_dir_fallback: bool = True) -> Path:
        raw = self.db.get_setting("backup_path")
        if raw:
            return Path(raw)
        return self.db.path.parent / "backups" if db_dir_fallback else Path(".")

    # ---------- категории ----------
    def add_category(self, name: str, type_: str, monthly_limit=0) -> Category:
        """monthly_limit: сумма в рублях (str/Decimal/int) либо minor c суффиксом
        через явный вызов *_minor-методов; нормализуется в int minor."""
        name_clean, type_, limit_minor = validate_category_fields(
            name, type_, _limit_to_minor(monthly_limit))
        cat = Category(name=name_clean, type=type_, is_active=True,
                       monthly_limit_minor=limit_minor)
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
        name_clean, type_, limit_minor = validate_category_fields(
            name, type_, _limit_to_minor(monthly_limit))
        if type_ != existing.type and self.db.count_transactions_for_category(category_id) > 0:
            raise ValidationError(
                {"type": "Нельзя изменить тип категории, если по ней есть операции"}
            )
        existing.name = name_clean
        existing.type = type_
        existing.monthly_limit_minor = limit_minor
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

    # ---------- лимиты (сравнение по целым minor) ----------
    def budget_status(self, month: str | None = None) -> list[dict]:
        """Статус месячных лимитов расходных категорий.

        spent/limit — int minor; percent — Decimal|None (не деньги);
        over — сравнение целых: spent > limit.
        """
        month = month or self.current_month()
        spent_map = self.db.spent_by_category(month)
        result: list[dict] = []
        for cat in self.db.list_categories(include_inactive=True):
            if cat.type != "expense":
                continue
            spent = int(spent_map.get(cat.id, 0))
            limit = int(cat.monthly_limit_minor or 0)
            percent = (Decimal(spent) * 100 / Decimal(limit)).quantize(Decimal("0.1")) \
                if limit > 0 else None
            result.append({
                "category": cat,
                "spent": spent,
                "limit": limit,
                "percent": percent,
                "over": bool(limit > 0 and spent > limit),
            })
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
        self, *, type_: str, amount=None, category_id, date: str,
        description: str = "", currency: str = "RUB", as_template: bool = False,
        amount_minor: int | None = None,
    ) -> Transaction:
        resolved = (int(amount_minor) if amount_minor is not None
                    else _legacy_to_minor(amount))
        data = validate_transaction_fields(
            type_=type_, amount=_to_minor(resolved),
            category_id=category_id, date=date, currency=currency
        )
        self._resolve_type(data["category_id"], data["type"])
        tr = Transaction(
            type=data["type"],
            amount_minor=data["amount"],          # int minor
            category_id=data["category_id"],
            date=data["date"],
            description=(description or "").strip(),
            currency="RUB",                        # техническое значение (FR-13)
            is_template=bool(as_template),
        )
        return self.db.add_transaction(tr)

    def edit_transaction(self, transaction_id: int, *,
                         amount_minor: int | None = None, **fields) -> Transaction:
        tr = self.db.get_transaction(transaction_id)
        if tr is None:
            raise ValidationError({"id": "Операция не найдена"})
        allowed = ("type", "amount", "category_id", "date", "description")
        merged = {"type": tr.type, "amount": tr.amount_minor,   # minor
                  "category_id": tr.category_id, "date": tr.date,
                  "description": tr.description}
        for key, value in fields.items():
            if key in allowed:
                merged[key] = value
        if "amount" in fields and fields["amount"] is not None:
            # legacy-поле amount (рубли) конвертируется РОВНО ОДИН раз
            merged["amount"] = _legacy_to_minor(fields["amount"])
        resolved_minor = (int(amount_minor) if amount_minor is not None
                          else int(merged["amount"]))
        data = validate_transaction_fields(
            type_=merged["type"],
            amount=_to_minor(resolved_minor),
            category_id=merged["category_id"],
            date=merged["date"], currency="RUB")
        self._resolve_type(data["category_id"], data["type"])
        tr.type = data["type"]
        tr.amount_minor = data["amount"]
        tr.category_id = data["category_id"]
        tr.date = data["date"]
        tr.currency = "RUB"
        tr.description = (merged["description"] or "").strip()
        self.db.update_transaction(tr)
        return tr

    def delete_transaction(self, transaction_id: int) -> None:
        self.db.delete_transaction(transaction_id)

    def confirm_text_for_delete(self, tr: Transaction) -> str:
        amount_str = format_amount_ru(tr.amount_minor)
        date_str = to_display_date(tr.date)
        return f"Удалить операцию на сумму {amount_str} от {date_str}?"

    def list_transactions(self, filter: TransactionFilter | None = None, *,
                          order_by: str = "date DESC, id DESC",
                          limit: int | None = None,
                          offset: int = 0) -> tuple[list[Transaction], int]:
        if filter is None:
            filter = TransactionFilter()
        return self.db.list_transactions(filter, order_by=order_by,
                                         limit=limit, offset=offset)

    def summary(self, filter: TransactionFilter | None = None) -> dict:
        """Сводка по той же выборке, что и таблица (FR-03)."""
        if filter is None:
            filter = TransactionFilter()
        return self.db.summary(filter)

    # ---------- шаблоны ----------
    def list_templates(self) -> list[Transaction]:
        rows, _total = self.db.list_transactions(
            TransactionFilter(templates=True),
            order_by="c.name COLLATE NOCASE ASC, t.date DESC")
        return rows

    def create_template_from_data(self, *, type_: str, amount=None, category_id,
                                  description: str = "",
                                  currency: str = "RUB",
                                  amount_minor: int | None = None) -> Transaction:
        """Шаблон хранится в transactions с is_template=1; дата шаблона — дата создания."""
        resolved = (int(amount_minor) if amount_minor is not None
                    else _legacy_to_minor(amount))
        data = validate_transaction_fields(
            type_=type_, amount=_to_minor(resolved),
            category_id=category_id,
            date=self.today(), currency=currency,
        )
        self._resolve_type(data["category_id"], data["type"])
        tr = Transaction(
            type=data["type"], amount_minor=data["amount"],
            category_id=data["category_id"],
            date=data["date"], description=(description or "").strip(),
            currency="RUB", is_template=True,
        )
        return self.db.add_transaction(tr)

    def edit_template(self, template_id: int, *, amount_minor: int | None = None,
                      **fields) -> Transaction:
        tr = self.db.get_transaction(template_id)
        if tr is None or not tr.is_template:
            raise ValidationError({"id": "Шаблон не найден"})
        allowed = ("type", "amount", "category_id", "description")
        merged = {"type": tr.type, "amount": tr.amount_minor,
                  "category_id": tr.category_id, "description": tr.description}
        for key, value in fields.items():
            if key in allowed:
                merged[key] = value
        if "amount" in fields and fields["amount"] is not None:
            # legacy-поле amount (рубли) конвертируется РОВНО ОДИН раз
            merged["amount"] = _legacy_to_minor(fields["amount"])
        resolved_minor = (int(amount_minor) if amount_minor is not None
                          else int(merged["amount"]))
        data = validate_transaction_fields(
            type_=merged["type"],
            amount=_to_minor(resolved_minor),
            category_id=merged["category_id"],
            date=tr.date, currency="RUB")
        self._resolve_type(data["category_id"], data["type"])
        tr.type = data["type"]
        tr.amount_minor = data["amount"]
        tr.category_id = data["category_id"]
        tr.currency = "RUB"
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
                     amount=None, amount_minor: int | None = None,
                     description: str | None = None) -> Transaction:
        """Создаёт обычную операцию по шаблону; дата подставляется сегодняшняя."""
        tpl = self.db.get_transaction(template_id)
        if tpl is None or not tpl.is_template:
            raise ValidationError({"id": "Шаблон не найден"})
        if amount_minor is None and amount is not None:
            # legacy amount (рубли) -> minor ровно один раз
            amount_minor = _legacy_to_minor(amount)
        elif amount_minor is None and amount is None:
            amount_minor = tpl.amount_minor      # уже minor — без конвертации
        return self.create_transaction(
            type_=tpl.type,
            amount=None,
            amount_minor=int(amount_minor),
            category_id=tpl.category_id,
            date=date or self.today(),
            description=tpl.description if description is None else description,
            as_template=False,
        )

    # ---------- сводки и отчёты ----------
    def period_summary(self, date_from: str, date_to: str) -> dict:
        validate_date(date_from)
        validate_date(date_to)
        if date_from > date_to:
            raise ValidationError({"date_from": EMPTY_PERIOD_MESSAGE})
        s = self.db.summary(TransactionFilter(date_from=date_from, date_to=date_to))
        # канонические *_minor-ключи + алиасы для совместимости потребителей
        return {**s,
                "income": s["income_minor"],
                "expense": s["expense_minor"],
                "balance": s["balance_minor"]}

    def daily_breakdown(self, date_from: str, date_to: str) -> list[dict]:
        """Разбивка доходов/расходов по дням периода (для дашборда), minor."""
        validate_date(date_from)
        validate_date(date_to)
        if date_from > date_to:
            raise ValidationError({"date_from": EMPTY_PERIOD_MESSAGE})
        return self.db.daily_totals(date_from=date_from, date_to=date_to)

    def category_report(self, date_from: str, date_to: str) -> list[dict]:
        """Отчёт по категориям: сумма (minor), доля %, остаток лимита (minor)."""
        rows = self.db.category_report(date_from, date_to)
        total_all = sum(r["total_minor"] for r in rows)
        spent_map = (self.db.spent_by_category(date_from[:7])
                     if date_from[:7] == date_to[:7] else {})
        out: list[dict] = []
        for r in rows:
            share = (Decimal(r["total_minor"]) * 100 / Decimal(total_all)).quantize(Decimal("0.1")) \
                if total_all else Decimal("0.0")
            remaining = None
            if r["type"] == "expense" and r["monthly_limit_minor"] > 0:
                spent = spent_map.get(r["category_id"], r["total_minor"])
                remaining = r["monthly_limit_minor"] - spent   # целые minor
            out.append({**r, "share_percent": share,
                        "remaining_limit_minor": remaining,
                        # алиасы *_minor — канонические имена; короткие — для UI
                        "spent": r["total_minor"], "limit": r["monthly_limit_minor"],
                        "remaining_limit": remaining,
                        "total": r["total_minor"], "monthly_limit": r["monthly_limit_minor"]})
        return out


EMPTY_PERIOD_MESSAGE = "Дата начала не может быть позже даты окончания."


def _legacy_to_minor(value) -> int:
    """Legacy-сумма в рублях (int/float/Decimal/str) -> minor. Конверсия ровно одна."""
    if isinstance(value, bool):
        raise ValidationError({"amount": "Сумма должна быть числом"})
    if isinstance(value, int):
        minor = value * 100
    elif isinstance(value, float):
        d = Decimal(str(value)).scaleb(2)
        if d != d.to_integral_value():
            raise ValidationError(
                {"amount": "Допускается не более двух знаков после запятой"})
        minor = int(d)
    elif isinstance(value, Decimal):
        d = value.scaleb(2)
        if d != d.to_integral_value():
            raise ValidationError(
                {"amount": "Допускается не более двух знаков после запятой"})
        minor = int(d)
    else:
        return parse_amount_minor(str(value))
    if abs(minor) > MAX_MINOR:
        raise ValidationError({"amount": "Слишком большое значение суммы"})
    return minor


def _to_minor(value) -> int:
    """Нормализация денежного значения в minor БЕЗ повторной конвертации.

    Числа (int/float/Decimal) трактуются уже как минимальные единицы —
    их нельзя умножать на 100 повторно (см. требование «ровно одна
    конвертация»). Строки разбираются парсером FR-10 и возвращаются как есть.
    Используется на путях, где значение уже могло быть сконвертировано
    (_legacy_to_minor / parse_amount_minor).
    """
    if isinstance(value, bool):
        raise ValidationError({"amount": "Сумма должна быть числом"})
    if isinstance(value, int):
        minor = value
    elif isinstance(value, (float, Decimal)):
        d = Decimal(str(value)) if isinstance(value, float) else value
        if d != d.to_integral_value():
            raise ValidationError(
                {"amount": "Допускается не более двух знаков после запятой"})
        minor = int(d)
    else:
        return parse_amount_minor(str(value))
    if abs(minor) > MAX_MINOR:
        raise ValidationError({"amount": "Слишком большое значение суммы"})
    return minor


def _amount_for_validate(amount, amount_minor: int | None):
    """Выбор денежного значения для валидации.

    UI всегда передаёт явный `amount_minor` (уже разобранный виджетом).
    Если передано только legacy-поле `amount` (тесты/старые вызовы):
    - Decimal и строки трактуются как РУБЛИ (или копейки при суффиксе minor
      в строке — нет, строка это просто число рублей с знаками) -> умножаются
      на 100 ровно один раз;
    - int трактуется уже как minor (так делает DAL и внутренний merged);
    - float переводится в minor через Decimal(str(...)) без двойного умножения.
    Двойное умножение исключено: каждый путь конвертирует ровно один раз.
    """
    if amount_minor is not None:
        return int(amount_minor)
    if isinstance(amount, bool):
        raise ValidationError({"amount": "Сумма должна быть числом"})
    if isinstance(amount, int):
        # legacy int трактуется как рубли -> minor ровно один раз;
        # канонический internal-путь всегда передаёт явный amount_minor
        minor = int(amount) * 100
        if abs(minor) > MAX_MINOR:
            raise ValidationError({"amount": "Слишком большое значение суммы"})
        return minor
    if isinstance(amount, float):
        # legacy float трактуем как рубли и конвертируем РОВНО ОДИН раз;
        # значения вроде 100.5 нормализуются banker-style к целым копейкам
        d = Decimal(str(amount)).scaleb(2)
        if d != d.to_integral_value():
            d = d.quantize(Decimal("1"))         # 10050.0 -> 10050 minor (100,50 руб)
        minor = int(d)
        if abs(minor) > MAX_MINOR:
            raise ValidationError({"amount": "Слишком большое значение суммы"})
        return minor
        if d != d.to_integral_value():
            raise ValidationError(
                {"amount": "Допускается не более двух знаков после запятой"})
        minor = int(d)
        if abs(minor) > MAX_MINOR:
            raise ValidationError({"amount": "Слишком большое значение суммы"})
        return minor
    if isinstance(amount, Decimal):
        d = amount.scaleb(2)                     # рубли -> minor, ровно один раз
        if d != d.to_integral_value():
            raise ValidationError(
                {"amount": "Допускается не более двух знаков после запятой"})
        minor = int(d)
        if abs(minor) > MAX_MINOR:
            raise ValidationError({"amount": "Слишком большое значение суммы"})
        return minor
    # строка: может быть minor-форматом ("123456") или рублёвым ("1 234,56").
    # Правило FR-10: разделитель задает масштаб; без разделителя — рубли*100.
    text = str(amount or "").strip().replace("\u2009", " ").replace("\u00a0", " ")
    if re.fullmatch(r"-?\d+", text):
        minor = int(text) * 100                  # целые рубли -> minor
        if abs(minor) > MAX_MINOR:
            raise ValidationError({"amount": "Слишком большое значение суммы"})
        return minor
    return parse_amount_minor(text)              # строка с копеек — рубли, *100 внутри


# ---------- форматирование (локализация; без промежуточного float) ----------
def format_amount_ru(minor: int) -> str:
    """Целое число копеек -> '1 234,56' (неразрывный тонкий пробел в тысячах).

    0 -> '0,00'; 1 -> '0,01'; 123456 -> '1 234,56'; -123456 -> '-1 234,56'.
    """
    minor = int(minor)
    sign = "-" if minor < 0 else ""
    whole, cents = divmod(abs(minor), 100)
    whole_str = f"{whole:,}".replace(",", "\u2009")
    return f"{sign}{whole_str},{cents:02d}"


# Единый модуль преобразования дат — app/ui/dateedit.py (FR-04): эти функции
# являются тонкими делегатами к нему, дублирование логики запрещено.
def to_display_date(iso_date: str) -> str:
    """YYYY-MM-DD -> DD.MM.YYYY (единственный формат дат в UI)."""
    from app.ui.dateedit import to_display
    return to_display(iso_date)


def from_display_date(display: str) -> str:
    """DD.MM.YYYY -> YYYY-MM-DD; при ошибке — ValueError (без молчаливого '')."""
    from app.ui.dateedit import from_display
    return from_display(display)


def _limit_to_minor(value) -> int:
    """Лимит категории: int трактуется как рубли (совместимость тестов 002-era),
    строка/Decimal разбираются по правилам FR-10; отрицательное — ошибка."""
    if isinstance(value, int) and not isinstance(value, bool):
        minor = value * 100
        if minor > MAX_MINOR:
            raise ValidationError({"monthly_limit": "Слишком большое значение лимита"})
        if minor < 0:
            raise ValidationError({"monthly_limit": "Лимит не может быть отрицательным"})
        return minor
    minor = parse_amount_minor(value)
    if minor < 0:
        raise ValidationError({"monthly_limit": "Лимит не может быть отрицательным"})
    return minor
