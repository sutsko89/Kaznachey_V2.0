"""Вкладка «Дашборд»: три отдельных подвкладки со своими графиками.

Подвкладки:
- «Обзор» — доходы/расходы за период; для месячных периодов разбивка по дням
  с указанием сумм (график + таблица), для остальных — динамика по месяцам;
- «Расходы по категориям» — горизонтальный график, суммы и проценты;
- «Доходы по категориям» — горизонтальный график, суммы и проценты.

Выбор периода общий для всех подвкладок.
"""
from __future__ import annotations

import calendar
import datetime as dt

from PyQt6.QtCore import QDate, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.domain.service import TreasuryService, format_amount_ru
from app.errors import ValidationError
from app.ui.dateedit import EMPTY_MESSAGE, DateError, NullableDateEdit
from app.ui.theme import (ACCENT, BORDER, CARD_BG, EXPENSE_COLOR, INCOME_COLOR,
                          MUTED, TEXT)

MONTH_NAMES = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]

# светлая тема — единственная; палитра берётся из app.ui.theme
INCOME_COLOR_LIGHT = INCOME_COLOR
EXPENSE_COLOR_LIGHT = EXPENSE_COLOR
INCOME_COLOR_DARK = INCOME_COLOR
EXPENSE_COLOR_DARK = EXPENSE_COLOR


def _month_options() -> list[tuple[str, str]]:
    """Варианты месяцев: текущий + 24 предыдущих."""
    today = dt.date.today().replace(day=1)
    out: list[tuple[str, str]] = []
    y, m = today.year, today.month
    for _ in range(25):
        out.append((f"{MONTH_NAMES[m - 1]} {y}", f"{y:04d}-{m:02d}"))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


class SectionFrame(QFrame):
    """Рамка с жирным заголовком и внутренней вертикальной раскладкой."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"SectionFrame {{ background: {CARD_BG}; border: 1px solid {BORDER};"
            f" border-radius: 10px; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)
        lbl = QLabel(title)
        lbl.setStyleSheet(f"font-weight: 600; color: {TEXT}; font-size: 14px;"
                          f" background: transparent;")
        outer.addWidget(lbl)
        self.inner = QVBoxLayout()
        outer.addLayout(self.inner)


class PairedBarsWidget(QWidget):
    """Парные столбцы доход/расход (по месяцам или по дням)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(190)
        self._data: list[tuple[str, float, float]] = []
        self.income_color = INCOME_COLOR_LIGHT
        self.expense_color = EXPENSE_COLOR_LIGHT

    def set_data(self, data: list[tuple[str, float, float]]) -> None:
        self._data = data
        self.update()

    def set_theme_colors(self, dark: bool) -> None:
        self.income_color = INCOME_COLOR_DARK if dark else INCOME_COLOR_LIGHT
        self.expense_color = EXPENSE_COLOR_DARK if dark else EXPENSE_COLOR_LIGHT
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        n = len(self._data)
        if n == 0:
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Нет данных за выбранный период")
            return
        max_val = max([v for row in self._data for v in row[1:]] + [1.0])
        label_h = 30
        chart_h = max(h - label_h - 14, 10)
        group_w = w / n
        bar_w = max(3.0, min(24.0, group_w * 0.30))
        radius = min(4.0, bar_w / 2)
        painter.setPen(QColor(BORDER))
        painter.drawLine(0, int(8 + chart_h), w, int(8 + chart_h))
        f = painter.font()
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1))
        painter.setFont(f)
        dense = n > 12  # дни месяца: подписываем каждый второй день
        for i, (label, income, expense) in enumerate(self._data):
            cx = i * group_w + group_w / 2
            inc_h = chart_h * (income / max_val)
            exp_h = chart_h * (expense / max_val)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.income_color))
            painter.drawRoundedRect(
                QRectF(cx - bar_w - 2, 8 + (chart_h - inc_h), bar_w, inc_h),
                radius, radius)
            painter.setBrush(QColor(self.expense_color))
            painter.drawRoundedRect(
                QRectF(cx + 2, 8 + (chart_h - exp_h), bar_w, exp_h),
                radius, radius)
            painter.setPen(QColor(MUTED))
            show_label = (not dense) or (i % 2 == 0)
            if show_label:
                short = label.replace(" ", "\n", 1)
                painter.drawText(
                    QRectF(i * group_w, h - label_h, group_w, label_h),
                    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                    short)


class HBarChartWidget(QWidget):
    """Горизонтальный график распределения по категориям (сумма и %)."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.color = color
        self._rows: list[tuple[str, float, float]] = []  # name, total, share%

    def set_rows(self, rows: list[tuple[str, float, float]]) -> None:
        self._rows = rows[:12]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if not self._rows:
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Нет данных за выбранный период")
            return
        max_val = max(r[1] for r in self._rows) or 1.0
        row_h = 26
        left = 130   # ширина подписи категории
        right = 110  # ширина подписи суммы/процента
        bar_area = max(w - left - right, 20)
        radius = 4.0
        f = painter.font()
        f.setPointSizeF(max(8.5, f.pointSizeF() - 0.5))
        painter.setFont(f)
        for i, (name, total, share) in enumerate(self._rows):
            y = 8 + i * row_h
            if y + row_h > h:
                break
            cy = y + 4
            painter.setPen(QColor(TEXT))
            elided = QFontMetricsText(painter, name, left - 10)
            painter.drawText(QRectF(4, cy - 2, left - 10, 16),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             elided)
            bw = max(bar_area * (total / max_val), 2.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self.color))
            painter.drawRoundedRect(QRectF(left, cy, bw, 14), radius, radius)
            painter.setPen(QColor(MUTED))
            painter.drawText(QRectF(left + bar_area + 8, cy - 2, right - 10, 16),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             f"{format_amount_ru(total)} · {share:.1f}%")


def QFontMetricsText(painter: QPainter, text: str, width: float) -> str:
    """Обрезает длинное название категории многоточием."""
    fm = painter.fontMetrics()
    return fm.elidedText(text, Qt.TextElideMode.ElideRight, int(width))


def _make_card(title: str, color: str | None = None) -> tuple[QFrame, QLabel]:
    frame = QFrame()
    frame.setObjectName("summaryCard")
    frame.setStyleSheet(
        f"#summaryCard {{ background: {CARD_BG}; border: 1px solid {BORDER};"
        f" border-radius: 12px; }}"
        f"#summaryCard QLabel {{ background: transparent; border: none; }}")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 12, 16, 12)
    lay.setSpacing(4)
    lbl_title = QLabel(title)
    lbl_title.setStyleSheet(
        f"color: {MUTED}; font-size: 12px; font-weight: 600; letter-spacing: 1px;")
    value = QLabel("0,00")
    style = f"font-size: 22px; font-weight: 700; color: {TEXT};"
    if color:
        style = f"font-size: 22px; font-weight: 700; color: {color};"
    value.setStyleSheet(style)
    lay.addWidget(lbl_title)
    lay.addWidget(value)
    lay.addStretch(1)
    return frame, value


def _make_table(headers: list[str], stretch_col: int = 0) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    for col in range(len(headers)):
        mode = (QHeaderView.ResizeMode.Stretch if col == stretch_col
                else QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(col, mode)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setDefaultSectionSize(30)
    return t


class DashboardTab(QWidget):
    """Дашборд: подвкладки Обзор / Расходы по категориям / Доходы по категориям."""

    def __init__(self, service: TreasuryService, parent=None):
        super().__init__(parent)
        self.service = service
        self._ready = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # ---- выбор периода (общий для всех подвкладок) ----
        top = QHBoxLayout()
        top.setSpacing(8)
        lbl_period = QLabel("Период:")
        lbl_period.setStyleSheet(f"color: {MUTED}; font-weight: 600;")
        top.addWidget(lbl_period)
        self.period_combo = QComboBox()
        self.period_combo.setMinimumHeight(34)
        self.period_combo.setMinimumWidth(190)
        self.period_combo.addItem("Текущий месяц", "current_month")
        self.period_combo.addItem("Предыдущий месяц", "prev_month")
        self.period_combo.addItem("Текущий квартал", "quarter")
        self.period_combo.addItem("Текущий год", "year")
        self.period_combo.addItem("Год назад — сегодня", "last_12m")
        self.period_combo.addItem("Месяц…", "month")
        self.period_combo.addItem("Произвольный…", "custom")
        top.addWidget(self.period_combo)

        self.month_combo = QComboBox()
        self.month_combo.setMinimumHeight(34)
        for text, data in _month_options():
            self.month_combo.addItem(text, data)
        self.month_combo.setVisible(False)
        top.addWidget(self.month_combo)

        self.lbl_from = QLabel("с")
        # единый компонент даты (FR-05): DD.MM.YYYY + календарь; ошибочный
        # ввод не превращается молча в отсутствие фильтра (FR-06)
        self.start_edit = NullableDateEdit(label="", default_today=True)
        self.start_edit.date_edit.setDate(
            QDate(dt.date.today().year, dt.date.today().month, 1))
        self.start_edit._enabled_cb.hide()
        self.lbl_to = QLabel("по")
        self.end_edit = NullableDateEdit(label="", default_today=True)
        self.end_edit.date_edit.setDate(QDate.currentDate())
        self.end_edit._enabled_cb.hide()
        self.lbl_from.setVisible(False)
        self.start_edit.setVisible(False)
        self.lbl_to.setVisible(False)
        self.end_edit.setVisible(False)
        top.addWidget(self.lbl_from)
        top.addWidget(self.start_edit)
        top.addWidget(self.lbl_to)
        top.addWidget(self.end_edit)

        self.btn_apply = QPushButton("Применить")
        self.btn_apply.setObjectName("primaryButton")
        self.btn_apply.setMinimumHeight(34)
        self.btn_apply.clicked.connect(self.refresh)
        top.addWidget(self.btn_apply)
        top.addStretch(1)
        layout.addLayout(top)

        # ---- карточки итогов ----
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_income, self.lbl_income = _make_card("ДОХОДЫ", INCOME_COLOR_LIGHT)
        self.card_expense, self.lbl_expense = _make_card("РАСХОДЫ", EXPENSE_COLOR_LIGHT)
        self.card_balance, self.lbl_balance = _make_card("БАЛАНС")
        self._balance_base_style = f"font-size: 22px; font-weight: 700; color: {TEXT};"
        self._balance_pos_style = f"font-size: 22px; font-weight: 700; color: {INCOME_COLOR};"
        self._balance_neg_style = f"font-size: 22px; font-weight: 700; color: {EXPENSE_COLOR};"
        cards.addWidget(self.card_income)
        cards.addWidget(self.card_expense)
        cards.addWidget(self.card_balance)
        layout.addLayout(cards)

        # ---- подвкладки ----
        self.sub_tabs = QTabWidget()
        self.sub_tabs.addTab(self._build_overview(), "Обзор")
        self.sub_tabs.addTab(self._build_category_page("expense"),
                             "Расходы по категориям")
        self.sub_tabs.addTab(self._build_category_page("income"),
                             "Доходы по категориям")
        layout.addWidget(self.sub_tabs, stretch=1)

        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        self.month_combo.currentIndexChanged.connect(lambda *_: self.refresh())
        self._ready = True
        self.refresh()

    # ---------- построение подвкладок ----------
    def _build_overview(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        self.overview_title = QLabel("Динамика по месяцам")
        self.overview_title.setStyleSheet(
            f"font-weight: 600; color: {TEXT}; font-size: 14px;")
        lay.addWidget(self.overview_title)
        self.bars = PairedBarsWidget()
        lay.addWidget(self.bars, stretch=1)
        legend = QHBoxLayout()
        self.legend_income = QLabel("■ Доходы")
        self.legend_income.setStyleSheet(f"color: {INCOME_COLOR_LIGHT};")
        self.legend_expense = QLabel("■ Расходы")
        self.legend_expense.setStyleSheet(f"color: {EXPENSE_COLOR_LIGHT};")
        legend.addWidget(self.legend_income)
        legend.addWidget(self.legend_expense)
        legend.addStretch(1)
        lay.addLayout(legend)
        box_tbl = SectionFrame("Разбивка по дням")
        self.table_daily = _make_table(["Дата", "Доходы", "Расходы"], stretch_col=0)
        box_tbl.inner.addWidget(self.table_daily)
        box_tbl.setMaximumHeight(260)
        lay.addWidget(box_tbl)
        return page

    def _build_category_page(self, type_: str) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        is_expense = type_ == "expense"
        color = EXPENSE_COLOR_LIGHT if is_expense else INCOME_COLOR_LIGHT
        title = ("Расходы по категориям" if is_expense else "Доходы по категориям")
        lbl = QLabel(title)
        lbl.setStyleSheet(f"font-weight: 600; color: {TEXT}; font-size: 14px;")
        lay.addWidget(lbl)
        chart = HBarChartWidget(color)
        lay.addWidget(chart, stretch=1)
        table = _make_table(["Категория", "Сумма", "Доля"], stretch_col=0)
        box_tbl = SectionFrame("Подробно")
        box_tbl.inner.addWidget(table)
        lay.addWidget(box_tbl, stretch=1)
        if is_expense:
            self.chart_expenses = chart
            self.table_expenses = table
        else:
            self.chart_incomes = chart
            self.table_incomes = table
        return page

    # ---------- выбор периода ----------
    def _on_period_changed(self, *_args) -> None:
        if not self._ready:
            return
        mode = self.period_combo.currentData()
        self.month_combo.setVisible(mode == "month")
        custom = mode == "custom"
        for wdg in (self.lbl_from, self.start_edit, self.lbl_to, self.end_edit):
            wdg.setVisible(custom)
        self.refresh()

    def current_range(self) -> tuple[str, str]:
        """(date_from, date_to) в формате YYYY-MM-DD для выбранного периода."""
        mode = self.period_combo.currentData()
        today = dt.date.today()
        if mode == "current_month":
            first = today.replace(day=1)
            last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
            return first.isoformat(), last.isoformat()
        if mode == "prev_month":
            prev_last = today.replace(day=1) - dt.timedelta(days=1)
            prev_first = prev_last.replace(day=1)
            return prev_first.isoformat(), prev_last.isoformat()
        if mode == "quarter":
            q_start = (today.month - 1) // 3 * 3 + 1
            first = today.replace(month=q_start, day=1)
            last_month = q_start + 2
            last = first.replace(month=last_month,
                                 day=calendar.monthrange(first.year, last_month)[1])
            return first.isoformat(), last.isoformat()
        if mode == "year":
            return f"{today.year}-01-01", f"{today.year}-12-31"
        if mode == "last_12m":
            start = today.replace(year=today.year - 1) + dt.timedelta(days=1)
            return start.isoformat(), today.isoformat()
        if mode == "month":
            ym = self.month_combo.currentData()
            y, m = int(ym[:4]), int(ym[5:7])
            return f"{ym}-01", f"{ym}-{calendar.monthrange(y, m)[1]:02d}"
        # custom — единый модуль дат (FR-04/05/06): некорректная дата даёт
        # явную ошибку и подсветку поля, а не молчаливое расширение выборки
        errors = {}
        self.start_edit.set_error(False)
        self.end_edit.set_error(False)
        try:
            d1 = self.start_edit.value()
        except DateError as exc:
            d1 = None
            errors["date_from"] = str(exc)
            self.start_edit.set_error(True)
        try:
            d2 = self.end_edit.value()
        except DateError as exc:
            d2 = None
            errors["date_to"] = str(exc)
            self.end_edit.set_error(True)
        if not errors and d1 > d2:
            errors["period"] = EMPTY_MESSAGE
        if errors:
            raise ValidationError(errors)
        return d1, d2

    @staticmethod
    def _is_month_period(date_from: str, date_to: str) -> bool:
        return date_from[:7] == date_to[:7]

    # ---------- обновление ----------
    def apply_theme(self, dark: bool) -> None:
        inc = INCOME_COLOR_DARK if dark else INCOME_COLOR_LIGHT
        exp = EXPENSE_COLOR_DARK if dark else EXPENSE_COLOR_LIGHT
        self.lbl_income.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {inc};")
        self.lbl_expense.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {exp};")
        self.bars.set_theme_colors(dark)
        self.legend_income.setStyleSheet(f"color: {inc};")
        self.legend_expense.setStyleSheet(f"color: {exp};")

    def refresh(self) -> None:
        if not self._ready:
            return
        try:
            date_from, date_to = self.current_range()
        except ValidationError as exc:
            QMessageBox.warning(self, "Казначей",
                                "Некорректный период: " + "; ".join(exc.errors.values()))
            return
        try:
            summary = self.service.period_summary(date_from, date_to)
            report = self.service.category_report(date_from, date_to)
            daily = self.service.daily_breakdown(date_from, date_to)
        except Exception as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return

        income = int(summary["income_minor"])
        expense = int(summary["expense_minor"])
        balance = income - expense   # целые minor, без float
        self.lbl_income.setText(format_amount_ru(income))
        self.lbl_expense.setText(format_amount_ru(expense))
        self.lbl_balance.setText(format_amount_ru(balance))
        if balance < 0:
            self.lbl_balance.setStyleSheet(self._balance_neg_style)
        elif balance > 0:
            self.lbl_balance.setStyleSheet(self._balance_pos_style)
        else:
            self.lbl_balance.setStyleSheet(self._balance_base_style)

        self._fill_breakdown(self.table_expenses, self.chart_expenses,
                             report, "expense")
        self._fill_breakdown(self.table_incomes, self.chart_incomes,
                             report, "income")
        self._fill_overview(date_from, date_to, daily)

    def _fill_overview(self, date_from: str, date_to: str,
                       daily: list[dict]) -> None:
        """Для месячного периода — разбивка по дням, иначе — по месяцам."""
        if self._is_month_period(date_from, date_to):
            self.overview_title.setText("Динамика по дням (месяц)")
            year, month = int(date_from[:4]), int(date_from[5:7])
            days_in_month = calendar.monthrange(year, month)[1]
            by_day = {int(r["date"][8:10]): r for r in daily}
            data: list[tuple[str, float, float]] = []
            for d in range(1, days_in_month + 1):
                rec = by_day.get(d)
                data.append((str(d),
                             rec["income_minor"] if rec else 0,
                             rec["expense_minor"] if rec else 0))
            self.bars.set_data(data)
            rows = daily
        else:
            self.overview_title.setText("Динамика по месяцам")
            self._fill_bars(date_from, date_to)
            rows = daily
        # таблица разбивки: по дням — для месяца; для длинных периодов — тоже по дням,
        # но только дни с операциями
        self.table_daily.setRowCount(len(rows))
        for i, r in enumerate(rows):
            it_date = QTableWidgetItem(_fmt_day(r["date"]))
            it_inc = QTableWidgetItem(format_amount_ru(r["income_minor"]))
            it_inc.setForeground(QColor(INCOME_COLOR))
            it_exp = QTableWidgetItem(format_amount_ru(r["expense_minor"]))
            it_exp.setForeground(QColor(EXPENSE_COLOR))
            for item in (it_inc, it_exp):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            self.table_daily.setItem(i, 0, it_date)
            self.table_daily.setItem(i, 1, it_inc)
            self.table_daily.setItem(i, 2, it_exp)

    def _fill_breakdown(self, table: QTableWidget, chart: HBarChartWidget,
                        report: list[dict], type_: str) -> None:
        rows = sorted((r for r in report if r["type"] == type_),
                      key=lambda r: r["total"], reverse=True)
        subtotal = sum(r["total"] for r in rows)
        shares = [(r["name"], r["total"],
                   (r["total"] / subtotal * 100.0) if subtotal else 0.0)
                  for r in rows]
        chart.set_rows(shares)
        table.setRowCount(len(rows))
        for i, (name, total, share) in enumerate(shares):
            it_name = QTableWidgetItem(name)
            it_sum = QTableWidgetItem(format_amount_ru(total))
            it_sum.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
            it_share = QTableWidgetItem(f"{share:.1f}%")
            it_share.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(i, 0, it_name)
            table.setItem(i, 1, it_sum)
            table.setItem(i, 2, it_share)

    def _fill_bars(self, date_from: str, date_to: str) -> None:
        """Помесячная динамика: не более 12 последних месяцев периода."""
        months = self._months_between(date_from, date_to)[-12:]
        data: list[tuple[str, float, float]] = []
        for ym in months:
            d1, d2 = self.service.month_range(ym)
            from app.dal.database import TransactionFilter
            totals = self.service.db.summary(
                TransactionFilter(date_from=d1, date_to=d2))
            y, m = int(ym[:4]), int(ym[5:7])
            data.append((f"{MONTH_NAMES[m - 1][:3]} {y}",
                         int(totals["income_minor"]),
                         int(totals["expense_minor"])))
        self.bars.set_data(data)

    @staticmethod
    def _months_between(date_from: str, date_to: str) -> list[str]:
        y, m = int(date_from[:4]), int(date_from[5:7])
        ye, me = int(date_to[:4]), int(date_to[5:7])
        out: list[str] = []
        while (y, m) <= (ye, me):
            out.append(f"{y:04d}-{m:02d}")
            m += 1
            if m == 13:
                m, y = 1, y + 1
        return out


def _fmt_day(iso_date: str) -> str:
    """YYYY-MM-DD -> DD.MM.YYYY."""
    return f"{iso_date[8:10]}.{iso_date[5:7]}.{iso_date[:4]}"
