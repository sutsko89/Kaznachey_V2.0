"""MoneyEdit — денежный ввод на базе QLineEdit без float и QDoubleSpinBox.

Ввод разбирается через Decimal (app.domain.validators.parse_amount_minor),
значение сервису передаётся только как amount_minor: int (копейки).
Обратное форматирование — format_amount_ru (целое minor -> «1 234,56»),
без промежуточного float. Приложение работает только в рублях (FR-13).
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLineEdit

from app.domain.service import format_amount_ru
from app.domain.validators import MAX_MINOR, parse_amount_minor
from app.errors import ValidationError


class MoneyEdit(QLineEdit):
    """Строковое поле суммы в рублях с точностью до копеек."""

    def __init__(self, parent=None, *, placeholder: str = "Например: 1 234,56"):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setMinimumHeight(34)

    # ---------- чтение ----------
    def parse_minor(self) -> int:
        """Строка ввода -> int минимальных единиц. Бросает ValidationError."""
        return parse_amount_minor(self.text())

    def minor_or_none(self) -> int | None:
        try:
            return self.parse_minor()
        except ValidationError:
            return None

    # ---------- запись ----------
    def set_minor(self, minor: int) -> None:
        """Установить значение из целых минимальных единиц (без float)."""
        self.setText(format_amount_ru(int(minor)))

    @staticmethod
    def validate_minor(minor: int, *, allow_zero: bool) -> str | None:
        """Проверка диапазона; возвращает текст ошибки или None."""
        if minor is None:
            return "Некорректная сумма"
        if not allow_zero and minor <= 0:
            return "Сумма должна быть больше нуля"
        if allow_zero and minor < 0:
            return "Лимит не может быть отрицательным"
        if minor > MAX_MINOR:
            return "Слишком большое значение суммы"
        return None
