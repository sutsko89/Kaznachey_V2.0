"""Запуск приложения: одиночный экземпляр, миграции, главное окно.

Модуль безопасности отсутствует намеренно (однопользовательская редакция):
нет ПИН-кода, пароля, регистрации, счётчика попыток и блокировок.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.dal.database import Database, default_db_path  # noqa: E402
from app.errors import BackupError, MigrationError  # noqa: E402
from app.services.migrations import run_migrations  # noqa: E402


def migrations_dir() -> Path:
    """Папка migrations/ рядом с исполняемым файлом (или исходниками)."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "migrations"


def open_database(db_path: Path | None = None) -> Database:
    """Открывает БД, проверяет одиночность экземпляра и применяет миграции.

    Исключения:
    - RuntimeError("Приложение уже открыто.") — второй экземпляр на той же БД;
    - BackupError — не удалось создать бэкап перед миграцией;
    - MigrationError — ошибка применения миграции.
    """
    path = Path(db_path) if db_path else default_db_path()
    db = Database(path)
    db.connect()  # может поднять RuntimeError, если файл удерживается другим процессом
    backup_dir = Path(db.get_setting("backup_path") or (path.parent / "backups"))
    run_migrations(db, migrations_dir(), backup_dir)
    return db


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    db_path = Path(args[0]) if args else default_db_path()

    from PyQt6.QtCore import QT_VERSION_STR  # noqa: F401  (проверка доступности Qt)
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Казначей")
    try:
        db = open_database(db_path)
    except RuntimeError as exc:
        QMessageBox.warning(None, "Казначей", str(exc))
        return 2
    except BackupError as exc:
        QMessageBox.critical(None, "Казначей", str(exc))
        return 3
    except MigrationError as exc:
        QMessageBox.critical(None, "Казначей", f"Ошибка миграции базы данных. {exc}")
        return 4

    window = MainWindow(db)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
