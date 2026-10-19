"""Диалоги категории и выбора шаблона."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QComboBox, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QVBoxLayout, QFormLayout,
)

from app.dal.database import Category, Transaction
from app.domain.service import TreasuryService, format_amount_ru
from app.errors import DatabaseError, ValidationError
from app.ui.moneyedit import MoneyEdit


class CategoryDialog(QDialog):
    """Создание/редактирование категории. Подсказка «0 = без лимита»;
    блокировка смены типа при наличии операций."""

    def __init__(self, service: TreasuryService, parent=None, category: Category | None = None):
        super().__init__(parent)
        self.service = service
        self.category = category
        self.saved: Category | None = None
        self.setWindowTitle("Редактировать категорию" if category else "Новая категория")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        form = QFormLayout()
        layout.addLayout(form)

        self.name_edit = QLineEdit(category.name if category else "")
        form.addRow("Название:", self.name_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItem("Расход", "expense")
        self.type_combo.addItem("Доход", "income")
        if category:
            idx = self.type_combo.findData(category.type)
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
            tx_count = service.db.count_transactions_for_category(category.id)
            if tx_count > 0:
                self.type_combo.setEnabled(False)
                form.addRow(QLabel("Тип нельзя изменить: по категории есть операции"))
        form.addRow("Тип:", self.type_combo)

        self.limit_edit = MoneyEdit(placeholder="0 = без лимита")
        if category:
            self.limit_edit.set_minor(category.monthly_limit_minor)
        form.addRow("Месячный лимит, руб.:", self.limit_edit)
        form.addRow(QLabel("0 = без лимита. Для доходных категорий лимит не применяется."))

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red; font-size: 8pt;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        self.error_label.hide()
        name = self.name_edit.text().strip()
        type_ = self.type_combo.currentData()
        # MoneyEdit: строка -> minor units через Decimal (FR-10, без float)
        try:
            limit_minor = self.limit_edit.parse_minor()
        except ValidationError as exc:
            self.error_label.setText(exc.errors.get("amount", "Некорректное значение лимита"))
            self.error_label.show()
            return
        error = MoneyEdit.validate_minor(limit_minor, allow_zero=True)
        if error is not None:
            self.error_label.setText(error if "Лимит" in error else
                                     ("Лимит не может быть отрицательным" if limit_minor < 0 else error))
            self.error_label.show()
            return
        try:
            if self.category is not None and self.category.id is not None:
                cat = self.service.update_category(self.category.id, name, type_, limit_minor)
            else:
                cat = self.service.add_category(name, type_, limit_minor)
            self.saved = cat
            self.accept()
        except ValidationError as exc:
            self.error_label.setText(" ".join(exc.errors.values()))
            self.error_label.show()
        except DatabaseError as exc:
            QMessageBox.critical(self, "Казначей", str(exc))


class TemplatePickerDialog(QDialog):
    """Выбор шаблона для быстрой операции («По шаблону»)."""

    def __init__(self, service: TreasuryService, parent=None):
        super().__init__(parent)
        self.service = service
        self.selected: Transaction | None = None
        self.setWindowTitle("Выберите шаблон")
        self.setModal(True)

        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        for tpl in service.list_templates():
            label = f"{tpl.category_name} — {type_label_ru(tpl.type)}"
            if tpl.description:
                label += f" — {tpl.description}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, tpl.id)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Использовать")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.list_widget.itemDoubleClicked.connect(lambda *_: self._on_ok())

    def _on_ok(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            self.error_hint = "Сначала выберите шаблон из списка"
            return
        tpl_id = item.data(Qt.ItemDataRole.UserRole)
        self.selected = self.service.db.get_transaction(tpl_id)
        self.accept()


def type_label_ru(t: str) -> str:
    return "доход" if t == "income" else "расход"
