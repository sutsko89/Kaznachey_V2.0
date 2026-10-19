"""Вкладка «Шаблоны»: таблица шаблонов, добавление/редактирование/удаление."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.dal.database import Transaction
from app.domain.service import TreasuryService, format_amount_ru
from app.errors import DatabaseError, ValidationError


class TemplatesTab(QWidget):
    def __init__(self, service: TreasuryService, parent=None):
        super().__init__(parent)
        self.service = service
        self.rows_cache: list[Transaction] = []
        self.on_add_template = None
        self.on_edit_template = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_add = QPushButton("+ Добавить")
        self.btn_add.setObjectName("primaryButton")
        self.btn_edit = QPushButton("Редактировать")
        self.btn_delete = QPushButton("Удалить")
        self.btn_delete.setObjectName("dangerButton")
        for b in (self.btn_add, self.btn_edit, self.btn_delete):
            b.setMinimumHeight(34)
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.btn_add.clicked.connect(lambda: self.on_add_template and self.on_add_template())
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_delete.clicked.connect(self._delete_selected)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Категория", "Тип", "Сумма", "Описание"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.doubleClicked.connect(lambda *_: self._edit_selected())
        layout.addWidget(self.table, stretch=1)

        self.refresh()

    def refresh(self) -> None:
        try:
            self.rows_cache = self.service.list_templates()
        except DatabaseError as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return
        self.table.setRowCount(len(self.rows_cache))
        for i, tpl in enumerate(self.rows_cache):
            cells = [
                tpl.category_name,
                "Доход" if tpl.type == "income" else "Расход",
                format_amount_ru(tpl.amount_minor),
                tpl.description,
            ]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if j == 2:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, j, item)

    def _selected_template(self) -> Transaction | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.rows_cache):
            return self.rows_cache[row]
        QMessageBox.information(self, "Казначей", "Сначала выберите шаблон.")
        return None

    def _edit_selected(self) -> None:
        tpl = None
        row = self.table.currentRow()
        if 0 <= row < len(self.rows_cache):
            tpl = self.rows_cache[row]
        else:
            QMessageBox.information(self, "Казначей", "Сначала выберите шаблон.")
            return
        if self.on_edit_template:
            self.on_edit_template(tpl)

    def _delete_selected(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self.rows_cache)):
            QMessageBox.information(self, "Казначей", "Сначала выберите шаблон.")
            return
        tpl = self.rows_cache[row]
        answer = QMessageBox.question(
            self, "Подтверждение удаления",
            self.service.confirm_text_for_delete_template(tpl),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_template(tpl.id)
        except (DatabaseError, ValidationError) as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return
        self.refresh()
