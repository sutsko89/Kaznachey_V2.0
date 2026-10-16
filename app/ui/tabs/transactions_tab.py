"""Вкладка «Операции»: поиск, фильтры, таблица с пагинацией, сводка за период."""
from __future__ import annotations

import datetime as dt

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.dal.database import Transaction
from app.domain.service import TreasuryService, format_amount_ru, to_display_date
from app.errors import DatabaseError
from app.ui.theme import EXPENSE_COLOR, INCOME_COLOR


class TransactionsTab(QWidget):
    """Строку таблицы создаёт главный окно через колбэки add/edit_by_template/export."""

    def __init__(self, service: TreasuryService, parent=None):
        super().__init__(parent)
        self.service = service
        self.page = 0
        self.total = 0
        self.order_by = "date DESC, id DESC"
        self.rows_cache: list[Transaction] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # ---- верхняя панель: поиск + фильтры + кнопки ----
        top = QHBoxLayout()
        top.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск по описанию или категории…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumHeight(34)
        self.search_edit.textChanged.connect(self._on_filter_changed)
        top.addWidget(self.search_edit, stretch=2)

        self.date_from_edit = QLineEdit()
        self.date_from_edit.setPlaceholderText("с дд.мм.гггг")
        self.date_to_edit = QLineEdit()
        self.date_to_edit.setPlaceholderText("по дд.мм.гггг")
        for e in (self.date_from_edit, self.date_to_edit):
            e.setMinimumHeight(34)
            e.setMaximumWidth(120)
            e.editingFinished.connect(self._on_filter_changed)
            top.addWidget(e)

        self.type_filter = QComboBox()
        self.type_filter.setMinimumHeight(34)
        self.type_filter.addItems(["Все типы", "Доход", "Расход"])
        self.type_filter.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.type_filter)

        self.category_filter = QComboBox()
        self.category_filter.setMinimumHeight(34)
        self.category_filter.setMinimumWidth(150)
        self.category_filter.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.category_filter)

        layout.addLayout(top)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_add = QPushButton("+ Добавить")
        self.btn_add.setObjectName("primaryButton")
        self.btn_template = QPushButton("По шаблону")
        self.btn_export = QPushButton("Экспорт CSV")
        self.btn_edit = QPushButton("Редактировать")
        self.btn_delete = QPushButton("Удалить")
        self.btn_delete.setObjectName("dangerButton")
        for b in (self.btn_add, self.btn_template, self.btn_export, self.btn_edit, self.btn_delete):
            b.setMinimumHeight(34)
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        # callbacks из главного окна
        self.on_add = None
        self.on_edit = None
        self.on_use_template = None
        self.on_export = None

        # ---- таблица ----
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Дата", "Тип", "Категория", "Сумма", "Комментарий"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.doubleClicked.connect(lambda *_: self._edit_current())
        for col, order in ((0, "date"), (3, "amount")):
            hh = self.table.horizontalHeaderItem(col)
            if hh is not None:
                hh.setToolTip(f"Двойной клик по заголовку — сортировка по {order}")
        self.table.horizontalHeader().sectionDoubleClicked.connect(self._on_header_sort)
        self.btn_edit.clicked.connect(self._edit_current)
        self.btn_delete.clicked.connect(self._delete_current)
        layout.addWidget(self.table, stretch=1)

        # ---- пагинация ----
        pager = QHBoxLayout()
        self.btn_prev = QPushButton("‹ Назад")
        self.btn_next = QPushButton("Вперёд ›")
        self.page_label = QLabel("стр. 1")
        self.btn_prev.clicked.connect(self._prev_page)
        self.btn_next.clicked.connect(self._next_page)
        pager.addWidget(self.btn_prev)
        pager.addStretch(1)
        pager.addWidget(self.page_label)
        pager.addStretch(1)
        pager.addWidget(self.btn_next)
        layout.addLayout(pager)

        # ---- сводка ----
        self.summary_label = QLabel("Доходы: —  Расходы: —  Баланс: —")
        self.summary_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.summary_label)

        self.reload_categories()
        self.refresh()

    # ---------- фильтры ----------
    def page_size(self) -> int:
        try:
            return int(self.service.get_setting("page_size", "50"))
        except ValueError:
            return 50

    def reload_categories(self) -> None:
        current = self.category_filter.currentData()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("Все категории", None)
        for c in self.service.list_categories(include_inactive=True):
            self.category_filter.addItem(c.name, c.id)
        if current is not None:
            idx = self.category_filter.findData(current)
            if idx >= 0:
                self.category_filter.setCurrentIndex(idx)
        self.category_filter.blockSignals(False)

    def _on_filter_changed(self) -> None:
        self.page = 0
        self.refresh()

    def current_filters(self) -> dict:
        from app.domain.service import from_display_date

        def norm(raw: str) -> str:
            raw = raw.strip()
            if not raw:
                return ""
            if "." in raw:
                try:
                    return from_display_date(raw)
                except ValueError:
                    return ""
            return raw

        type_map = {"Все типы": "", "Доход": "income", "Расход": "expense"}
        return dict(
            search=self.search_edit.text().strip(),
            date_from=norm(self.date_from_edit.text()),
            date_to=norm(self.date_to_edit.text()),
            tx_type=type_map[self.type_filter.currentText()],
            category_id=self.category_filter.currentData(),
        )

    # ---------- данные ----------
    def refresh(self) -> None:
        try:
            rows, total = self.service.list_transactions(
                **self.current_filters(),
                order_by=self.order_by,
                limit=self.page_size(),
                offset=self.page * self.page_size(),
            )
        except DatabaseError as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return
        self.rows_cache = rows
        self.total = total
        self.table.setRowCount(len(rows))
        for i, tr in enumerate(rows):
            items = [
                to_display_date(tr.date),
                "Доход" if tr.type == "income" else "Расход",
                tr.category_name,
                format_amount_ru(tr.amount),
                tr.description,
            ]
            for j, text in enumerate(items):
                item = QTableWidgetItem(text)
                if j == 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setForeground(QColor(INCOME_COLOR if tr.type == "income"
                                              else EXPENSE_COLOR))
                self.table.setItem(i, j, item)
        pages = max(1, (total + self.page_size() - 1) // self.page_size())
        self.page_label.setText(f"стр. {self.page + 1} из {pages}")
        self.btn_prev.setEnabled(self.page > 0)
        self.btn_next.setEnabled(self.page + 1 < pages)
        self._update_summary()

    def _update_summary(self) -> None:
        f = self.current_filters()
        date_from = f["date_from"] or "1970-01-01"
        date_to = f["date_to"] or "9999-12-31"
        s = self.service.db.period_totals(date_from=date_from, date_to=date_to)
        self.summary_label.setText(
            f"Доходы: {format_amount_ru(s['income'])}   "
            f"Расходы: {format_amount_ru(s['expense'])}   "
            f"Баланс: {format_amount_ru(s['balance'])}"
        )

    # ---------- сортировка ----------
    def _on_header_sort(self, column: int) -> None:
        mapping = {
            0: ("date ASC, id ASC", "date DESC, id DESC"),
            3: ("amount ASC, id ASC", "amount DESC, id DESC"),
            2: ("c.name COLLATE NOCASE ASC, t.date DESC",
                "c.name COLLATE NOCASE DESC, t.date DESC"),
        }
        if column in mapping:
            asc, desc = mapping[column]
            self.order_by = asc if self.order_by != asc else desc
            self.page = 0
            self.refresh()

    # ---------- страницы ----------
    def _prev_page(self) -> None:
        if self.page > 0:
            self.page -= 1
            self.refresh()

    def _next_page(self) -> None:
        if (self.page + 1) * self.page_size() < self.total:
            self.page += 1
            self.refresh()

    # ---------- действия ----------
    def current_transaction(self) -> Transaction | None:
        row = self.table.currentRow()
        if 0 <= row < len(self.rows_cache):
            return self.rows_cache[row]
        return None

    def _edit_current(self) -> None:
        tr = self.current_transaction()
        if tr is not None and self.on_edit:
            self.on_edit(tr)

    def _delete_current(self) -> None:
        tr = self.current_transaction()
        if tr is None:
            return
        answer = QMessageBox.question(
            self, "Подтверждение удаления",
            self.service.confirm_text_for_delete(tr),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_transaction(tr.id)
        except DatabaseError as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return
        self.refresh()
