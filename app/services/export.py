"""Экспорт операций в CSV.

Кодировка UTF-8 с BOM, стандартный односимвольный разделитель ';' и модуль
csv с корректным quoting. Экспорт принимает тот же TransactionFilter, что и
таблица/сводка; шаблоны не экспортируются; сумма формируется из amount_minor
без промежуточного float. Колонка «валюта» удалена (одновалютный RUB-режим).
"""
from __future__ import annotations

import csv
from pathlib import Path

from app.dal.database import Database, TransactionFilter
from app.domain.service import format_amount_ru

CSV_HEADER = ["дата", "тип", "категория", "сумма", "комментарий"]
SEPARATOR = ";"


def export_transactions_csv(db: Database, dest: str | Path,
                            filter: TransactionFilter | None = None,
                            **filter_kwargs) -> int:
    """Экспортирует отфильтрованные операции (без шаблонов). Возвращает число строк.

    Принимает тот же TransactionFilter, что и таблица/сводка (FR-01);
    kwargs — удобный конструктор фильтра для тестов и вызывающего кода.
    """
    if filter is None:
        filter = TransactionFilter(**filter_kwargs)
    if filter.templates:
        filter = TransactionFilter(
            search=filter.search, date_from=filter.date_from,
            date_to=filter.date_to, tx_type=filter.tx_type,
            category_id=filter.category_id, templates=False)
    dest = Path(dest)
    page_size = 1000
    offset = 0
    count = 0
    with dest.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=SEPARATOR, quoting=csv.QUOTE_MINIMAL,
                            lineterminator="\r\n")
        writer.writerow(CSV_HEADER)
        while True:
            rows, total = db.list_transactions(
                filter, order_by="date ASC, id ASC",
                limit=page_size, offset=offset)
            for tr in rows:
                writer.writerow([
                    tr.date,
                    "доход" if tr.type == "income" else "расход",
                    tr.category_name,
                    # человекочитаемая сумма без float: '1 234,56'
                    format_amount_ru(tr.amount_minor),
                    tr.description or "",
                ])
                count += 1
            offset += page_size
            if offset >= total or not rows:
                break
    return count
