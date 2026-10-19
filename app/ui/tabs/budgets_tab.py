"""Вкладка «Бюджеты и категории»: прогресс-бары лимитов, превышения красным сверху."""
from __future__ import annotations

import datetime as dt

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from PyQt6.QtGui import QColor

from app.dal.database import Category
from app.domain.service import TreasuryService, format_amount_ru
from app.errors import CategoryInUseError, DatabaseError, ValidationError
from app.ui.theme import ACCENT, DANGER, EXPENSE_COLOR, INCOME_COLOR

MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


class BudgetsTab(QWidget):
    def __init__(self, service: TreasuryService, parent=None):
        super().__init__(parent)
        self.service = service
        self.on_add_category = None
        self.on_edit_category = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # ---- выбор месяца + блок превышений ----
        top = QHBoxLayout()
        top.setSpacing(8)
        lbl_month = QLabel("Месяц:")
        lbl_month.setStyleSheet("color: #6b7280; font-weight: 600;")
        top.addWidget(lbl_month)
        self.month_combo = QComboBox()
        self._ready = False  # блокирует refresh() до окончания инициализации вкладки
        today = dt.date.today().replace(day=1)
        for year in range(today.year - 1, today.year + 1):
            for m in range(1, 13):
                self.month_combo.addItem(f"{MONTH_NAMES[m - 1]} {year}", f"{year:04d}-{m:02d}")
        idx = self.month_combo.findData(today.strftime("%Y-%m"))
        if idx >= 0:
            self.month_combo.setCurrentIndex(idx)
        self.month_combo.setMinimumHeight(34)
        self.month_combo.setMinimumWidth(190)
        self.month_combo.currentIndexChanged.connect(self._on_month_changed)
        top.addWidget(self.month_combo)
        top.addStretch(1)
        layout.addLayout(top)

        self.over_frame = QFrame()
        self.over_frame.setStyleSheet(
            f"QFrame {{ background: #fdecea; border: 1px solid #f3b7ae;"
            f" border-radius: 10px; }}"
            f" QLabel {{ color: {DANGER}; font-weight: 600; background: transparent; }}"
        )
        self.over_label = QLabel("Превышение лимитов: нет")
        over_layout = QVBoxLayout(self.over_frame)
        over_layout.setContentsMargins(8, 4, 8, 4)
        over_layout.addWidget(self.over_label)
        # скрытие произойдёт в refresh(); hide() здесь нельзя — при инициализации
        # виджета isVisible() возвращает True независимо от вызова hide()
        layout.addWidget(self.over_frame)

        # ---- кнопки управления категориями ----
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_add = QPushButton("+ Добавить")
        self.btn_add.setObjectName("primaryButton")
        self.btn_edit = QPushButton("Редактировать")
        self.btn_delete = QPushButton("Удалить")
        self.btn_delete.setObjectName("dangerButton")
        self.btn_toggle = QPushButton("Деактивировать")
        for b in (self.btn_add, self.btn_edit, self.btn_delete, self.btn_toggle):
            b.setMinimumHeight(34)
            buttons.addWidget(b)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.btn_add.clicked.connect(lambda: self.on_add_category and self.on_add_category())
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_toggle.clicked.connect(self._toggle_selected)

        # ---- список категорий с прогресс-барами ----
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list_container, stretch=1)

        # ---- отчёт по категориям за выбранный месяц ----
        lbl_report = QLabel("Отчёт по категориям за выбранный месяц:")
        lbl_report.setProperty("secondary", True)
        layout.addWidget(lbl_report)
        self.report_table = QTableWidget(0, 4)
        self.report_table.setAlternatingRowColors(True)
        self.report_table.verticalHeader().setDefaultSectionSize(32)
        self.report_table.setHorizontalHeaderLabels(
            ["Категория", "Сумма", "Доля, %", "Остаток лимита"])
        self.report_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.report_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.report_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.report_table.verticalHeader().setVisible(False)
        layout.addWidget(self.report_table)

        self.selected_category_id: int | None = None
        self._buttons_row = buttons
        self._ready = True
        self.refresh()

    # ---------- выбор месяца ----------
    def current_month(self) -> str:
        return self.month_combo.currentData()

    def _on_month_changed(self, *_args) -> None:
        if getattr(self, "_ready", False):
            self.refresh()

    # ---------- обновление ----------
    def refresh(self) -> None:
        month = self.current_month()
        try:
            statuses = self.service.budget_status(month)
        except DatabaseError as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return

        # блок превышений — красным, сверху
        over_rows = [s for s in statuses if s["over"]]
        if over_rows:
            lines = ["Превышение лимитов:"]
            for s in over_rows:
                lines.append(
                    f"⚠ {s['category'].name}: потрачено {format_amount_ru(s['spent'])}"
                    f" / {format_amount_ru(s['limit'])} руб."
                    f" ({int(s['percent'])}%)"
                )
            self.over_label.setText("\n".join(lines))
            self.over_frame.show()
        else:
            self.over_frame.hide()

        # перерисовка списка категорий
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for s in statuses:
            cat = s["category"]
            row_w = QWidget()
            row_w.setCursor(Qt.CursorShape.PointingHandCursor)
            row_w.setStyleSheet(
                "background: #ffffff; border-bottom: 1px solid #eef0f3;")
            h = QHBoxLayout(row_w)
            h.setContentsMargins(10, 6, 10, 6)
            h.setSpacing(10)
            name = cat.name + ("" if cat.is_active else "  (неактивна)")
            lbl = QLabel(name)
            lbl.setMinimumWidth(220)
            bar = QProgressBar()
            bar.setRange(0, 100)
            if s["limit"] > 0:
                pct = min(int(s["percent"] or 0), 100)
                bar.setValue(pct)
                text = (f"Потрачено {format_amount_ru(s['spent'])} / "
                        f"{format_amount_ru(s['limit'])} руб. ({int(s['percent'])}%)")
            else:
                bar.setRange(0, 1)
                bar.setValue(0)
                bar.setTextVisible(True)
                bar.setFormat("без лимита")
                text = f"Потрачено {format_amount_ru(s['spent'])} руб."
            status_lbl = QLabel(text)
            if s["over"]:
                for wdg in (lbl, status_lbl):
                    # "red" оставлен как имя цвета: читается тем же тоном, что #d93025,
                    # и используется тестами перерасхода
                    wdg.setStyleSheet(f"color: red; font-weight: 600;")
                bar.setStyleSheet(
                    f"QProgressBar::chunk {{ background-color: {DANGER}; }}")
            elif s["limit"] > 0:
                bar.setStyleSheet(
                    f"QProgressBar::chunk {{ background-color: {ACCENT}; }}")
            h.addWidget(lbl)
            h.addWidget(bar, stretch=1)
            h.addWidget(status_lbl)
            row_w.mousePressEvent = (
                lambda _ev, cid=cat.id, wid=row_w: self._select(cid, wid)
            )
            row_w.setProperty("category_id", cat.id)
            self.list_layout.addWidget(row_w)
        self.list_layout.addStretch(1)

        # отчёт по категориям
        date_from, date_to = self.service.month_range(month)
        report = self.service.category_report(date_from, date_to)
        self.report_table.setRowCount(len(report))
        for i, r in enumerate(report):
            remaining = ""
            if r["remaining_limit"] is not None:
                remaining = format_amount_ru(r["remaining_limit_minor"])
                if r["remaining_limit_minor"] < 0:
                    remaining = "-" + remaining[1:] if remaining.startswith("-") else remaining
            cells = [
                r["name"] + (" (доход)" if r["type"] == "income" else ""),
                format_amount_ru(r["total"]),
                f"{r['share_percent']:.1f}".replace(".", ","),
                remaining,
            ]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if r["type"] == "expense" and r["monthly_limit"] > 0 \
                        and r["remaining_limit_minor"] is not None \
                        and r["remaining_limit_minor"] < 0:
                    item.setForeground(QColor(DANGER))
                self.report_table.setItem(i, j, item)
        self._update_buttons()

    # ---------- выделение и действия ----------
    def _select(self, category_id: int, widget: QWidget) -> None:
        self.selected_category_id = category_id
        self._update_buttons()
        for i in range(self.list_layout.count()):
            w = self.list_layout.itemAt(i).widget()
            if w is not None and w.property("category_id") is not None:
                if w is widget:
                    w.setStyleSheet("background: #dbe7ff; border-radius: 8px;")
                else:
                    w.setStyleSheet("background: #ffffff; border-bottom: 1px solid #eef0f3;")

    def _selected_category(self) -> Category | None:
        """FR-14: действие применяется ТОЛЬКО к явно выбранной пользователем
        категории. Автовыбор первой запрещён; без выбора возвращаем None."""
        if self.selected_category_id is None:
            return None
        cat = self.service.db.get_category(self.selected_category_id)
        if cat is None:  # категория могла быть удалена — сбрасываем выбор
            self.selected_category_id = None
            self._update_buttons()
        return cat

    def _update_buttons(self) -> None:
        """FR-14/15: destructive-кнопки active только при явном выборе;
        подпись переключателя зависит от состояния выбранной категории."""
        has_sel = self.selected_category_id is not None
        self.btn_edit.setEnabled(has_sel)
        self.btn_delete.setEnabled(has_sel)
        self.btn_toggle.setEnabled(has_sel)
        if has_sel:
            cat = self.service.db.get_category(self.selected_category_id)
            self.btn_toggle.setText("Активировать" if (cat is not None and not cat.is_active)
                                    else "Деактивировать")
        else:
            self.btn_toggle.setText("Деактивировать")

    def _edit_selected(self) -> None:
        cat = self._selected_category()
        if cat is not None and self.on_edit_category:
            self.on_edit_category(cat)

    def _delete_selected(self) -> None:
        cat = self._selected_category()
        if cat is None:
            return
        answer = QMessageBox.question(
            self, "Подтверждение удаления",
            f"Удалить категорию «{cat.name}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_category(cat.id)
        except CategoryInUseError as exc:
            QMessageBox.warning(self, "Удаление невозможно", str(exc))
            return
        except (DatabaseError, ValidationError) as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return
        self.selected_category_id = None
        self.refresh()
        self._update_buttons()

    def _toggle_selected(self) -> None:
        cat = self._selected_category()
        if cat is None:
            return
        try:
            self.service.set_category_active(cat.id, not cat.is_active)
        except (DatabaseError, ValidationError) as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return
        self.refresh()
