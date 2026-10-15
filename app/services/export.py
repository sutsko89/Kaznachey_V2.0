"""Экспорт операций в CSV.

Кодировка UTF-8 с BOM, разделитель ';;' — Excel корректно открывает кириллицу.
Шаблоны не экспортируются; выгружаются все страницы выборки (с учётом текущих фильтров).
"""
from __future__ import annotations

from pathlib import Path

from app.dal.database import Database

CSV_HEADER = ["дата", "тип", "категория", "сумма", "валюта", "комментарий"]
SEPARATOR = ";;"


def export_transactions_csv(
    db: Database,
    dest: str | Path,
    *,
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    tx_type: str = "",
    category_id: int | None = None,
) -> int:
    """Экспортирует отфильтрованные операции (без шаблонов). Возвращает число строк."""
    dest = Path(dest)
    page_size = 1000
    offset = 0
    count = 0
    with dest.open("w", encoding="utf-8-sig", newline="") as f:
        # разделитель ';;': csv-модуль поддерживает только односимвольный delimiter,
        # поэтому строки форматируем вручную
        f.write(SEPARATOR.join(CSV_HEADER) + "\r\n")
        while True:
            rows, total = db.list_transactions(
                templates=False,
                search=search,
                date_from=date_from,
                date_to=date_to,
                tx_type=tx_type,
                category_id=category_id,
                order_by="date ASC, id ASC",
                limit=page_size,
                offset=offset,
            )
            for tr in rows:
                fields = [
                    tr.date,
                    "доход" if tr.type == "income" else "расход",
                    tr.category_name,
                    _fmt_amount(tr.amount),
                    tr.currency,
                    _escape(tr.description),
                ]
                f.write(SEPARATOR.join(fields) + "\r\n")
                count += 1
            offset += page_size
            if offset >= total or not rows:
                break
    return count


def _fmt_amount(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _escape(text: str) -> str:
    text = text or ""
    if '"' in text or ";" in text or "\n" in text:
        # внутри кавычек разделитель экранируем, чтобы не ломать разбор по ';;'
        return '"' + text.replace(';;', ';').replace('"', '""') + '"'
    return text.replace(";;", ";")
