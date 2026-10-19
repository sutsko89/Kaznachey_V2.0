"""Диалог добавления/редактирования операции (и создания шаблона).

Этап 1: денежный ввод — QLineEdit + Decimal-разбор в копеечные minor units
(без float и без QDoubleSpinBox), выбор валюты удалён (FR-13), дата — через
QDateEdit с календарём в единственном формате DD.MM.YYYY (FR-04/05).
"""
from __future__ import annotations

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QComboBox, QDateEdit,
    QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout,
)

from app.dal.database import Category, Transaction
from app.domain.service import TreasuryService
from app.errors import DatabaseError, ValidationError
from app.ui.moneyedit import MoneyEdit

TYPE_INCOME, TYPE_EXPENSE = "income", "expense"


class TransactionDialog(QDialog):
    """Поля: тип, категория, сумма, дата, комментарий + чекбокс «Сохранить как шаблон»."""

    def __init__(self, service: TreasuryService, parent=None, *,
                 transaction: Transaction | None = None,
                 template_prefill: Transaction | None = None,
                 as_template: bool = False):
        super().__init__(parent)
        self.service = service
        self.transaction = transaction
        self.saved: Transaction | None = None
        self.setWindowTitle("Редактирование операции" if transaction else
                            ("Новый шаблон" if as_template else "Добавить операцию"))
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)
        layout.addLayout(form)

        # тип
        self.type_combo = QComboBox()
        self.type_combo.addItem("Расход", TYPE_EXPENSE)
        self.type_combo.addItem("Доход", TYPE_INCOME)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("Тип:", self.type_combo)

        # категория
        self.category_combo = QComboBox()
        form.addRow("Категория:", self.category_combo)

        # сумма: MoneyEdit (QLineEdit + Decimal-разбор в minor units, FR-10)
        self.amount_edit = MoneyEdit()
        form.addRow("Сумма, руб.:", self.amount_edit)

        hint = QLabel("Приложение работает только в рублях. Сумма вводится с точностью до копеек.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 9pt;")
        form.addRow(hint)

        # дата
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.date_edit.setMinimumHeight(34)
        form.addRow("Дата:", self.date_edit)

        # комментарий
        self.description_edit = QLineEdit()
        form.addRow("Комментарий:", self.description_edit)

        # компактные ошибки рядом с полями
        self.error_labels: dict[str, QLabel] = {}
        for key in ("type", "category_id", "amount", "date"):
            lbl = QLabel("")
            lbl.setStyleSheet("color: red; font-size: 8pt;")
            lbl.hide()
            self.error_labels[key] = lbl
            layout.addWidget(lbl)

        self.template_check = None
        if not as_template and transaction is None:
            from PyQt6.QtWidgets import QCheckBox
            self.template_check = QCheckBox("Сохранить как шаблон")
            layout.addWidget(self.template_check)
        elif as_template:
            self._as_template_fixed = True

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Enter — сохранить, Esc — закрыть (стандартные роли кнопок делают это сами)

        self._fill_categories()

        src = transaction or template_prefill
        if src is not None:
            idx = self.type_combo.findData(src.type)
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
            self._fill_categories()
            cidx = self.category_combo.findData(src.category_id)
            if cidx >= 0:
                self.category_combo.setCurrentIndex(cidx)
            self.amount_edit.set_minor(src.amount_minor)
            self.date_edit.setDate(QDate.fromString(src.date, "yyyy-MM-dd"))
            if template_prefill is not None:
                # при использовании шаблона дата — сегодняшняя
                self.date_edit.setDate(QDate.currentDate())
            self.description_edit.setText(src.description)
            if transaction is not None:
                self.template_check_enabled(False)

    def template_check_enabled(self, enabled: bool) -> None:
        if self.template_check is not None:
            self.template_check.setEnabled(enabled)

    def _set_type_index(self, t: str) -> None:
        idx = self.type_combo.findData(t)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)

    def _on_type_changed(self) -> None:
        self._fill_categories(keep=False)

    def _fill_categories(self, keep: bool = True) -> None:
        current = self.category_combo.currentData() if keep else None
        self.category_combo.clear()
        t = self.type_combo.currentData()
        cats: list[Category] = [c for c in self.service.list_categories(include_inactive=False)
                                if c.type == t]
        for c in cats:
            self.category_combo.addItem(c.name, c.id)
        if current is not None:
            idx = self.category_combo.findData(current)
            if idx >= 0:
                self.category_combo.setCurrentIndex(idx)

    def _clear_errors(self) -> None:
        for lbl in self.error_labels.values():
            lbl.setText("")
            lbl.hide()

    def _show_error(self, field: str, text: str) -> None:
        lbl = self.error_labels.get(field)
        if lbl is not None:
            lbl.setText(text)
            lbl.show()

    def _on_accept(self) -> None:
        self._clear_errors()
        category_id = self.category_combo.currentData()
        # MoneyEdit: строка -> minor units через Decimal (FR-10, без float)
        try:
            amount_minor = self.amount_edit.parse_minor()
        except ValidationError as exc:
            self._show_error("amount", exc.errors.get("amount", "Некорректная сумма"))
            return
        error = MoneyEdit.validate_minor(amount_minor, allow_zero=False)
        if error is not None:
            self._show_error("amount", error)
            return
        fields = dict(
            type_=self.type_combo.currentData(),
            amount_minor=amount_minor,
            category_id=category_id if category_id is not None else 0,
            date=self.date_edit.date().toString("yyyy-MM-dd"),
            description=self.description_edit.text(),
        )
        try:
            if category_id is None:
                raise ValidationError({"category_id": "Категория обязательна"})
            if self.transaction is not None:
                tr = self.service.edit_transaction(self.transaction.id, **fields)
            else:
                as_tpl = bool(self.template_check and self.template_check.isChecked()) \
                    or getattr(self, "_as_template_fixed", False)
                tr = self.service.create_transaction(**fields, as_template=as_tpl)
            self.saved = tr
            self.accept()
        except ValidationError as exc:
            for key, msg in exc.errors.items():
                self._show_error(key, msg)
        except DatabaseError as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
