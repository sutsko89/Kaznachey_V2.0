-- 001_init.sql: базовая схема «Казначей» (версия 1)
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    monthly_limit REAL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(name, type)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    amount REAL NOT NULL,
    category_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    description TEXT,
    currency TEXT DEFAULT 'RUB',
    is_template INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NULL,
    FOREIGN KEY(category_id) REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_transactions_is_template ON transactions(is_template);

INSERT OR IGNORE INTO app_settings (key, value) VALUES ('base_currency', 'RUB');
INSERT OR IGNORE INTO app_settings (key, value) VALUES ('date_format', 'DD.MM.YYYY');
INSERT OR IGNORE INTO app_settings (key, value) VALUES ('number_format', 'ru');
INSERT OR IGNORE INTO app_settings (key, value) VALUES ('ui_scale', '100');
INSERT OR IGNORE INTO app_settings (key, value) VALUES ('theme', 'light');
INSERT OR IGNORE INTO app_settings (key, value) VALUES ('page_size', '50');
