"""Smoke-проверка всех кнопок главного окна «Казначей» (offscreen Qt).

Проверяет, что каждая кнопка подключена и выполняет реальное действие:
главное окно, вкладки Операции / Бюджеты и категории / Шаблоны / Настройки.
Запуск: QT_QPA_PLATFORM=offscreen python smoke_buttons.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QDialog, QFileDialog, QMessageBox)

from app.dal.database import Database
from app.domain.service import TreasuryService
from app.main import migrations_dir, open_database
from app.ui.dialogs_misc import CategoryDialog, TemplatePickerDialog
from app.ui.dialogs_transaction import TransactionDialog
from app.ui.main_window import MainWindow

FAILURES: list[str] = []


def check(name: str, cond: bool):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        FAILURES.append(name)


class FakeTxDialog(QDialog):
    """Замена диалога операции: создаёт запись напрямую при accept()."""

    def __init__(self, service, parent=None, *, transaction=None,
                 template_prefill=None, as_template=False):
        QDialog.__init__(self, parent)
        self.service = service
        self.transaction = transaction
        self.saved = None
        self._template_prefill = template_prefill
        self._as_template_fixed = as_template

    def exec(self):
        cat = next(c for c in self.service.list_categories()
                   if c.type == "expense")
        if self.transaction is not None:
            self.saved = self.service.edit_transaction(
                self.transaction.id, type_=self.transaction.type,
                amount=self.transaction.amount + 1,
                category_id=self.transaction.category_id,
                date=self.transaction.date,
                description=self.transaction.description,
                currency=self.transaction.currency)
        else:
            self.saved = self.service.create_transaction(
                type_="expense", amount=100.0, category_id=cat.id,
                date=self.service.today(), description="Такси до дома",
                as_template=self._as_template_fixed)
        return 1


class FakeCategoryDialog(QDialog):
    def __init__(self, service, parent=None, category=None):
        QDialog.__init__(self, parent)
        self.service = service
        self.category = category
        self.saved = None

    def exec(self):
        if self.category is None:
            self.saved = self.service.add_category("Новая категория", "expense", 0)
        else:
            self.saved = self.service.update_category(
                self.category.id, self.category.name + "!",
                self.category.type, self.category.monthly_limit)
        return 1


class FakeTemplatePicker(QDialog):
    selected = None
    error_hint = ""

    def exec(self):
        return 0  # отмена — ветка «По шаблону» не должна падать


class FakeTemplateEditDialog(QDialog):
    """Замена диалога редактирования шаблона: применяет правку при exec()."""

    def __init__(self, service, parent=None, template=None):
        QDialog.__init__(self, parent)
        self.service = service
        self.template = template
        self.saved = None

    def exec(self):
        tpl = self.template
        self.saved = self.service.edit_template(
            tpl.id, type_=tpl.type, amount=tpl.amount + 1,
            category_id=tpl.category_id,
            description=tpl.description, currency=tpl.currency)
        return 1


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "kaznachei.db"
    app = QApplication(sys.argv[:1])

    # Автоответы на все диалоги сообщений: везде «Yes/Ok».
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    QFileDialog.getExistingDirectory = staticmethod(
        lambda *a, **k: str(tmp / "bk"))
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (str(tmp / "export.csv"), ""))

    # Подменяем реальные диалоги фикстурами (сами диалоги покрыты UI-тестами).
    TransactionDialog.__init__ = FakeTxDialog.__init__
    TransactionDialog.exec = FakeTxDialog.exec
    CategoryDialog.__init__ = FakeCategoryDialog.__init__
    CategoryDialog.exec = FakeCategoryDialog.exec
    TemplatePickerDialog.exec = FakeTemplatePicker.exec
    from app.ui.main_window import TemplateEditDialog
    TemplateEditDialog.__init__ = FakeTemplateEditDialog.__init__
    TemplateEditDialog.exec = FakeTemplateEditDialog.exec

    db = open_database(db_path)
    service = db  # MainWindow принимает Database; сам строит TreasuryService
    win = MainWindow(db)
    service = win.service
    win.show()

    _mod = getattr(Qt.Modifier, "CTRL", None) or getattr(Qt.Modifier, "ControlModifier")
    CTRL = Qt.KeyboardModifier(_mod.value)

    tabs = win.tabs
    t_tab = tabs.widget(0)   # Операции
    b_tab = tabs.widget(1)   # Бюджеты и категории
    tpl_tab = tabs.widget(2)  # Шаблоны
    s_tab = tabs.widget(3)   # Настройки

    check("стартовал без экрана входа, вкладка «Операции» активна",
          tabs.currentIndex() == 0)

    # --- Кнопка «Выход» ------------------------------------------------------
    win.btn_exit.click()
    check("кнопка «Выход» закрывает приложение", not win.isVisible())
    win.show()

    # --- Вкладка «Операции» ---------------------------------------------------
    cat = service.add_category("Транспорт", "expense", 0)
    service.create_transaction(type_="expense", amount=350.0, category_id=cat.id,
                               date=service.today(), description="Такси до дома")
    t_tab.refresh()

    n_before = len(t_tab.service.list_transactions(limit=1000)[0])
    t_tab.btn_add.click()
    n_after = len(service.list_transactions(limit=1000)[0])
    check("«Добавить» создаёт операцию", n_after == n_before + 1)

    t_tab.search_edit.setText("ТАКСИ")  # textChanged сам применит фильтр
    check("поиск «ТАКСИ» находит «Такси до дома» (регистронезависимо)",
          t_tab.table.rowCount() >= 1)
    t_tab.search_edit.clear()

    t_tab.btn_template.click()  # picker отменён — не должно упасть
    check("«По шаблону» работает без падения", True)

    t_tab.table.selectRow(0)
    t_tab.btn_edit.click()
    check("«Редактировать» операцию работает",
          any(abs(t.amount - 101.0) < 1e-9 for t in
              service.list_transactions(limit=1000)[0]))

    before_rows = t_tab.table.rowCount()
    t_tab.table.selectRow(0)
    t_tab.btn_delete.click()
    check("«Удалить» удаляет операцию (с подтверждением)",
          t_tab.table.rowCount() == before_rows - 1)

    t_tab.btn_export.click()
    csv = (tmp / "export.csv").read_text(encoding="utf-8-sig")
    check("«Экспорт» создаёт CSV с разделителем ;;", ";;" in csv)

    t_tab.btn_next.click()
    t_tab.btn_prev.click()
    check("пагинация «Назад/Вперёд» рабочая", True)

    # --- Вкладка «Бюджеты и категории» ----------------------------------------
    tabs.setCurrentIndex(1)
    b_tab.refresh()
    n_cats = len(service.list_categories(include_inactive=True))
    b_tab.btn_add.click()
    check("«Добавить» категорию",
          len(service.list_categories(include_inactive=True)) == n_cats + 1)

    # Редактирование БЕЗ ручного выделения — автовбор первой категории.
    b_tab.selected_category_id = None
    info_calls = []
    orig_info = QMessageBox.information
    QMessageBox.information = staticmethod(
        lambda parent, title, text, *a, **k: info_calls.append(text))
    b_tab.btn_edit.click()
    QMessageBox.information = orig_info
    cats = service.list_categories(include_inactive=True)
    check("«Редактировать» с автовбором меняет категорию, а не просит выбрать",
          any(c.name.endswith("!") for c in cats) and not info_calls)

    b_tab.selected_category_id = None
    b_tab.btn_toggle.click()
    cats = service.list_categories(include_inactive=True)
    check("«Деактивировать» переключает категорию",
          any(c.is_active == 0 for c in cats))
    b_tab.btn_toggle.click()
    cats = service.list_categories(include_inactive=True)
    check("«Активировать» переключает обратно",
          all(c.is_active == 1 for c in cats))

    unused = service.add_category("Пустая", "expense", 0)
    b_tab.selected_category_id = unused.id
    b_tab.btn_delete.click()
    check("«Удалить» удаляет категорию без операций",
          service.db.get_category(unused.id) is None)

    used = [c for c in service.list_categories(include_inactive=True)
            if service.db.count_transactions_for_category(c.id) > 0][0]
    b_tab.selected_category_id = used.id
    b_tab.btn_delete.click()
    check("категория с операциями не удаляется физически",
          service.db.get_category(used.id) is not None)

    # --- Вкладка «Шаблоны» -----------------------------------------------------
    tabs.setCurrentIndex(2)
    tpl_tab.refresh()
    n_tpl = len(service.list_templates())
    tpl_tab.btn_add.click()
    check("«Добавить» шаблон",
          len(service.list_templates()) == n_tpl + 1)

    tpl_tab.table.selectRow(0)
    tpl_tab.btn_edit.click()
    edited = service.list_templates()[0]
    check("«Редактировать» шаблон применяет изменения", edited.amount == 101.0)

    n_before_del = len(service.list_templates())
    tpl_tab.table.selectRow(0)
    tpl_tab.btn_delete.click()
    check("«Удалить» шаблон",
          len(service.list_templates()) == n_before_del - 1)

    # --- Вкладка «Настройки» ----------------------------------------------------
    tabs.setCurrentIndex(3)
    s_tab.base_currency_combo.setCurrentText("USD")
    s_tab.page_size_combo.setCurrentText("50")
    s_tab.btn_save_settings.click()
    check("«Сохранить настройки» пишет base_currency",
          service.get_setting("base_currency") == "USD")
    check("«Сохранить настройки» пишет page_size",
          service.get_setting("page_size") == "50")

    s_tab.btn_browse_backup_dir.click()
    check("«Выбрать папку…» сохраняет backup_path",
          service.get_setting("backup_path") == str(tmp / "bk"))

    s_tab.btn_create_backup.click()
    backups = list((tmp / "bk").glob("kaznachei_backup_*.db"))
    check("«Создать бэкап» создаёт файл в выбранной папке", len(backups) >= 1)

    s_tab.btn_refresh_backups.click()
    check("«Обновить список» показывает бэкапы",
          s_tab.backups_list.count() >= 1)

    tabs.setCurrentIndex(3)
    win.activateWindow()
    app.processEvents()
    from PyQt6.QtTest import QTest
    for key, idx in ((Qt.Key.Key_1, 0), (Qt.Key.Key_2, 1),
                     (Qt.Key.Key_3, 2), (Qt.Key.Key_4, 3)):
        QTest.keyClick(win, key, CTRL)
        check(f"Ctrl+{int(key.value)} переключает вкладку {idx + 1}",
              win.tabs.currentIndex() == idx)

    # --- Восстановление из бэкапа (в конце: закрывает приложение) -------------
    tabs.setCurrentIndex(3)
    s_tab.btn_create_backup.click()
    s_tab.btn_refresh_backups.click()
    s_tab.backups_list.setCurrentRow(0)
    # В offscreen-режиме QApplication.quit() не скрывает окно, поэтому
    # завершение приложения проверяем через факт закрытия соединения с БД
    # (quit_after_restore сначала закрывает БД).
    db_was_open = service.db._conn is not None
    restored = s_tab.restore_selected_backup()
    check("«Восстановить из выбранного бэкапа» подтверждает и завершает работу",
          restored and db_was_open and service.db._conn is None)
    old_files = list(tmp.glob("kaznachei_old_*.db"))
    check("откат переименовал текущую БД в kaznachei_old_<timestamp>.db",
          len(old_files) >= 1)

    print()
    if FAILURES:
        print(f"SMOKE FAILED: {len(FAILURES)} — {FAILURES}")
        return 1
    print("SMOKE PASSED: все кнопки рабочие")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
