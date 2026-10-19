"""Unit-тесты бизнес-логики (ТЗ, разделы 8–9).

Валидация, операции, шаблоны, лимиты, превышения, категории, настройки,
форматы локализации.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.domain.service import (
    TreasuryService,
    format_amount_ru,
    from_display_date,
    to_display_date,
)
from app.domain.validators import validate_date
from app.errors import CategoryInUseError, ValidationError


class TestValidators:
    def test_validate_date_ok(self):
        assert validate_date("2026-02-28") == "2026-02-28"

    def test_validate_date_bad_format(self):
        with pytest.raises(ValidationError):
            validate_date("28.02.2026")

    def test_validate_date_not_calendar(self):
        with pytest.raises(ValidationError):
            validate_date("2026-02-30")
        with pytest.raises(ValidationError):
            validate_date("2026-13-01")

    def test_transaction_all_errors_collected(self, service, category):
        with pytest.raises(ValidationError) as exc:
            service.create_transaction(
                type_="wrong", amount_minor=-500, category_id=0,
                date="не дата", currency="РОБ")
        errs = exc.value.errors
        assert {"type", "amount", "category_id", "date", "currency"} <= set(errs)


class TestTransactions:
    def test_create_and_read(self, service, category):
        tr = service.create_transaction(type_="expense", amount=100.5,
                                        category_id=category.id, date="2026-10-01",
                                        description="Молоко")
        assert tr.id is not None and not tr.is_template
        got = service.db.get_transaction(tr.id)
        assert got.amount_minor == 10050 and got.description == "Молоко"

    def test_zero_amount_rejected(self, service, category):
        with pytest.raises(ValidationError) as e:
            service.create_transaction(type_="expense", amount=0,
                                       category_id=category.id, date="2026-10-01")
        assert "amount" in e.value.errors

    def test_category_required(self, service):
        with pytest.raises(ValidationError) as e:
            service.create_transaction(type_="expense", amount=10,
                                       category_id=None, date="2026-10-01")
        assert "category_id" in e.value.errors

    def test_type_must_match_category(self, service, income_category):
        with pytest.raises(ValidationError) as e:
            service.create_transaction(type_="expense", amount=10,
                                       category_id=income_category.id,
                                       date="2026-10-01")
        assert "type" in e.value.errors

    def test_edit_transaction(self, service, category):
        tr = service.create_transaction(type_="expense", amount=10,
                                        category_id=category.id, date="2026-10-01")
        edited = service.edit_transaction(tr.id, amount_minor=2000, description="изм.")
        assert edited.amount_minor == 2000 and edited.description == "изм."
        assert edited.updated_at is not None

    def test_edit_missing_transaction(self, service):
        with pytest.raises(ValidationError):
            service.edit_transaction(999999, amount=1)

    def test_delete_transaction(self, service, category):
        tr = service.create_transaction(type_="expense", amount=10,
                                        category_id=category.id, date="2026-10-01")
        service.delete_transaction(tr.id)
        assert service.db.get_transaction(tr.id) is None

    def test_confirm_text_for_delete(self, service, category):
        tr = service.create_transaction(type_="expense", amount=1234.5,
                                        category_id=category.id, date="2026-10-05")
        text = service.confirm_text_for_delete(tr)
        assert text.startswith("Удалить операцию на сумму ")
        assert "1 234,50" in text.replace("\u2009", " ")
        assert "05.10.2026" in text and text.endswith("?")

    def test_templates_excluded_from_feed_and_balances(self, service, category):
        service.create_transaction(type_="expense", amount=100,
                                   category_id=category.id, date="2026-10-01",
                                   as_template=True)
        rows, total = service.list_transactions()
        assert total == 0 and rows == []
        s = service.period_summary("2026-10-01", "2026-10-31")
        assert s["expense"] == 0


class TestTemplates:
    def test_create_list_use(self, service, category):
        tpl = service.create_template_from_data(type_="expense", amount=500,
                                                category_id=category.id,
                                                description="ЖКХ")
        assert tpl.is_template
        assert [t.id for t in service.list_templates()] == [tpl.id]

        tr = service.use_template(tpl.id)
        assert not tr.is_template
        assert tr.amount_minor == 50000 and tr.category_id == category.id
        assert tr.date == TreasuryService.today()  # сегодняшняя дата

    def test_use_with_overrides(self, service, category):
        tpl = service.create_template_from_data(type_="expense", amount=500,
                                                category_id=category.id)
        tr = service.use_template(tpl.id, amount_minor=70000, date="2026-09-01",
                                  description="оплата за сентябрь")
        assert tr.amount_minor == 70000 and tr.date == "2026-09-01"

    def test_edit_and_delete_template(self, service, category):
        tpl = service.create_template_from_data(type_="expense", amount=100,
                                                category_id=category.id,
                                                description="Связь")
        service.edit_template(tpl.id, amount_minor=20000)
        assert service.db.get_transaction(tpl.id).amount_minor == 20000
        service.delete_template(tpl.id)
        assert service.list_templates() == []

    def test_delete_template_keeps_transactions(self, service, category):
        tpl = service.create_template_from_data(type_="expense", amount=100,
                                                category_id=category.id,
                                                description="ЖКХ")
        tr = service.use_template(tpl.id)
        service.delete_template(tpl.id)
        assert service.db.get_transaction(tr.id) is not None

    def test_confirm_text(self, service, category):
        tpl = service.create_template_from_data(type_="expense", amount=100,
                                                category_id=category.id,
                                                description="ЖКХ")
        text = service.confirm_text_for_delete_template(tpl)
        assert text == "Удалить шаблон «ЖКХ»? Операции, созданные по нему ранее, останутся."

    def test_validation_same_as_transaction(self, service, income_category):
        with pytest.raises(ValidationError) as e:
            service.create_template_from_data(type_="wrong", amount=-1,
                                              category_id=income_category.id)
        assert {"amount", "type"} <= set(e.value.errors)

    def test_edit_regular_tx_as_template_fails(self, service, category):
        tr = service.create_transaction(type_="expense", amount=10,
                                        category_id=category.id, date="2026-10-01")
        with pytest.raises(ValidationError):
            service.edit_template(tr.id, amount=5)
        with pytest.raises(ValidationError):
            service.delete_template(tr.id)
        with pytest.raises(ValidationError):
            service.use_template(tr.id)


class TestCategoriesAndLimits:
    def test_add_income_limit_forced_zero(self, service):
        cat = service.add_category("Зарплата", "income", monthly_limit=1000)
        assert cat.monthly_limit_minor == 0  # для доходов лимит не применяется

    def test_duplicate_name_type(self, service):
        service.add_category("Продукты", "expense")
        with pytest.raises(ValidationError) as e:
            service.add_category("Продукты", "expense")
        assert "name" in e.value.errors
        # тот же name, другой тип — ок
        service.add_category("Продукты", "income")

    def test_empty_name_rejected(self, service):
        with pytest.raises(ValidationError):
            service.add_category("  ", "expense")

    def test_negative_limit_rejected(self, service):
        with pytest.raises(ValidationError):
            service.add_category("X", "expense", monthly_limit=-1)

    def test_delete_category_with_transactions_forbidden(self, service, category):
        service.create_transaction(type_="expense", amount=10,
                                   category_id=category.id, date="2026-10-01")
        with pytest.raises(CategoryInUseError):
            service.delete_category(category.id)
        assert service.db.get_category(category.id) is not None

    def test_delete_empty_category_ok(self, service):
        cat = service.add_category("Пустая", "expense")
        service.delete_category(cat.id)
        assert service.db.get_category(cat.id) is None

    def test_activate_deactivate(self, service):
        cat = service.add_category("Закрытая", "expense")
        service.set_category_active(cat.id, False)
        assert not service.db.get_category(cat.id).is_active
        assert service.list_categories(include_inactive=False) == []
        service.set_category_active(cat.id, True)
        assert service.db.get_category(cat.id).is_active

    def test_change_type_blocked_when_has_transactions(self, service, category):
        service.create_transaction(type_="expense", amount=10,
                                   category_id=category.id, date="2026-10-01")
        with pytest.raises(ValidationError) as e:
            service.update_category(category.id, "Продукты", "income")
        assert "type" in e.value.errors
        # без операций — можно
        cat2 = service.add_category("Транспорт", "expense")
        service.update_category(cat2.id, "Транспорт", "income")
        assert service.db.get_category(cat2.id).type == "income"

    def test_budget_status_percent_and_over(self, service, category):
        service.update_category(category.id, "Продукты", "expense", 1000)
        month = TreasuryService.current_month()
        today = TreasuryService.today()
        service.create_transaction(type_="expense", amount_minor=40000,
                                   category_id=category.id, date=today)
        status = service.budget_status(month)
        row = next(s for s in status if s["category"].id == category.id)
        assert row["spent"] == 40000 and row["limit"] == 100000
        assert float(row["percent"]) == 40.0 and not row["over"]

        service.create_transaction(type_="expense", amount_minor=70000,
                                   category_id=category.id, date=today)
        status = service.budget_status(month)
        row = next(s for s in status if s["category"].id == category.id)
        assert row["spent"] == 110000 and row["over"]

    def test_overbudget_sorted_first(self, service):
        c1 = service.add_category("Обычная", "expense", "100")
        c2 = service.add_category("Я перерасход", "expense", "100")
        today = TreasuryService.today()
        service.create_transaction(type_="expense", amount_minor=5000,
                                   category_id=c1.id, date=today)
        service.create_transaction(type_="expense", amount_minor=15000,
                                   category_id=c2.id, date=today)
        status = service.budget_status()
        assert status[0]["category"].id == c2.id  # превышение — сверху
        over = service.overbudget_categories()
        assert [o["category"].id for o in over] == [c2.id]

    def test_limit_zero_means_unlimited(self, service, category):
        # лимит 0 = без лимита (fixture создаёт категорию с лимитом 1000 — обнуляем)
        service.update_category(category.id, "Продукты", "expense", 0)
        today = TreasuryService.today()
        service.create_transaction(type_="expense", amount=10**6,
                                   category_id=category.id, date=today)
        row = next(s for s in service.budget_status()
                   if s["category"].id == category.id)
        assert row["limit"] == 0 and row["percent"] is None and not row["over"]

    def test_templates_do_not_affect_limits(self, service, category):
        service.update_category(category.id, "Продукты", "expense", "100")
        service.create_transaction(type_="expense", amount_minor=50000,
                                   category_id=category.id,
                                   date=TreasuryService.today(), as_template=True)
        row = next(s for s in service.budget_status()
                   if s["category"].id == category.id)
        assert row["spent"] == 0


class TestSummariesAndReports:
    def test_period_summary(self, service, category, income_category):
        service.create_transaction(type_="income", amount_minor=100000,
                                   category_id=income_category.id, date="2026-10-01")
        service.create_transaction(type_="expense", amount_minor=30000,
                                   category_id=category.id, date="2026-10-05")
        s = service.period_summary("2026-10-01", "2026-10-31")
        assert s["income"] == 100000 and s["expense"] == 30000 and s["balance"] == 70000

    def test_period_summary_invalid_range(self, service):
        with pytest.raises(ValidationError):
            service.period_summary("2026-10-31", "2026-10-01")

    def test_month_range_december(self):
        assert TreasuryService.month_range("2026-12") == ("2026-12-01", "2026-12-31")
        assert TreasuryService.month_range("2028-02") == ("2028-02-01", "2028-02-29")

    def test_category_report_share_and_remaining(self, service, category):
        service.update_category(category.id, "Продукты", "expense", 1000)
        inc_cat = service.add_category("Зарплата", "income")
        month = TreasuryService.current_month()
        first, last = TreasuryService.month_range(month)
        service.create_transaction(type_="expense", amount_minor=25000,
                                   category_id=category.id, date=first)
        service.create_transaction(type_="income", amount_minor=75000,
                                   category_id=inc_cat.id, date=first)
        report = service.category_report(first, last)
        exp_row = next(r for r in report if r["category_id"] == category.id)
        assert float(exp_row["share_percent"]) == 25.0
        assert exp_row["remaining_limit"] == 75000
        inc_row = next(r for r in report if r["category_id"] == inc_cat.id)
        assert inc_row["remaining_limit"] is None


class TestSettingsRules:
    @pytest.mark.parametrize("key", ["ui_scale", "page_size", "backup_path"])
    def test_user_keys_roundtrip(self, service, key):
        """FR-19: пользователь может изменять только эти три ключа."""
        service.set_setting(key, "x")
        assert service.get_setting(key) == "x"

    @pytest.mark.parametrize("key", ["base_currency", "date_format",
                                     "number_format", "theme"])
    def test_legacy_keys_not_writable_or_readable(self, service, key):
        """FR-18/19: ложные настройки удалены из продукта (только read-only в БД)."""
        with pytest.raises(ValidationError):
            service.set_setting(key, "x")
        with pytest.raises(ValidationError):
            service.get_setting(key)

    def test_db_version_internal_only(self, service):
        """db_version изменяется только механизмом миграций (DAL), не сервисом."""
        with pytest.raises(ValidationError):
            service.set_setting("db_version", "9")
        service.db.set_setting("db_version", "3")   # прямой путь DAL допустим
        assert service.db.get_setting("db_version") == "3"

    @pytest.mark.parametrize("key", ["pin_hash", "password_hash",
                                     "login_attempts", "lock_until"])
    def test_forbidden_keys(self, service, key):
        with pytest.raises(ValidationError):
            service.set_setting(key, "x")
        with pytest.raises(ValidationError):
            service.get_setting(key)

    def test_unknown_key_rejected(self, service):
        with pytest.raises(ValidationError):
            service.set_setting("some_other", "x")

    def test_backup_dir_default(self, service):
        assert service.backup_dir() == service.db.path.parent / "backups"
        service.set_setting("backup_path", "/tmp/other")
        assert str(service.backup_dir()) == "/tmp/other"


class TestLocalizationFormats:
    def test_format_amount(self):
        """FR-11: вход — целые копейки, промежуточного float нет."""
        assert format_amount_ru(1234567).replace("\u2009", " ") == "12 345,67"
        assert format_amount_ru(0) == "0,00"
        assert format_amount_ru(1) == "0,01"
        assert format_amount_ru(-100050).replace("\u2009", " ") == "-1 000,50"
        assert format_amount_ru(123456).replace("\u2009", " ") == "1 234,56"

    def test_dates(self):
        assert to_display_date("2026-10-05") == "05.10.2026"
        assert from_display_date("05.10.2026") == "2026-10-05"
