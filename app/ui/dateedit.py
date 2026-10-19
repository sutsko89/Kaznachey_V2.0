"""Единый компонент работы с датами (FR-04/FR-05/FR-06).

В БД даты хранятся как YYYY-MM-DD, в интерфейсе — всегда DD.MM.YYYY.
Пользовательская настройка формата даты удалена: преобразование выполняет
только этот модуль. Для полей фильтра предусмотрена NullableDateEdit с
явной очисткой значения (чекбокс «не задано») — пустая граница периода
выражается значением None, а не молчаливой пустой строкой.
"""
from __future__ import annotations

import datetime as dt

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import QCheckBox, QDateEdit, QHBoxLayout, QWidget

DB_FORMAT = "yyyy-MM-dd"      # формат хранения в SQLite
UI_FORMAT = "dd.MM.yyyy"      # единственный отображаемый формат

EMPTY_MESSAGE = "Дата начала не может быть позже даты окончания."


def to_display(iso_date: str) -> str:
    """YYYY-MM-DD -> DD.MM.YYYY."""
    d = dt.date.fromisoformat(iso_date)
    return d.strftime("%d.%m.%Y")


def from_display(text: str) -> str:
    """DD.MM.YYYY -> YYYY-MM-DD; при ошибке — ValueError (без молчаливого '' )."""
    d = dt.datetime.strptime(text.strip(), "%d.%m.%Y").date()
    return d.isoformat()


def iso_to_qdate(value: str) -> QDate:
    y, m, d = (int(p) for p in value.split("-"))
    return QDate(y, m, d)


def qdate_to_iso(qd: QDate) -> str:
    return f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}"


def validate_range(date_from: str | None, date_to: str | None) -> str | None:
    """Возвращает сообщение об ошибке, если обе даты заданы и начало > конца."""
    if date_from and date_to and date_from > date_to:
        return EMPTY_MESSAGE
    return None


class NullableDateEdit(QWidget):
    """QDateEdit с календарём и возможностью явно оставить поле пустым.

    value() возвращает ISO-строку YYYY-MM-DD либо None, если дата не задана.
    """

    def __init__(self, parent=None, *, label: str = "",
                 default_today: bool = False):
        super().__init__(parent)
        self._enabled_cb = QCheckBox(label or "задано")
        self._enabled_cb.setChecked(default_today)
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat(UI_FORMAT)
        self.date_edit.setMinimumHeight(34)
        self.date_edit.setEnabled(default_today)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._enabled_cb)
        lay.addWidget(self.date_edit)
        self._enabled_cb.toggled.connect(self.date_edit.setEnabled)

    # ---------- API ----------
    def value(self) -> str | None:
        if not self._enabled_cb.isChecked():
            return None
        return qdate_to_iso(self.date_edit.date())

    def set_value(self, iso: str | None) -> None:
        if iso is None or iso == "":
            self._enabled_cb.setChecked(False)
        else:
            self._enabled_cb.setChecked(True)
            self.date_edit.setDate(iso_to_qdate(iso))

    def is_empty(self) -> bool:
        return not self._enabled_cb.isChecked()

    def clear(self) -> None:
        """Явная кнопка «очистить» для отдельного поля."""
        self._enabled_cb.setChecked(False)
