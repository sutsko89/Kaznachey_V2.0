"""Точка входа GUI «Казначей».

Порядок старта (FR-07): получить lock выбранной БД ДО открытия файла и
до запуска миграций; при неудаче — не открывать главное окно, не менять файл
БД, показать сообщение и завершиться с ненулевым кодом.
"""
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.dal.database import Database, default_db_path
from app.errors import BackupError, DatabaseError, MigrationError
from app.services.instance_lock import ALREADY_OPEN_MESSAGE, InstanceLock
from app.services.migrations import bundled_migrations_dir, run_migrations
from app.ui.main_window import MainWindow
from app.ui.theme import apply_light_theme


def _critical(title: str, text: str) -> int:
    """Показывает фатальную ошибку без главного окна; возвращает код завершения."""
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.critical(None, title, text)
    return 1


def bootstrap(db_path=None):
    """Открывает приложение: lock → соединения → миграции. Возвращает (rc, payload).

    rc == 0 — payload это (app, window, lock); иначе payload — текст ошибки.
    Lock возвращается вызывающему коду и хранится в памяти до полного завершения.

    Если QApplication уже существует (тесты, повторный вызов), он переиспользуется:
    создание второго экземпляра QApplication в том же процессе недопустимо и
    приводит к аварийному завершению.
    """
    app = QApplication.instance() or QApplication(sys.argv)
    apply_light_theme(app)

    path = default_db_path() if db_path is None else db_path

    # FR-16: встроенные миграции (frozen — из бандла, dev — из репозитория).
    try:
        migrations_dir = bundled_migrations_dir()
    except MigrationError as exc:
        return 1, str(exc)

    # FR-07: постоянная блокировка ДО открытия БД и миграций.
    lock = InstanceLock(path)
    if not lock.try_lock():
        return 2, ALREADY_OPEN_MESSAGE

    try:
        db = Database(path)
        db.connect()
        run_migrations(db, migrations_dir, db_backup_dir(db))
    except (MigrationError, BackupError, DatabaseError) as exc:
        lock.unlock()
        return 1, str(exc)
    except Exception as exc:  # noqa: BLE001
        lock.unlock()
        return 1, f"Непредвиденная ошибка запуска: {exc}"

    window = MainWindow(db)
    window.show()
    return 0, (app, window, lock)


def db_backup_dir(db: Database):
    from pathlib import Path

    raw = db.get_setting("backup_path")
    if raw:
        return Path(raw)
    return Path(db.path).parent / "backups"


# FR-08: отдельный код завершения второго экземпляра (тот же lock уже занят).
EXIT_ALREADY_RUNNING = 3


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    db_path = args[0] if args else None
    rc, payload = bootstrap(db_path)
    if rc != 0:
        _critical("Казначей", payload)
        # Второй экземпляр той же БД — отдельный exit code (FR-08),
        # завершается немедленно, без ожидания блокировки.
        return EXIT_ALREADY_RUNNING if rc == 2 else rc
    app, window, lock = payload
    try:
        code = app.exec()
    finally:
        # FR-07: штатное освобождение блокировки при выходе.
        lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
