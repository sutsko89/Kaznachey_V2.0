"""UI-тесты (pytest-qt): главное окно, вкладки, диалоги, горячие клавиши.

Запускаются с QT_QPA_PLATFORM=offscreen.
"""
from __future__ import annotations

import pytest
from pathlib import Path

pytest.importorskip("pytestqt")

from PyQt6.QtCore import QDate, Qt  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QGroupBox, QLabel, QMessageBox, QPushButton,
)

# PyQt6 (installed version) exposes Qt.Modifier.CTRL; older bindings use ControlModifier.
# QTest.keyClick expects a KeyboardModifier, so convert via the underlying int value.
_mod = getattr(Qt.Modifier, "CTRL", None) or getattr(Qt.Modifier, "ControlModifier")
CTRL = Qt.KeyboardModifier(_mod.value)

from app.dal.database import Transaction  # noqa: E402
from app.domain.service import format_amount_ru  # noqa: E402
from app.ui.tabs.dashboard_tab import (  # noqa: E402
    INCOME_COLOR_DARK, INCOME_COLOR_LIGHT)
from app.ui.dialogs_misc import CategoryDialog, TemplatePickerDialog  # noqa: E402
from app.ui.dialogs_transaction import TransactionDialog  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture()
def window(qtbot, db, service):
    w = MainWindow(db)
    qtbot.addWidget(w)
    w.show()
    return w


class TestMainWindow:
    def test_opens_without_login(self, window):
        """Нет экрана входа: сразу главное окно; вкладки по ТЗ + Дашборд."""
        assert window.windowTitle() == "Казначей"
        tabs = [window.tabs.tabText(i) for i in range(window.tabs.count())]
        assert tabs == ["Операции", "Бюджеты и категории", "Дашборд",
                        "Шаблоны", "Настройки"]

    def test_minimum_size(self, window):
        m = window.minimumSize()
        assert m.width() >= 1000 and m.height() >= 700

    @pytest.mark.parametrize("key_code,idx", [("2", 1), ("3", 2), ("4", 4), ("1", 0)])
    def test_hotkeys_switch_tabs(self, window, qtbot, key_code, idx):
        key = getattr(Qt.Key, f"Key_{key_code}")
        qtbot.keyClick(window, key, CTRL)
        assert window.tabs.currentIndex() == idx

    def test_ctrl_f_focuses_search(self, window, qtbot):
        window.tabs.setCurrentIndex(0)
        qtbot.keyClick(window.transactions_tab.search_edit, Qt.Key.Key_F, CTRL)
        assert window.transactions_tab.search_edit.hasFocus()


class TestTransactionsTab:
    def test_add_via_dialog(self, window, qtbot, service, category):
        dlg = TransactionDialog(service, window)
        qtbot.addWidget(dlg)
        idx = dlg.category_combo.findData(category.id)
        assert idx >= 0, "категория расхода должна быть в списке диалога"
        dlg.category_combo.setCurrentIndex(idx)
        dlg.amount_spin.setValue(250.0)
        dlg.description_edit.setText("Ужин")
        dlg._on_accept()
        assert dlg.saved is not None
        rows, total = service.list_transactions()
        assert total == 1

    def test_missing_category_shows_inline_error(self, qtbot, service):
        dlg = TransactionDialog(service)
        qtbot.addWidget(dlg)
        dlg.category_combo.clear()  # ни одной категории нет
        dlg.amount_spin.setValue(10)
        dlg._on_accept()
        lbl = dlg.error_labels["category_id"]
        assert lbl.text() == "Категория обязательна"
        assert not lbl.isHidden()
        assert dlg.result() == 0  # диалог не закрылся

    def test_delete_requires_confirmation(self, window, qtbot, service, category,
                                          monkeypatch):
        tr = service.create_transaction(type_="expense", amount=50,
                                        category_id=category.id, date="2026-10-01")
        tab = window.transactions_tab
        tab.refresh()
        assert tab.table.rowCount() == 1

        asked_texts = []
        answers = iter([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])

        def fake_question(*a, **k):
            asked_texts.append(a[2])
            return next(answers)

        monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
        tab.table.selectRow(0)
        tab.btn_delete.click()
        assert service.db.get_transaction(tr.id) is not None  # отказ — не удалила
        tab.table.selectRow(0)
        tab.btn_delete.click()
        assert service.db.get_transaction(tr.id) is None
        assert asked_texts[0].startswith("Удалить операцию на сумму ")

    def test_search_filters_table(self, window, qtbot, service, category):
        service.create_transaction(type_="expense", amount=10, category_id=category.id,
                                   date="2026-10-01", description="Такси аэропорт")
        service.create_transaction(type_="expense", amount=20, category_id=category.id,
                                   date="2026-10-02", description="Хлеб")
        window._refresh_all()
        tab = window.transactions_tab
        tab.search_edit.setText("такси")
        assert tab.table.rowCount() == 1
        tab.search_edit.setText("")
        assert tab.table.rowCount() == 2

    def test_summary_shown(self, window, service, category, income_category):
        service.create_transaction(type_="income", amount=1000,
                                   category_id=income_category.id, date="2026-10-01")
        service.create_transaction(type_="expense", amount=400,
                                   category_id=category.id, date="2026-10-02")
        window._refresh_all()
        text = window.transactions_tab.summary_label.text()
        assert "Доходы:" in text and "Расходы:" in text and "Баланс:" in text
        assert "600,00" in text

    def test_pagination_next_prev(self, window, service, category, monkeypatch):
        for i in range(30):
            service.create_transaction(type_="expense", amount=1, category_id=category.id,
                                       date=f"2026-10-{(i % 28) + 1:02d}")
        tab = window.transactions_tab
        tab.page_size = lambda: 25
        tab.refresh()
        assert tab.table.rowCount() == 25
        assert tab.btn_next.isEnabled()
        tab.btn_next.click()
        assert tab.table.rowCount() == 5
        tab.btn_prev.click()
        assert tab.table.rowCount() == 25


class TestBudgetsTab:
    def test_overbudget_red_and_top(self, window, service):
        c_ok = service.add_category("Связь", "expense", 1000)
        c_bad = service.add_category("Продукты", "expense", 100)
        today = service.today()
        service.create_transaction(type_="expense", amount=50, category_id=c_ok.id,
                                   date=today)
        service.create_transaction(type_="expense", amount=200, category_id=c_bad.id,
                                   date=today)
        window.budgets_tab.refresh()
        tab = window.budgets_tab
        # проверяем isHidden, а не isVisible: у дочерних виджетов скрытого
        # контейнера isVisible() может возвращать True (Qt-семантика)
        assert not tab.over_frame.isHidden()
        assert "Продукты" in tab.over_label.text()
        # первая строка списка — превышающая категория, подсвечена красным
        first = tab.list_layout.itemAt(0).widget()
        labels = [w.text() for w in first.findChildren(QLabel)]
        assert any("Продукты" in t for t in labels)
        red = [w for w in first.findChildren(QLabel) if "red" in w.styleSheet()]
        assert red, "перерасход должен быть красным"

    def test_progress_text_format(self, window, service):
        cat = service.add_category("Одежда", "expense", 1000)
        service.create_transaction(type_="expense", amount=250, category_id=cat.id,
                                   date=service.today())
        window.budgets_tab.refresh()
        tab = window.budgets_tab
        row = tab.list_layout.itemAt(0).widget()
        texts = " ".join(w.text() for w in row.findChildren(QLabel))
        assert "Потрачено 250,00 / 1 000,00 руб. (25%)" in texts.replace("\u2009", " ")

    def test_delete_category_in_use_blocked(self, window, service, category, monkeypatch):
        service.create_transaction(type_="expense", amount=10, category_id=category.id,
                                   date=service.today())
        tab = window.budgets_tab
        tab.refresh()
        shown = [tab.list_layout.itemAt(i).widget()
                 for i in range(tab.list_layout.count())]
        row = next(w for w in shown
                   if w is not None and w.property("category_id") == category.id)
        row.mousePressEvent(None)
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        warnings = []
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: warnings.append(a[2])))
        tab.btn_delete.click()
        assert warnings and "деактивировать" in warnings[0]
        assert service.db.get_category(category.id) is not None

    def test_deactivate_activate_button(self, window, service):
        cat = service.add_category("Закрытая", "expense")
        tab = window.budgets_tab
        tab.refresh()
        shown = [tab.list_layout.itemAt(i).widget()
                 for i in range(tab.list_layout.count())]
        row = next(w for w in shown
                   if w is not None and w.property("category_id") == cat.id)
        row.mousePressEvent(None)
        tab.btn_toggle.click()
        assert not service.db.get_category(cat.id).is_active
        tab.btn_toggle.click()
        assert service.db.get_category(cat.id).is_active


class TestTemplatesTab:
    def test_template_flow_ui(self, window, qtbot, service, category):
        tpl = service.create_template_from_data(type_="expense", amount=300,
                                                category_id=category.id,
                                                description="ЖКХ")
        window._refresh_all()
        tab = window.templates_tab
        assert tab.table.rowCount() == 1

        picker = TemplatePickerDialog(service, window)
        qtbot.addWidget(picker)
        assert picker.list_widget.count() == 1
        picker.list_widget.setCurrentRow(0)
        picker._on_ok()
        assert picker.selected is not None and picker.selected.id == tpl.id

        # операция по шаблону: дата подставляется сегодняшняя
        dlg = TransactionDialog(service, window, template_prefill=picker.selected)
        qtbot.addWidget(dlg)
        assert dlg.date_edit.date() == QDate.currentDate()
        dlg._on_accept()
        assert dlg.saved is not None and not dlg.saved.is_template
        rows, total = service.list_transactions()
        assert total == 1

    def test_template_delete_confirmation(self, window, service, category, monkeypatch):
        tpl = service.create_template_from_data(type_="expense", amount=100,
                                                category_id=category.id,
                                                description="ЖКХ")
        created = service.use_template(tpl.id)  # операция по шаблону
        window._refresh_all()
        tab = window.templates_tab
        tab.table.selectRow(0)

        asked = []
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: (asked.append(a[2]),
                                                          QMessageBox.StandardButton.Yes)[1]))
        tab.btn_delete.click()
        assert asked[0] == \
            "Удалить шаблон «ЖКХ»? Операции, созданные по нему ранее, останутся."
        assert service.list_templates() == []
        # операция осталась
        assert service.db.get_transaction(created.id) is not None

    def test_templates_not_in_transactions_feed(self, window, service, category):
        service.create_template_from_data(type_="expense", amount=300,
                                          category_id=category.id)
        window._refresh_all()
        assert window.transactions_tab.table.rowCount() == 0


class TestSettingsTab:
    def test_no_security_controls(self, window):
        """В настройках нет ПИН/пароля/входа/регистрации (ТЗ 11.6)."""
        widgets = [w for w in window.settings_tab.findChildren(
            (QLabel, QPushButton)) if hasattr(w, "text")]
        groups = [g.title() for g in window.settings_tab.findChildren(QGroupBox)]
        all_texts = " ".join([w.text() for w in widgets] + groups)
        lowered = all_texts.lower()
        for forbidden in ("пин", "пароль", "регистр", "вход", "пользовател"):
            assert forbidden not in lowered

    def test_blocks_present(self, window):
        groups = [g.title() for g in window.settings_tab.findChildren(QGroupBox)]
        assert "Данные и отображение" in groups
        assert "Резервные копии и откат" in groups

    def test_create_backup_button(self, window, monkeypatch, tmp_path):
        # бэкапы пишем в отдельную папку этого теста (по умолчанию — рядом с БД)
        window.service.db.set_setting("backup_path", str(tmp_path / "ui_backups"))
        window.settings_tab.load_values()
        monkeypatch.setattr(QMessageBox, "information",
                            staticmethod(lambda *a, **k: None))
        ok = window.settings_tab.create_backup()
        assert ok
        assert window.settings_tab.backups_list.count() == 1
        name = window.settings_tab.backups_list.item(0).text()
        assert name.startswith("kaznachei_backup_") and name.endswith(".db")

    def test_restore_requires_confirmation(self, window, monkeypatch, tmp_path):
        window.service.db.set_setting("backup_path", str(tmp_path / "ui_backups"))
        window.settings_tab.load_values()
        monkeypatch.setattr(QMessageBox, "information",
                            staticmethod(lambda *a, **k: None))
        window.settings_tab.create_backup()
        window.settings_tab.backups_list.setCurrentRow(0)
        asked = {}

        def fake_question(*a, **k):
            asked["text"] = a[2]
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
        result = window.settings_tab.restore_selected_backup()
        assert result is False
        assert ("Вы уверены, что хотите заменить текущую БД выбранной резервной копией? "
                "Все изменения после даты бэкапа будут потеряны.") == asked["text"]

    def test_save_settings_roundtrip(self, window, monkeypatch):
        monkeypatch.setattr(QMessageBox, "information",
                            staticmethod(lambda *a, **k: None))
        tab = window.settings_tab
        tab.theme_combo.setCurrentIndex(1)   # тёмная
        tab.scale_combo.setCurrentText("125%")
        tab.page_size_combo.setCurrentText("25")
        tab.btn_save_settings.click()
        assert tab.service.get_setting("theme") == "dark"
        assert tab.service.get_setting("ui_scale") == "125"
        assert tab.service.get_setting("page_size") == "25"


class TestCategoryDialog:
    def test_hint_and_validation(self, qtbot, service):
        dlg = CategoryDialog(service)
        qtbot.addWidget(dlg)
        hints = [w.text() for w in dlg.findChildren(QLabel)]
        assert any("0 = без лимита" in t for t in hints)
        # пустое имя — ошибка, диалог не закрывается
        dlg._on_accept()
        assert dlg.error_label.text() == "Название категории обязательно"
        assert not dlg.error_label.isHidden()
        dlg.name_edit.setText("Транспорт")
        dlg.limit_spin.setValue(500)
        dlg._on_accept()
        assert dlg.saved is not None
        assert service.list_categories()[0].monthly_limit == 500

    def test_type_locked_when_has_transactions(self, qtbot, service, category):
        service.db.add_transaction(Transaction(
            type="expense", amount=1, category_id=category.id,
            date="2026-10-01", created_at="now"))
        dlg = CategoryDialog(service, category=category)
        qtbot.addWidget(dlg)
        assert not dlg.type_combo.isEnabled()
        assert any("Тип нельзя изменить" in w.text()
                   for w in dlg.findChildren(QLabel))


class TestDashboardTab:
    """Вкладка «Дашборд»: сводка доходов/расходов с выбором периода."""

    def test_tab_present(self, window):
        assert window.tabs.tabText(2) == "Дашборд"

    def test_summary_cards(self, window, service, category, income_category):
        service.create_transaction(type_="expense", amount=400.5,
                                   category_id=category.id, date=service.today())
        service.create_transaction(type_="income", amount=1000.0,
                                   category_id=income_category.id, date=service.today())
        window._refresh_all()
        tab = window.dashboard_tab
        assert tab.lbl_income.text() == format_amount_ru(1000.0)
        assert tab.lbl_expense.text() == format_amount_ru(400.5)
        assert tab.lbl_balance.text() == format_amount_ru(599.5)

    def test_period_modes_ranges(self, window, service):
        import datetime as dt
        tab = window.dashboard_tab
        today = dt.date.today()
        # текущий месяц
        tab.period_combo.setCurrentIndex(0)
        d1, d2 = tab.current_range()
        assert d1 == today.replace(day=1).isoformat()
        assert d2 >= today.isoformat()
        # предыдущий месяц
        tab.period_combo.setCurrentIndex(1)
        d1, d2 = tab.current_range()
        assert d2 < today.replace(day=1).isoformat()
        # год
        tab.period_combo.setCurrentIndex(3)
        d1, d2 = tab.current_range()
        assert d1.startswith(str(today.year)) and d2.endswith("-12-31")

    def test_specific_month_selection(self, window, service, category):
        service.create_transaction(type_="expense", amount=111.0,
                                   category_id=category.id, date="2026-08-15")
        tab = window.dashboard_tab
        idx = tab.period_combo.findData("month")
        tab.period_combo.setCurrentIndex(idx)
        m_idx = tab.month_combo.findData("2026-08")
        assert m_idx >= 0
        tab.month_combo.setCurrentIndex(m_idx)
        assert tab.lbl_expense.text() == format_amount_ru(111.0)
        # переключение на другой месяц — данные меняются
        tab.month_combo.setCurrentIndex(tab.month_combo.findData("2026-07"))
        assert tab.lbl_expense.text() == format_amount_ru(0.0)

    def test_custom_period_validation(self, window, qtbot, monkeypatch):
        tab = window.dashboard_tab
        tab.period_combo.setCurrentIndex(tab.period_combo.findData("custom"))
        tab.start_edit.setText("2026-10-10")
        tab.end_edit.setText("2026-10-01")  # начало позже конца
        shown = {}
        def fake_warning(*a, **k):
            shown["text"] = a[2] if len(a) > 2 else ""
        monkeypatch.setattr("app.ui.tabs.dashboard_tab.QMessageBox.warning",
                            staticmethod(fake_warning))
        tab.refresh()
        assert "период" in shown.get("text", "").lower() or shown

    def test_breakdown_tables(self, window, service, category, income_category):
        service.create_transaction(type_="expense", amount=300.0,
                                   category_id=category.id, date=service.today())
        service.create_transaction(type_="expense", amount=100.0,
                                   category_id=category.id, date=service.today())
        service.create_transaction(type_="income", amount=500.0,
                                   category_id=income_category.id, date=service.today())
        window._refresh_all()
        tab = window.dashboard_tab
        te = tab.table_expenses
        assert te.rowCount() == 1
        assert te.item(0, 1).text() == format_amount_ru(400.0)
        assert te.item(0, 2).text() == "100.0%"
        ti = tab.table_incomes
        assert ti.rowCount() == 1
        assert ti.item(0, 1).text() == format_amount_ru(500.0)

    def test_templates_not_counted(self, window, service, category):
        service.create_template_from_data(type_="expense", amount=999.0,
                                          category_id=category.id)
        window._refresh_all()
        assert window.dashboard_tab.lbl_expense.text() == format_amount_ru(0.0)

    def test_bars_widget_updated(self, window, service, category):
        service.create_transaction(type_="expense", amount=50.0,
                                   category_id=category.id, date=service.today())
        window._refresh_all()
        data = window.dashboard_tab.bars._data
        assert data and sum(v for _, v, _ in data) + sum(v for _, _, v in data) > 0

    def test_theme_colors_apply(self, window, service):
        window.dashboard_tab.apply_theme(True)
        assert window.dashboard_tab.bars.income_color == INCOME_COLOR_DARK
        window.dashboard_tab.apply_theme(False)
        assert window.dashboard_tab.bars.income_color == INCOME_COLOR_LIGHT
