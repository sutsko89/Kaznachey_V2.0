"""Subprocess-тесты single-instance блокировки (FR-07/FR-08, раздел 8 ТЗ).

Проверяются реальные процессы:
- первый процесс удерживает lock;
- второй с той же БД завершается максимум за ~2 секунды с отдельным exit code;
- второй с другой БД запускается нормально;
- после штатного завершения первого lock освобождается и третья копия стартует;
- после аварийного завершения владельца (kill -9) новая копия тоже стартует
  (QLockFile перезаписывает stale-lock по мёртвому PID).

Для неGUI-замеров используется app.services.instance_lock напрямую; полный
стек «lock → миграции → окно» проверяется через app.main.bootstrap в
subprocess-скриптах.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

EXIT_ALREADY_RUNNING = 3  # FR-08: отдельный код второго экземпляра

# Скрипт-агент: держит InstanceLock указанное число секунд и печатает статус.
AGENT_KEEP = """
import sys, time
sys.path.insert(0, {root!r})
from app.services.instance_lock import InstanceLock
lock = InstanceLock(sys.argv[1])
if not lock.try_lock():
    print("FAIL-LOCK", flush=True)
    sys.exit(2)
print("LOCKED", flush=True)
time.sleep(int(sys.argv[2]))
lock.unlock()
print("UNLOCKED", flush=True)
sys.exit(0)
"""

# Скрипт-агент: пытается получить lock и немедленно выходит (без ожидания).
AGENT_TRY = """
import sys
sys.path.insert(0, {root!r})
from app.services.instance_lock import InstanceLock
lock = InstanceLock(sys.argv[1])
ok = lock.try_lock()
print("LOCKED" if ok else "BUSY", flush=True)
sys.exit(0 if ok else {exit_busy})
"""


def _run_agent(code: str, args: list[str], timeout: float = 15):
    script = code.format(root=str(ROOT), exit_busy=EXIT_ALREADY_RUNNING)
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )


@pytest.fixture()
def db_a(tmp_path):
    return str(tmp_path / "a" / "kaznachei.db")


@pytest.fixture()
def db_b(tmp_path):
    return str(tmp_path / "b" / "kaznachei.db")


class TestSecondInstanceRefusesFast:
    def test_second_process_with_same_db_exits_fast_and_distinct_code(self, db_a):
        """Второй процесс: НЕ ждёт бесконечно, а за <=2 сек возвращает exit code 3."""
        keeper = subprocess.Popen(
            [sys.executable, "-c", AGENT_KEEP.format(root=str(ROOT)), db_a, "30"],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            # ждём, пока первый реально получит lock
            line = keeper.stdout.readline()
            assert line.strip() == "LOCKED"

            start = time.monotonic()
            res = _run_agent(AGENT_TRY, [db_a], timeout=5)
            elapsed = time.monotonic() - start

            assert res.stdout.strip() == "BUSY"
            assert res.returncode == EXIT_ALREADY_RUNNING
            # Ключевая регрессия на tryLock(-1): раньше второй процесс висел бы
            # до освобождения блокировки (30 с). Теперь — мгновенный отказ.
            assert elapsed < 2.0, (
                f"второй экземпляр ждал {elapsed:.1f} с вместо немедленного отказа")

            # первая копия продолжает работать
            assert keeper.poll() is None
        finally:
            keeper.terminate()
            keeper.wait(timeout=5)

    def test_second_process_with_other_db_starts_fine(self, db_a, db_b):
        keeper = subprocess.Popen(
            [sys.executable, "-c", AGENT_KEEP.format(root=str(ROOT)), db_a, "30"],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            assert keeper.stdout.readline().strip() == "LOCKED"
            res = _run_agent(AGENT_TRY, [db_b], timeout=5)
            assert res.stdout.strip() == "LOCKED"
            assert res.returncode == 0
            assert keeper.poll() is None  # владелец db_a не пострадал
        finally:
            keeper.terminate()
            keeper.wait(timeout=5)


class TestGracefulReleaseAndStaleRecovery:
    def test_after_graceful_exit_third_copy_gets_lock(self, db_a):
        """После штатного завершения первой копии третья успешно получает lock."""
        res1 = _run_agent(AGENT_KEEP, [db_a, "0"], timeout=10)
        assert "LOCKED" in res1.stdout and "UNLOCKED" in res1.stdout
        assert res1.returncode == 0

        res2 = _run_agent(AGENT_TRY, [db_a], timeout=5)
        assert res2.stdout.strip() == "LOCKED"
        assert res2.returncode == 0

    def test_after_crash_new_copy_can_acquire_lock(self, db_a):
        """После SIGKILL владельца QLockFile считает lock устаревшим (мёртвый PID)."""
        killer = subprocess.Popen(
            [sys.executable, "-c", AGENT_KEEP.format(root=str(ROOT)), db_a, "60"],
            stdout=subprocess.PIPE, text=True,
        )
        assert killer.stdout.readline().strip() == "LOCKED"
        killer.kill()
        killer.wait(timeout=5)
        # lock-файл остался на диске, но PID внутри него мёртв
        from app.services.instance_lock import db_lock_key, lock_dir
        lock_file = lock_dir() / f"{db_lock_key(db_a)}.lock"
        assert lock_file.exists()

        res = _run_agent(AGENT_TRY, [db_a], timeout=5)
        assert res.stdout.strip() == "LOCKED"
        assert res.returncode == 0


class TestBootstrapIntegration:
    """Полный путь запуска: bootstrap возвращает rc=2 + сообщение FR-08 для
    второго экземпляра и НЕ трогает файл БД."""

    BOOTSTRAP_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
from PyQt6.QtWidgets import QApplication
app = QApplication([])  # offscreen-сессия Qt
from app.main import bootstrap
holder = None
mode = sys.argv[1]
path = sys.argv[2]
if mode == "hold":
    rc, payload = bootstrap(path)
    assert rc == 0, payload
    holder = payload  # держим app/window/lock
    print("BOOTSTRAP_OK", flush=True)
    import time; time.sleep(float(sys.argv[3]))
    payload[2].unlock()
    sys.exit(0)
else:
    rc, payload = bootstrap(path)
    print(f"RC={{rc}} MSG={{payload}}", flush=True)
    sys.exit(0)
"""

    def _boot(self, args, timeout=25):
        script = self.BOOTSTRAP_SCRIPT.format(root=str(ROOT))
        return subprocess.run(
            [sys.executable, "-c", script, *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )

    def test_bootstrap_blocks_second_instance_without_touching_db(self, tmp_path):
        db = str(tmp_path / "kaznachei.db")
        holder = subprocess.Popen(
            [sys.executable, "-c", self.BOOTSTRAP_SCRIPT.format(root=str(ROOT)),
             "hold", db, "20"],
            stdout=subprocess.PIPE, text=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        try:
            assert holder.stdout.readline().strip() == "BOOTSTRAP_OK"
            db_file = Path(db)
            mtime = db_file.stat().st_mtime_ns if db_file.exists() else None

            start = time.monotonic()
            second = self._boot(["second", db], timeout=15)
            assert time.monotonic() - start < 5
            assert "RC=2" in second.stdout
            assert "Эта база уже открыта в другом экземпляре" in second.stdout

            # второй процесс не изменил файл БД и не запустил миграции повторно
            if db_file.exists():
                assert db_file.stat().st_mtime_ns == mtime
            assert holder.poll() is None
        finally:
            holder.terminate()
            holder.wait(timeout=5)
