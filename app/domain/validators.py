"""Валидация данных предметной области (без UI).

Этап 1: деньги — целые минимальные единицы (minor, копейки). Разбор строки
ввода выполняется через Decimal; float для денег запрещён. Ввод с более чем
двумя знаками после запятой отклоняется, а не округляется молча.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

from app.errors import ValidationError

TYPES = ("income", "expense")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Безопасный диапазон: до 999 999 999,99 руб. (1e11 minor) — одинаково
# проверяется в UI и доменном слое; гарантированно помещается в SQLite INTEGER.
MAX_MINOR = 10**11 - 1

_AMOUNT_RE = re.compile(
    r"^(?P<sign>-)?(?P<whole>\d{1,3}(?:[ \u2009\u00a0]\d{3})*|\d+)"
    r"(?:(?:,|\.)(?P<frac>\d{1,2}))?$"
)


def parse_amount_minor(value) -> int:
    """Строку/число -> целое число минимальных единиц (копеек).

    Допускаются пробелы как разделители тысяч и ',' или '.' как десятичный
    разделитель. Более двух знаков после запятой — ошибка (не округляем).
    Отрицательное значение здесь разрешено только для лимитов >= 0 проверкой
    выше; сумма операции должна быть строго больше нуля (validate_amount_minor).
    """
    if isinstance(value, bool):
        raise ValidationError({"amount": "Сумма должна быть числом"})
    if isinstance(value, int):
        minor = value * 100
    elif isinstance(value, Decimal):
        d = value
    else:
        text = str(value or "").strip().replace("\u2009", " ").replace("\u00a0", " ")
        text = re.sub(r"(?<=\d)[ ](?=\d{3}(\D|$))", "", text)  # пробелы-тысячные
        m = _AMOUNT_RE.match(text)
        if not m:
            raise ValidationError(
                {"amount": "Некорректная сумма. Пример: 1 234,56 (не более двух знаков после запятой)"})
        frac = (m.group("frac") or "").ljust(2, "0")
        try:
            d = Decimal(f"{m.group('whole')}.{frac}")
        except InvalidOperation:
            raise ValidationError({"amount": "Сумма должна быть числом"}) from None
        if m.group("sign"):
            d = -d
    try:
        minor = int((d * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        raise ValidationError({"amount": "Сумма должна быть числом"}) from None
    if d * 100 != minor:  # молчаливое округление запрещено
        raise ValidationError(
            {"amount": "Допускается не более двух знаков после запятой"})
    if abs(minor) > MAX_MINOR:
        raise ValidationError({"amount": "Слишком большое значение суммы"})
    return minor


def validate_amount(value) -> int:
    """Сумма операции: строго больше нуля, не более двух знаков (minor)."""
    minor = parse_amount_minor(value)
    if minor <= 0:
        raise ValidationError({"amount": "Сумма должна быть больше нуля"})
    return minor


def validate_limit(value) -> int:
    """Месячный лимит: не меньше нуля (minor); 0 = без лимита."""
    minor = parse_amount_minor(value)
    if minor < 0:
        raise ValidationError({"monthly_limit": "Лимит не может быть отрицательным"})
    return minor


def validate_date(value: str) -> str:
    """Проверяет корректную календарную дату YYYY-MM-DD. Пустая строка — ошибка."""
    if not value or not DATE_RE.match(value):
        raise ValidationError({"date": "Дата должна быть в формате YYYY-MM-DD"})
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ValidationError({"date": "Некорректная календарная дата"}) from None
    return value


def validate_type(value: str) -> str:
    if value not in TYPES:
        raise ValidationError({"type": "Тип должен быть доходом или расходом"})
    return value


def validate_category_id(value) -> int:
    try:
        cid = int(value)
    except (TypeError, ValueError):
        raise ValidationError({"category_id": "Категория обязательна"}) from None
    if cid <= 0:
        raise ValidationError({"category_id": "Категория обязательна"})
    return cid


def validate_currency(value: str) -> str:
    code = (value or "").strip().upper()
    if not code:
        code = "RUB"
    if not re.match(r"^[A-Z]{3}$", code):
        raise ValidationError({"currency": "Код валюты — 3 буквы, например RUB"})
    return code


def validate_category_fields(name: str, type_: str, monthly_limit) -> tuple[str, str, int]:
    errors: dict[str, str] = {}
    name = (name or "").strip()
    if not name:
        errors["name"] = "Название категории обязательно"
    limit = 0
    if type_ not in TYPES:
        errors["type"] = "Тип должен быть доходом или расходом"
    else:
        try:
            limit = validate_limit(monthly_limit)
        except ValidationError as exc:
            errors.update(exc.errors)
    if not errors and type_ == "income" and limit > 0:
        # для доходных категорий лимит не применяется — принудительно 0
        limit = 0
    if errors:
        raise ValidationError(errors)
    return name, type_, limit


def validate_transaction_fields(
    *, type_: str, amount, category_id, date: str, currency: str = "RUB"
) -> dict:
    """Полная валидация полей операции/шаблона. Возвращает нормализованный словарь."""
    errors: dict[str, str] = {}
    normalized: dict = {}
    for field, fn, val in (
        ("type", validate_type, type_),
        ("amount", validate_amount, amount),
        ("category_id", validate_category_id, category_id),
        ("date", validate_date, date),
        ("currency", validate_currency, currency),
    ):
        try:
            normalized[field] = fn(val)
        except ValidationError as exc:
            errors.update(exc.errors)
    if errors:
        raise ValidationError(errors)
    return normalized
