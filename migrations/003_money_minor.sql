-- 003_money_minor.sql: перевод денежных значений на целые минимальные единицы (версия 3)
-- RUB: 1 minor = 1 копейка. 1 234,56 руб. = 123456 minor.
-- Старые REAL-колонки amount и monthly_limit удаляются вместе с пересозданием
-- таблиц, чтобы приложение не могло читать или писать в них (ТЗ Этап 1, запрет fallback).
-- id, связи, даты, категории, шаблоны, описания и временные метки сохраняются.

CREATE TABLE transactions_new (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    amount_minor INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    description TEXT,
    currency TEXT DEFAULT 'RUB',
    is_template INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NULL,
    FOREIGN KEY(category_id) REFERENCES categories(id)
);

INSERT INTO transactions_new
    (id, type, amount_minor, category_id, date, description, currency,
     is_template, created_at, updated_at)
SELECT
    id, type, CAST(ROUND(amount * 100) AS INTEGER), category_id, date,
    description, COALESCE(currency, 'RUB'), is_template, created_at, updated_at
FROM transactions;

DROP TABLE transactions;
ALTER TABLE transactions_new RENAME TO transactions;

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_transactions_is_template ON transactions(is_template);
-- индексы из миграции 002 теряются при DROP TABLE — восстанавливаем
CREATE INDEX IF NOT EXISTS idx_transactions_type_date ON transactions(type, date);

CREATE TABLE categories_new (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    monthly_limit_minor INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(name, type)
);

INSERT INTO categories_new
    (id, name, type, is_active, monthly_limit_minor, created_at)
SELECT
    id, name, type, is_active, CAST(ROUND(COALESCE(monthly_limit, 0) * 100) AS INTEGER), created_at
FROM categories;

DROP TABLE categories;
ALTER TABLE categories_new RENAME TO categories;

CREATE INDEX IF NOT EXISTS idx_categories_type_active ON categories(type, is_active);
