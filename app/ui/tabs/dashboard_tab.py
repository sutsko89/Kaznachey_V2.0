"""Вкладка «Дашборд»: сводка доходов/расходов за выбранный период.

Возможности:
- выбор периода (текущий/предыдущий месяц, квартал, год, 12 месяцев,
  конкретный месяц, произвольный диапазон);
- карточки: доходы, расходы, баланс;
- динамика по месяцам (парные столбцы доход/расход, собственная отрисовка);
- распределение расходов и доходов по категориям с долями.
"""
from __future__ import annotations

import calendar
import datetime as dt

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.domain.service import TreasuryService, format_amount_ru
from app.errors import ValidationError
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


class MonthlyBarsWidget(QWidget):
    """Лёгкая офлайн-диаграмма: парные столбцы доход/расход по месяцам."""

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
        bar_w = max(6.0, min(24.0, group_w * 0.30))
        radius = min(4.0, bar_w / 2)
        # базовая линия
        painter.setPen(QColor(BORDER))
        painter.drawLine(0, int(8 + chart_h), w, int(8 + chart_h))
        f = painter.font()
        f.setPointSizeF(max(8.0, f.pointSizeF() - 1))
        painter.setFont(f)
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
            short = label.replace(" ", "\n", 1)
            painter.drawText(
                QRectF(i * group_w, h - label_h, group_w, label_h),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, short)


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


class DashboardTab(QWidget):
    """Дашборд по доходам/расходам с выбором периода."""

    def __init__(self, service: TreasuryService, parent=None):
        super().__init__(parent)
        self.service = service
        self._ready = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # ---- выбор периода ----
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
        self.start_edit = QLineEdit(dt.date.today().replace(day=1).isoformat())
        self.start_edit.setPlaceholderText("ДД.ММ.ГГГГ или YYYY-MM-DD")
        self.start_edit.setMinimumHeight(34)
        self.start_edit.setFixedWidth(130)
        self.lbl_to = QLabel("по")
        self.end_edit = QLineEdit(dt.date.today().isoformat())
        self.end_edit.setPlaceholderText("ДД.ММ.ГГГГ или YYYY-MM-DD")
        self.end_edit.setMinimumHeight(34)
        self.end_edit.setFixedWidth(130)
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

        # ---- динамика по месяцам ----
        box_bars = SectionFrame("Динамика по месяцам")
        self.bars = MonthlyBarsWidget()
        box_bars.inner.addWidget(self.bars)
        legend = QHBoxLayout()
        self.legend_income = QLabel("■ Доходы")
        self.legend_income.setStyleSheet(f"color: {INCOME_COLOR_LIGHT};")
        self.legend_expense = QLabel("■ Расходы")
        self.legend_expense.setStyleSheet(f"color: {EXPENSE_COLOR_LIGHT};")
        legend.addWidget(self.legend_income)
        legend.addWidget(self.legend_expense)
        legend.addStretch(1)
        box_bars.inner.addLayout(legend)
        layout.addWidget(box_bars)

        # ---- распределение по категориям ----
        split = QHBoxLayout()
        box_cat = SectionFrame("Расходы по категориям")
        self.table_expenses = self._make_table()
        box_cat.inner.addWidget(self.table_expenses)
        box_inc = SectionFrame("Доходы по категориям")
        self.table_incomes = self._make_table()
        box_inc.inner.addWidget(self.table_incomes)
        split.addWidget(box_cat)
        split.addWidget(box_inc)
        layout.addLayout(split, stretch=1)

        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        self.month_combo.currentIndexChanged.connect(lambda *_: self.refresh())
        self._ready = True
        self.refresh()

    @staticmethod
    def _make_table() -> QTableWidget:
        t = QTableWidget(0, 3)
        t.setHorizontalHeaderLabels(["Категория", "Сумма", "Доля"])
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setDefaultSectionSize(30)
        return t

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
        # custom
        from app.domain.validators import validate_date
        errors = {}
        d1 = self.start_edit.text().strip()
        d2 = self.end_edit.text().strip()
        try:
            validate_date(d1)
        except ValidationError as exc:
            errors.update(exc.errors)
        try:
            validate_date(d2)
        except ValidationError as exc:
            errors.update(exc.errors)
        if not errors and d1 > d2:
            errors["period"] = "Начало периода позже его конца"
        if errors:
            raise ValidationError(errors)
        return d1, d2

    # ---------- обновление ----------
    def apply_theme(self, dark: bool) -> None:
        inc = INCOME_COLOR_DARK if dark else INCOME_COLOR_LIGHT
        exp = EXPENSE_COLOR_DARK if dark else EXPENSE_COLOR_LIGHT
        self.lbl_income.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {inc};")
        self.lbl_expense.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {exp};")
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
        except Exception as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return

        income = float(summary.get("income", 0.0))
        expense = float(summary.get("expense", 0.0))
        balance = round(income - expense, 2)
        self.lbl_income.setText(format_amount_ru(income))
        self.lbl_expense.setText(format_amount_ru(expense))
        self.lbl_balance.setText(format_amount_ru(balance))
        if balance < 0:
            self.lbl_balance.setStyleSheet(self._balance_neg_style)
        elif balance > 0:
            self.lbl_balance.setStyleSheet(self._balance_pos_style)
        else:
            self.lbl_balance.setStyleSheet(self._balance_base_style)

        self._fill_breakdown(self.table_expenses, report, "expense")
        self._fill_breakdown(self.table_incomes, report, "income")
        self._fill_bars(date_from, date_to)

    def _fill_breakdown(self, table: QTableWidget, report: list[dict], type_: str) -> None:
        rows = sorted((r for r in report if r["type"] == type_),
                      key=lambda r: r["total"], reverse=True)
        subtotal = sum(r["total"] for r in rows)
        table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            share = (r["total"] / subtotal * 100.0) if subtotal else 0.0
            it_name = QTableWidgetItem(r["name"])
            it_sum = QTableWidgetItem(format_amount_ru(r["total"]))
            it_sum.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it_share = QTableWidgetItem(f"{share:.1f}%")
            it_share.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(i, 0, it_name)
            table.setItem(i, 1, it_sum)
            table.setItem(i, 2, it_share)

    def _fill_bars(self, date_from: str, date_to: str) -> None:
        """Помесячная динамика: не более 12 последних месяцев периода."""
        months = self._months_between(date_from, date_to)[-12:]
        data: list[tuple[str, float, float]] = []
        for ym in months:
            d1, d2 = self.service.month_range(ym)
            totals = self.service.db.period_totals(date_from=d1, date_to=d2)
            y, m = int(ym[:4]), int(ym[5:7])
            data.append((f"{MONTH_NAMES[m - 1][:3]} {y}",
                         float(totals.get("income", 0.0)),
                         float(totals.get("expense", 0.0))))
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
