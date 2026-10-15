"""Резервное копирование и восстановление БД.

Имя файла бэкапа: kaznachei_backup_YYYYMMDD_HHMMSS_v<current_version>.db
Восстановление (откат): текущая БД переименовывается в kaznachei_old_<timestamp>.db,
выбранный бэкап копируется на место основной БД.
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

from app.errors import BackupError, RestoreError

BACKUP_PREFIX = "kaznachei_backup_"
BACKUP_RE = re.compile(r"^kaznachei_backup_(\d{8})_(\d{6})_v(\d+)\.db$")


def backup_filename(moment: dt.datetime | None = None, current_version: int = 0) -> str:
    moment = moment or dt.datetime.now()
    return f"{BACKUP_PREFIX}{moment:%Y%m%d_%H%M%S}_v{current_version}.db"


def create_backup(
    db_path: str | Path,
    backup_dir: str | Path,
    *,
    current_version: int = 0,
    moment: dt.datetime | None = None,
) -> Path:
    """Копирует файл БД в папку бэкапов. При неудаче — BackupError (миграция не выполняется)."""
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    if not db_path.is_file():
        raise BackupError(f"Файл базы данных не найден: {db_path}")
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        dest = _unique_path(backup_dir / backup_filename(moment, current_version))
        # консистентная копия через sqlite3.backup (файл может активно использоваться)
        import sqlite3

        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return dest
    except Exception as exc:  # OSError и любые ошибки записи
        raise BackupError(str(exc)) from exc


def _unique_path(path: Path) -> Path:
    """Если файл с таким именем уже есть — добавляет счётчик перед расширением."""
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def list_backups(backup_dir: str | Path) -> list[Path]:
    """Список файлов бэкапов, новые — первыми."""
    d = Path(backup_dir)
    if not d.is_dir():
        return []
    backups = [p for p in d.iterdir() if BACKUP_RE.match(p.name)]
    return sorted(backups, key=lambda p: p.stat().st_mtime, reverse=True)


def restore_backup(backup_file: str | Path, db_path: str | Path) -> Path:
    """Откат к резервной копии. Возвращает путь сохранённой старой БД
    (kaznachei_old_<timestamp>.db). После восстановления приложение должно быть закрыто.
    """
    backup_file = Path(backup_file)
    db_path = Path(db_path)
    if not backup_file.is_file():
        raise RestoreError(f"Резервная копия не найдена: {backup_file}")
    if not BACKUP_RE.match(backup_file.name):
        raise RestoreError(f"Файл не является резервной копией: {backup_file.name}")
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    old_path = db_path.with_name(f"kaznachei_old_{timestamp}.db")
    try:
        if db_path.exists():
            db_path.rename(old_path)
        shutil.copy2(backup_file, db_path)
        # убираем возможный журнал WAL у восстановленной копии
        wal = Path(str(db_path) + "-wal")
        if wal.exists():
            wal.unlink(missing_ok=True)
        return old_path if old_path.exists() else db_path
    except OSError as exc:
        # попытка вернуть старую БД на место
        if old_path.exists() and not db_path.exists():
            try:
                old_path.rename(db_path)
            except OSError:
                pass
        raise RestoreError(str(exc)) from exc
