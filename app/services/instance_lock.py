"""Межпроцессная блокировка единственного экземпляра (FR-07/FR-08).

Блокировка привязана к каноническому абсолютному пути файла БД:
SHA-256 от str(Path(path).resolve()) задаёт имя lock-файла в пользовательском
каталоге приложения (QStandardPaths.AppLocalDataLocation). Разные базы можно
открывать одновременно, одну и ту же — нет.

QLockFile удерживает блокировку всё время жизни объекта (нельзя хранить lock
только в локальной переменной — он будет удалён при сборке мусора), штатно
освобождает его при выходе и умеет перезаписывать устаревшие lock-файлы после
аварийного завершения процесса-владельца.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PyQt6.QtCore import QLockFile, QStandardPaths

ALREADY_OPEN_MESSAGE = "Эта база уже открыта в другом экземпляре «Казначея»."

# Период, по истечении которого lock считается устаревшим, если PID владельца
# не существует (механизм QLockFile); 0 = доверять проверке PID.
STALE_AFTER_MS = 0


def db_lock_key(db_path: str | Path) -> str:
    """SHA-256 канонического абсолютного пути к БД."""
    canonical = str(Path(db_path).resolve())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lock_dir(create: bool = True) -> Path:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation)
    if not base:
        base = str(Path.home() / ".local" / "share" / "Kaznachei")
    d = Path(base) / "locks"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


class InstanceLock:
    """Постоянная блокировка одной БД на один процесс.

    Использовать так:
        lock = InstanceLock(db_path)
        if not lock.try_lock(): ...          # второй экземпляр — отказ
        ...                                   # держать объект до выхода из main()
        lock.unlock()                         # штатное освобождение при выходе
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.key = db_lock_key(self.db_path)
        self._file = None

    def try_lock(self) -> bool:
        path = lock_dir() / f"{self.key}.lock"
        qlock = QLockFile(str(path))
        qlock.setStaleLockTime(STALE_AFTER_MS)
        # tryLock(-1): попытка однократная; устаревший lock (мёртвый PID)
        # QLockFile удаляет сам.
        if qlock.tryLock(-1):
            self._file = qlock
            return True
        qlock.close() if hasattr(qlock, "close") else None
        return False

    def is_locked_by_me(self) -> bool:
        return self._file is not None and self._file.isLocked()

    def unlock(self) -> None:
        if self._file is not None:
            self._file.unlock()
            self._file = None
