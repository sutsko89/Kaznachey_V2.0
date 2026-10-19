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
from PyQt6.QtWidgets import (
    QCheckBox, QDateEdit, QHBoxLayout, QLineEdit, QPushButton, QWidget,
)

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


class DateError(Exception):
    """Некорректно введённая дата: молчаливо превращать её в отсутствие
    фильтра запрещено (FR-06)."""


class DateLineEdit(QWidget):
    """Редактируемое поле даты DD.MM.YYYY с календарём (popup по кнопке).

    В отличие от NullableDateEdit пользователь может ввести дату с клавиатуры;
    некорректный ввод НЕ трактуется как «дата не задана»: editingFinished
    валидирует содержимое, подсвечивает поле красным и выбрасывает DateError.
    value() для корректного значения возвращает ISO YYYY-MM-DD,
    для пустого — None.
    """

    ERROR_STYLE = ("border: 1px solid #b3261e; background: #fdecea;")

    def __init__(self, parent=None, initial_iso: str | None = None):
        super().__init__(parent)
        self.line = QLineEdit()
        self.line.setPlaceholderText("ДД.ММ.ГГГГ")
        self.line.setMinimumHeight(34)
        self.btn_calendar = QPushButton("🗹")
        self.btn_calendar.setObjectName("iconButton")
        self.btn_calendar.setFixedWidth(38)
        self.btn_calendar.setMinimumHeight(34)
        self.btn_calendar.setToolTip("Открыть календарь")
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat(UI_FORMAT)
        self.date_edit.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.line, stretch=1)
        lay.addWidget(self.btn_calendar)
        self.btn_calendar.clicked.connect(self._open_calendar)
        self.date_edit.dateChanged.connect(self._on_calendar_date)
        self.line.editingFinished.connect(self._validate_interactive)
        if initial_iso is not None:
            self.set_value(initial_iso)

    # ---------- API ----------
    def value(self) -> str:
        """ISO YYYY-MM-DD; при некорректном вводе — DateError (не ''!)."""
        text = self.line.text().strip()
        if text == "":
            raise DateError("Дата не указана.")
        try:
            return from_display(text)
        except ValueError:
            raise DateError(f"Некорректная дата «{text}». Формат: ДД.ММ.ГГГГ.")

    def value_or_none(self) -> str | None:
        try:
            return self.value()
        except DateError:
            return None

    def set_value(self, iso: str | None) -> None:
        self.line.setText(to_display(iso) if iso else "")
        self._set_error(False)

    def has_text(self) -> bool:
        return self.line.text().strip() != ""

    def focus_line(self) -> QLineEdit:
        return self.line

    # ---------- внутреннее ----------
    def _open_calendar(self) -> None:
        try:
            current = self.value()
            self.date_edit.setDate(iso_to_qdate(current))
        except DateError:
            self.date_edit.setDate(QDate.currentDate())
        # показать popup через временное позиционирование невидимого QDateEdit
        geo = self.btn_calendar.geometry()
        self.date_edit.setGeometry(self.btn_calendar.mapToParent(geo.topLeft()).x(),
                                   geo.bottom() + 2, 220, 24)
        self.date_edit.setVisible(True)
        self.date_edit.showPopup()

    def _on_calendar_date(self, qd: QDate) -> None:
        self.line.setText(qd.toString(UI_FORMAT))
        self._set_error(False)
        self.date_edit.setVisible(False)
        self._emit_finished()

    def _validate_interactive(self) -> None:
        ok = True
        try:
            self.value()
        except DateError:
            ok = False
        self._set_error(not ok)
        if ok:
            self._emit_finished()

    def _set_error(self, error: bool) -> None:
        self.line.setStyleSheet(self.ERROR_STYLE if error else "")

    def _emit_finished(self) -> None:
        cb = getattr(self, "on_changed", None)
        if callable(cb):
            cb()


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
        """ISO YYYY-MM-DD либо None (дата явно не задана).

        Некорректно введённая с клавиатуры дата НЕ превращается молча в
        отсутствие фильтра — выбрасывается DateError (FR-06).
        """
        if not self._enabled_cb.isChecked():
            return None
        text = self.date_edit.lineEdit().text().strip()
        if QDate.fromString(text, UI_FORMAT).isValid():
            return qdate_to_iso(self.date_edit.date())
        raise DateError(f"Некорректная дата «{text}». Формат: ДД.ММ.ГГГГ.")

    def set_error(self, error: bool) -> None:
        """Подсветка поля при ошибке ввода даты."""
        self.date_edit.setStyleSheet(
            "border: 1px solid #b3261e; background: #fdecea;" if error else "")

    def set_value(self, iso: str | None) -> None:
        if iso is None or iso == "":
            self._enabled_cb.setChecked(False)
        else:
            self._enabled_cb.setChecked(True)
            self.date_edit.setDate(iso_to_qdate(iso))
        self.set_error(False)

    def is_empty(self) -> bool:
        return not self._enabled_cb.isChecked()

    def clear(self) -> None:
        """Явная кнопка «очистить» для отдельного поля."""
        self._enabled_cb.setChecked(False)
