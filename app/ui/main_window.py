"""Главное окно «Казначей»: вкладки, горячие клавиши, связывание диалогов.

Экрана входа нет: приложение сразу открывается в главном окне.
Модуль безопасности отсутствует (однопользовательская редакция).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QTabWidget, QVBoxLayout, QWidget,
)

from app.dal.database import Database, Transaction
from app.domain.service import TreasuryService
from app.errors import DatabaseError, ValidationError
from app.services.export import export_transactions_csv
from app.ui.dialogs_misc import CategoryDialog, TemplatePickerDialog
from app.ui.dialogs_transaction import TransactionDialog
from app.ui.tabs.budgets_tab import BudgetsTab
from app.ui.tabs.dashboard_tab import DashboardTab
from app.ui.tabs.settings_tab import SettingsTab
from app.ui.tabs.templates_tab import TemplatesTab
from app.ui.tabs.transactions_tab import TransactionsTab

class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self.service = TreasuryService(db, Path(__file__).resolve().parent.parent.parent / "migrations")
        self.setWindowTitle("Казначей")
        self.resize(1000, 700)
        self.setMinimumSize(1000, 700)

        # светлая тема применяется к приложению целиком (если QApplication уже создан)
        qapp = QApplication.instance()
        if qapp is not None:
            from app.ui.theme import apply_light_theme
            apply_light_theme(qapp)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)

        # ---- верхняя панель ----
        top = QHBoxLayout()
        top.setSpacing(10)
        title = QLabel("Казначей")
        title.setObjectName("appTitle")
        top.addWidget(title)
        subtitle = QLabel("личные финансы офлайн")
        subtitle.setObjectName("appSubtitle")
        top.addWidget(subtitle)
        top.addStretch(1)
        self.btn_exit = QPushButton("Выход")
        self.btn_exit.setObjectName("exitButton")
        self.btn_exit.clicked.connect(self.close)
        top.addWidget(self.btn_exit)
        layout.addLayout(top)

        # ---- вкладки ----
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.transactions_tab = TransactionsTab(self.service)
        self.budgets_tab = BudgetsTab(self.service)
        self.dashboard_tab = DashboardTab(self.service)
        self.templates_tab = TemplatesTab(self.service)
        self.settings_tab = SettingsTab(self.service)
        self.tabs.addTab(self.transactions_tab, "Операции")
        self.tabs.addTab(self.budgets_tab, "Бюджеты и категории")
        self.tabs.addTab(self.dashboard_tab, "Дашборд")
        self.tabs.addTab(self.templates_tab, "Шаблоны")
        self.tabs.addTab(self.settings_tab, "Настройки")
        # масштаб интерфейса (светлая тема — единственная, применяется на старте)
        self._apply_scale()

        # ---- колбэки вкладок ----
        self.transactions_tab.on_add = self.add_transaction
        self.transactions_tab.on_edit = self.edit_transaction
        self.transactions_tab.on_use_template = self.add_by_template
        self.transactions_tab.on_export = self.export_csv
        self.transactions_tab.btn_template.clicked.connect(self.add_by_template)
        self.transactions_tab.btn_add.clicked.connect(self.add_transaction)
        self.transactions_tab.btn_export.clicked.connect(self.export_csv)
        self.budgets_tab.on_add_category = self.add_category
        self.budgets_tab.on_edit_category = self.edit_category
        self.templates_tab.on_add_template = self.add_template
        self.templates_tab.on_edit_template = self.edit_template
        self.settings_tab.on_saved = self.on_settings_saved

        # ---- горячие клавиши ----
        self._setup_shortcuts()

    # ---------- масштаб ----------
    def _apply_scale(self) -> None:
        try:
            scale = int(self.service.get_setting("ui_scale", "100"))
        except ValueError:
            scale = 100
        qapp = QApplication.instance()
        base = getattr(self, "_base_font_px", None)
        if base is None:
            base = 14.0
            self._base_font_px = base
        px = max(6.0, base * scale / 100.0)
        if qapp is not None:
            f = qapp.font()
            f.setPixelSize(int(round(px)))
            qapp.setFont(f)
        f2 = self.font()
        f2.setPixelSize(int(round(px)))
        self.setFont(f2)

    def on_settings_saved(self) -> None:
        """Мгновенное применение сохранённого масштаба без перезапуска."""
        self._apply_scale()
        self.transactions_tab.refresh()
        self.budgets_tab.refresh()

    def _setup_shortcuts(self) -> None:
        def sc(seq: str, slot) -> None:
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(slot)

        sc("Ctrl+N", self.add_transaction)
        sc("Ctrl+F", lambda: (self.tabs.setCurrentWidget(self.transactions_tab),
                              self.transactions_tab.search_edit.setFocus(),
                              self.transactions_tab.search_edit.selectAll()))
        sc("Ctrl+E", self.export_csv)
        sc("Ctrl+B", self.settings_tab.create_backup)
        sc("Ctrl+1", lambda: self.tabs.setCurrentIndex(0))
        sc("Ctrl+2", lambda: self.tabs.setCurrentIndex(1))
        sc("Ctrl+3", lambda: self.tabs.setCurrentIndex(2))
        sc("Ctrl+4", lambda: self.tabs.setCurrentIndex(4))
        sc("Ctrl+5", lambda: self.tabs.setCurrentIndex(3))

    # ---------- операции ----------
    def _refresh_all(self) -> None:
        self.transactions_tab.reload_categories()
        self.transactions_tab.refresh()
        self.budgets_tab.refresh()
        self.templates_tab.refresh()
        self.dashboard_tab.refresh()

    def add_transaction(self) -> None:
        dlg = TransactionDialog(self.service, self)
        if dlg.exec():
            self._refresh_all()

    def edit_transaction(self, tr: Transaction) -> None:
        dlg = TransactionDialog(self.service, self, transaction=tr)
        if dlg.exec():
            self._refresh_all()

    # ---------- шаблоны ----------
    def add_by_template(self) -> None:
        picker = TemplatePickerDialog(self.service, self)
        if not picker.exec():
            return
        if picker.selected is None:
            QMessageBox.information(picker, "Казначей", getattr(picker, "error_hint",
                                                                "Шаблон не выбран."))
        if picker.selected is None:
            return
        dlg = TransactionDialog(self.service, self, template_prefill=picker.selected)
        if dlg.exec():
            self._refresh_all()

    def add_template(self) -> None:
        dlg = TransactionDialog(self.service, self, as_template=True)
        if dlg.exec():
            self._refresh_all()

    def edit_template(self, tpl: Transaction) -> None:
        dlg = TemplateEditDialog(self.service, self, template=tpl)
        if dlg.exec():
            self._refresh_all()

    # ---------- категории ----------
    def add_category(self) -> None:
        dlg = CategoryDialog(self.service, self)
        if dlg.exec():
            self._refresh_all()

    def edit_category(self, cat) -> None:
        dlg = CategoryDialog(self.service, self, category=cat)
        if dlg.exec():
            self._refresh_all()

    # ---------- экспорт ----------
    def export_csv(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        suggested = str(Path.home() / "Kaznachei" / "export.csv")
        dest, _ = QFileDialog.getSaveFileName(self, "Экспорт в CSV", suggested,
                                              "CSV файлы (*.csv)")
        if not dest:
            return
        filters = self.transactions_tab.current_filters()
        try:
            count = export_transactions_csv(self.db, dest, **filters)
        except (DatabaseError, OSError) as exc:
            QMessageBox.critical(self, "Казначей", f"Не удалось выполнить экспорт: {exc}")
            return
        QMessageBox.information(
            self, "Казначей",
            f"Экспорт завершён успешно: {count} операций сохранено в {dest}",
        )

    # ---------- откат ----------
    def quit_after_restore(self) -> None:
        self.db.close()
        QApplication.quit()


# отдельный импорт после определения MainWindow — чтобы избежать циклического импорта
class TemplateEditDialog(TransactionDialog):
    """Редактирование шаблона: те же правила валидации суммы и категории."""

    def __init__(self, service: TreasuryService, parent=None, template: Transaction | None = None):
        super().__init__(service, parent, template_prefill=template, as_template=True)
        self.template = template
        self.transaction = None

    def _on_accept(self) -> None:
        self._clear_errors()
        category_id = self.category_combo.currentData()
        fields = dict(
            type_=self.type_combo.currentData(),
            amount=self.amount_spin.value(),
            category_id=category_id if category_id is not None else 0,
            description=self.description_edit.text(),
            currency=self.currency_combo.currentText(),
        )
        try:
            if category_id is None:
                raise ValidationError({"category_id": "Категория обязательна"})
            tr = self.service.edit_template(self.template.id, **fields)
            self.saved = tr
            self.accept()
        except ValidationError as exc:
            for key, msg in exc.errors.items():
                self._show_error(key, msg)
        except DatabaseError as exc:
            QMessageBox.critical(self, "Казначей", str(exc))


if __name__ == "__main__":
    from app.main import main

    raise SystemExit(main())
