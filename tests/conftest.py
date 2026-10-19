"""Общие фикстуры для тестов «Казначей»."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from app.dal.database import Database
from app.domain.service import TreasuryService
from app.services.migrations import run_migrations

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "kaznachei.db"


@pytest.fixture()
def backup_dir(tmp_path: Path) -> Path:
    d = tmp_path / "backups"
    d.mkdir()
    return d


@pytest.fixture()
def db(db_path: Path, backup_dir: Path) -> Database:
    """Чистая БД со всеми применёнными миграциями (актуальная схема)."""
    database = Database(db_path)
    run_migrations(database, MIGRATIONS_DIR, backup_dir)
    yield database
    database.close()


@pytest.fixture()
def service(db: Database) -> TreasuryService:
    return TreasuryService(db, MIGRATIONS_DIR)


@pytest.fixture()
def category(service: TreasuryService):
    return service.add_category("Продукты", "expense", monthly_limit=1000)


@pytest.fixture()
def income_category(service: TreasuryService):
    return service.add_category("Зарплата", "income")


def today_iso() -> str:
    return dt.date.today().isoformat()
