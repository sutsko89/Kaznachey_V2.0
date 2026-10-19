"""Вкладка «Операции»: поиск, фильтры, таблица с пагинацией, сводка по выборке.

Единая модель фильтра (FR-01): один объект TransactionFilter создаётся в
current_filter() и передаётся в список операций, подсчёт строк, сводку и
CSV-экспорт. Копирование логики фильтра запрещено.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.dal.database import Transaction, TransactionFilter
from app.domain.service import TreasuryService, format_amount_ru, to_display_date
from app.errors import DatabaseError
from app.ui.dateedit import NullableDateEdit, validate_range
from app.ui.theme import EXPENSE_COLOR, INCOME_COLOR


class TransactionsTab(QWidget):
    """Строки таблицы создаёт главное окно через колбэки add/edit_by_template/export."""

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

        self.date_from_edit = NullableDateEdit(label="с")
        self.date_to_edit = NullableDateEdit(label="по")
        for e in (self.date_from_edit, self.date_to_edit):
            e.date_edit.dateChanged.connect(self._on_filter_changed)
            top.addWidget(e)

        self.btn_reset_period = QPushButton("Сбросить период")
        self.btn_reset_period.setMinimumHeight(34)
        self.btn_reset_period.clicked.connect(self._reset_period)
        top.addWidget(self.btn_reset_period)

        self.type_filter = QComboBox()
        self.type_filter.setMinimumHeight(34)
        self.type_filter.addItem("Все типы", None)
        self.type_filter.addItem("Доход", "income")
        self.type_filter.addItem("Расход", "expense")
        self.type_filter.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.type_filter)

        self.category_filter = QComboBox()
        self.category_filter.setMinimumHeight(34)
        self.category_filter.setMinimumWidth(150)
        self.category_filter.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.category_filter)

        layout.addLayout(top)

        # компактное сообщение об ошибке периода под фильтрами (FR-06)
        self.date_error_label = QLabel("")
        self.date_error_label.setStyleSheet("color: #b3261e; font-weight: bold;")
        self.date_error_label.hide()
        layout.addWidget(self.date_error_label)

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
        self.table.horizontalHeader().sectionDoubleClicked.connect(self._on_header_sort)
        self.table.itemSelectionChanged.connect(self._update_row_buttons)
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

        # ---- сводка по текущей выборке (FR-03) ----
        self.summary_label = QLabel(
            "По текущей выборке: Доходы: 0,00   Расходы: 0,00   Баланс: 0,00   Операций: 0"
        )
        self.summary_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.summary_label)

        self.reload_categories()
        self.refresh()

    # ---------- единый фильтр ----------
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

    def current_filter(self) -> TransactionFilter:
        """Единственный источник условий для таблицы, сводки и экспорта (FR-01)."""
        return TransactionFilter(
            search=self.search_edit.text().strip(),
            date_from=self.date_from_edit.value(),
            date_to=self.date_to_edit.value(),
            tx_type=self.type_filter.currentData(),
            category_id=self.category_filter.currentData(),
            templates=False,
        )

    def _period_error(self) -> str | None:
        return validate_range(self.date_from_edit.value(), self.date_to_edit.value())

    def _reset_period(self) -> None:
        self.date_from_edit.clear()
        self.date_to_edit.clear()
        self._on_filter_changed()

    def _on_filter_changed(self) -> None:
        self.page = 0
        self.refresh()

    # ---------- данные ----------
    def refresh(self) -> None:
        error = self._period_error()
        if error is not None:
            # FR-06: запрос к БД не выполняется, пользователь видит сообщение
            self.date_error_label.setText(error)
            self.date_error_label.show()
            self.rows_cache = []
            self.total = 0
            self.table.setRowCount(0)
            self._update_summary(empty=True)
            self._update_row_buttons()
            return
        self.date_error_label.hide()

        f = self.current_filter()
        try:
            rows, total = self.service.list_transactions(
                f,
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
                format_amount_ru(tr.amount_minor),
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
        self._update_row_buttons()

    def _update_summary(self, *, empty: bool = False) -> None:
        """Сводка считается по всей отфильтрованной выборке, не по странице (FR-02/03)."""
        if empty:
            s = {"income_minor": 0, "expense_minor": 0, "balance_minor": 0, "count": 0}
        else:
            s = self.service.summary(self.current_filter())
        self.summary_label.setText(
            f"По текущей выборке: Доходы: {format_amount_ru(s['income_minor'])}   "
            f"Расходы: {format_amount_ru(s['expense_minor'])}   "
            f"Баланс: {format_amount_ru(s['balance_minor'])}   "
            f"Операций: {s['count']}"
        )

    # ---------- сортировка ----------
    def _on_header_sort(self, column: int) -> None:
        mapping = {
            0: ("date ASC, id ASC", "date DESC, id DESC"),
            3: ("amount_minor ASC, id ASC", "amount_minor DESC, id DESC"),
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

    def _update_row_buttons(self) -> None:
        """FR-15: destructive-кнопки disabled, пока объект явно не выбран."""
        has_sel = self.current_transaction() is not None
        self.btn_edit.setEnabled(has_sel)
        self.btn_delete.setEnabled(has_sel)

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
