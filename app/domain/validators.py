"""Валидация данных предметной области (без UI)."""
from __future__ import annotations

import datetime as dt
import re

from app.errors import ValidationError

TYPES = ("income", "expense")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_date(value: str) -> str:
    """Проверяет, что значение — корректная календарная дата YYYY-MM-DD."""
    if not value or not DATE_RE.match(value):
        raise ValidationError({"date": "Дата должна быть в формате YYYY-MM-DD"})
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ValidationError({"date": "Некорректная календарная дата"}) from None
    return value


def validate_amount(value) -> float:
    """Сумма строго больше нуля."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise ValidationError({"amount": "Сумма должна быть числом"}) from None
    if amount <= 0:
        raise ValidationError({"amount": "Сумма должна быть больше нуля"})
    return amount


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


def validate_category_fields(name: str, type_: str, monthly_limit) -> tuple[str, str, float]:
    errors: dict[str, str] = {}
    name = (name or "").strip()
    if not name:
        errors["name"] = "Название категории обязательно"
    limit = 0.0
    if type_ not in TYPES:
        errors["type"] = "Тип должен быть доходом или расходом"
    try:
        limit = float(monthly_limit if monthly_limit not in (None, "") else 0)
    except (TypeError, ValueError):
        errors["monthly_limit"] = "Лимит должен быть числом (0 = без лимита)"
    if not errors and limit < 0:
        errors["monthly_limit"] = "Лимит не может быть отрицательным"
    if not errors and type_ == "income" and limit > 0:
        # для доходных категорий лимит не применяется — принудительно 0
        limit = 0.0
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
