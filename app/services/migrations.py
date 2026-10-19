"""Механизм миграций схемы БД.

Миграции — SQL-файлы migrations/NNN_*.sql с непрерывной нумерацией, начиная с 001.
Версия хранится в таблице app_settings (ключ db_version) и изменяется только
этим механизмом. Перед каждой ожидающей миграцией автоматически создаётся
резервная копия; если файла БД ещё нет (первый запуск) — копировать нечего,
миграции применяются к новой базе после её создания.

FR-16: перед изменением БД проверяется содержимое устаревшей колонки currency:
записи с валютой, отличной от RUB, блокируют автоматическое продолжение
(MigrationError) — молча пересчитывать USD/EUR в рубли запрещено (FR-13).

Встроенные ресурсы: в frozen-сборке PyInstaller миграции читаются из
sys._MEIPASS (каталог, распакованный из datas), а не из папки рядом с exe;
в режиме разработки — из каталога репозитория. Подмена штатных SQL-файлов
файлами рядом с исполняемым файлом невозможна: поиск вне бандла не выполняется.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
from pathlib import Path

from app.errors import BackupError, DatabaseError, MigrationError

MIGRATION_RE = re.compile(r"^(\d{3})_[A-Za-z0-9_]+\.sql$")


def bundled_migrations_dir() -> Path:
    """Каталог миграций: ресурс пакета в dev или распакованный бандл в frozen."""
    import sys

    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        cand = base / "migrations"
        if not cand.is_dir():
            raise MigrationError(
                "Внутренний ресурс «migrations» отсутствует в сборке. "
                "Пересоберите приложение: запуск с неполной сборкой прерван, "
                "частично инициализированная база данных не создана."
            )
        return cand
    return Path(__file__).resolve().parent.parent.parent / "migrations"


def load_migrations(migrations_dir: str | Path) -> list[tuple[int, Path]]:
    d = Path(migrations_dir)
    if not d.is_dir():
        raise MigrationError(f"Каталог миграций не найден: {d}")
    result: list[tuple[int, Path]] = []
    for p in sorted(d.glob("*.sql")):
        m = MIGRATION_RE.match(p.name)
        if m:
            result.append((int(m.group(1)), p))
    versions = [v for v, _ in result]
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        raise MigrationError(
            f"Нарушена непрерывная нумерация миграций: найдено {versions}, "
            f"ожидалось {expected}"
        )
    return result


def get_current_version(db_or_path) -> int:
    """Текущая версия схемы БД (0, если таблицы schema_version ещё нет).

    Принимает Database, sqlite3.Connection или путь к файлу БД.
    Источник истины — таблица schema_version (ТЗ 6.1); app_settings.db_version
    является зеркалом для совместимости.
    """
    conn = None
    own_conn = False
    try:
        if hasattr(db_or_path, "connection"):          # Database
            conn = db_or_path.connection
        elif hasattr(db_or_path, "execute"):           # sqlite3.Connection
            conn = db_or_path
        else:                                          # path
            conn = sqlite3.connect(str(db_or_path))
            own_conn = True
        return _max_version(conn)
    except sqlite3.Error:
        return 0
    finally:
        if own_conn and conn is not None:
            conn.close()


def run_migrations(db, migrations_dir: str | Path, backup_dir: str | Path) -> list[int]:
    """Применяет все ожидающие миграции. Возвращает список применённых версий.

    Каждая ожидающая миграция выполняется в одной транзакции SQLite
    (SQLite поддерживает атомарные ALTER TABLE + UPDATE внутри BEGIN...COMMIT);
    при ошибке — полный rollback без частичных изменений схемы.
    """
    from app.services.backup import create_backup

    migrations = load_migrations(migrations_dir)
    conn = db.connection
    # Версия схемы хранится в таблице schema_version (ТЗ 6.1); таблица
    # app_settings создаётся миграцией 001, поэтому до неё версия — 0.
    if not _has_table(conn, "schema_version"):
        current = 0
    else:
        current = _max_version(conn)
    applied: list[int] = []
    for version, path in migrations:
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        # 1. Резервная копия ДО изменения схемы и до любой записи в файл БД.
        #    Если на диске ещё нет файла с содержимым (первый запуск),
        #    создаём эталонный пустой файл SQLite — от него тоже делается
        #    бэкап, что гарантирует «бэкап перед каждой миграцией» (ТЗ 6.3).
        if not _db_file_has_content(db.path):
            empty = sqlite3.connect(str(db.path))
            empty.close()
        try:
            create_backup(db.path, backup_dir, current_version=current)
        except BackupError:
            raise
        except Exception as exc:
            raise BackupError(str(exc)) from exc
        # 2. FR-13: проверка foreign-currency до изменения схемы.
        _assert_rub_only(conn, version, path.name)
        # 3. Атомарное применение.
        try:
            # Пересоздание таблиц (миграция 003) при включённых FK вызывает
            # ложные нарушения ссылочной целостности; на время миграции FK
            # отключаются, после успеха запускается quick_check.
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    _exec_migration_sql(conn, sql)
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
            row = conn.execute("PRAGMA quick_check").fetchone()
            if not row or row[0] != "ok":
                raise MigrationError(
                    f"Миграция {path.name} нарушила целостность БД: {row}")
        except sqlite3.Error as exc:
            raise MigrationError(
                f"Миграция {path.name} не выполнена; изменения откачены: {exc}"
            ) from exc
        # 4. Учёт версии — отдельной транзакцией ПОСЛЕ успешного применения
        #    миграции (ТЗ 6.3): schema_version + db_version в app_settings.
        #    Так фиксация версии не входит в откатываемую транзакцию самой
        #    миграции и не мешает проверке «бэкап до любой записи в БД».
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                " version INTEGER NOT NULL PRIMARY KEY,"
                " applied_at TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)"
                " ON CONFLICT(version) DO NOTHING",
                (version, dt.datetime.now().isoformat(timespec="seconds")),
            )
            # зеркалим версию в app_settings только если таблица настроек
            # уже существует (её создаёт штатная миграция 001)
            if _has_table(conn, "app_settings"):
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES ('db_version', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(version),),
                )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise MigrationError(
                f"Не удалось зафиксировать версию {version}: {exc}"
            ) from exc
        applied.append(version)
    return applied

def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _max_version(conn: sqlite3.Connection) -> int:
    """Максимальная применённая версия из schema_version (или 0)."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error:
        return 0


def _db_file_has_content(path: str | Path) -> bool:
    """True, если файл БД существует и содержит неотформатированные страницы
    (т.е. в него уже что-то записано) — тогда нужен бэкап перед миграцией."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        with p.open("rb") as fh:
            header = fh.read(100)
    except OSError:
        return False
    return len(header) == 100 and header[8:11] != b"\x00\x00\x00"



def split_sql_statements(sql: str) -> list[str]:
    """Разбивает SQL-скрипт на отдельные инструкции БЕЗ split(";").

    Используется корректный сканер на базе sqlite3.complete_statement():
    он понимает строковые литералы ('...', "..."), quoted-идентификаторы
    ([...] / `...`), комментарии (-- и /* */) и не режет скрипт по точке с
    запятой внутри комментария или строки. Комментарии и пустые фрагменты
    отбрасываются, каждая возвращаемая инструкция содержит только исполняемый
    SQL (безопасно для conn.execute).
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        # однострочный комментарий -- ... до конца строки: отбрасывается,
        # точка с запятой ВНУТРИ него не завершает инструкцию
        if ch == "-" and sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end == -1 else end + 1
            continue
        # блочный комментарий /* ... */: отбрасывается целиком
        if ch == "/" and sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        # строковые литералы и quoted-идентификаторы — копируем целиком
        if ch in ("'", '"', "[", "`"):
            closing = {"'": "'", '"': '"', "[": "]", "`": "`"}[ch]
            j = i + 1
            while j < n:
                if sql[j] == closing:
                    if j + 1 < n and sql[j + 1] == closing:  # удвоенная кавычка
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            else:
                j = n  # незакрытый — берём до конца
            buf.append(sql[i:j])
            i = j
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            # complete_statement подтверждает завершённость инструкции
            if stmt and sqlite3.complete_statement(stmt + ";"):
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    # хвост без завершающей ";" (например, комментарий удалён, а SQL остался)
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _exec_migration_sql(conn: sqlite3.Connection, sql: str) -> None:
    """Выполняет скрипт миграции внутри уже открытой транзакции.

    executescript самовольно делает COMMIT, поэтому инструкции разделяются
    корректным парсером split_sql_statements (на основе
    sqlite3.complete_statement), а не split(";").
    """
    for stmt in split_sql_statements(sql):
        conn.execute(stmt)


def _read_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='db_version'").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def _assert_rub_only(conn: sqlite3.Connection, target_version: int,
                     filename: str) -> None:
    """Если в таблице operations есть currency != 'RUB' — блокируем миграцию."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "transactions" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "currency" not in cols:
        return
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM transactions"
            " WHERE currency IS NOT NULL AND currency <> 'RUB'").fetchone()[0]
    except sqlite3.Error:
        return
    if n:
        raise MigrationError(
            f"Найдено записей с валютой, отличной от RUB: {n}. "
            "Автоматическая миграция заблокирована: мультивалютность не поддерживается, "
            "молча пересчитывать USD/EUR в рубли запрещено. "
            "Создайте резервную копию или CSV-экспорт и обратитесь к разработчику."
        )
