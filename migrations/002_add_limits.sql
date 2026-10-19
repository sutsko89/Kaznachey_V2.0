-- 002_add_limits.sql: индексы для быстрого расчёта месячных лимитов (версия 2)
CREATE INDEX IF NOT EXISTS idx_transactions_type_date ON transactions(type, date);
CREATE INDEX IF NOT EXISTS idx_categories_type_active ON categories(type, is_active);
