-- 003_money_minor.sql: перевод денежных значений на целые минимальные единицы (версия 3)
-- RUB: 1 minor = 1 копейка; 1 234,56 руб. = 123456 minor.
-- Старые колонки amount REAL / monthly_limit REAL остаются как deprecated-наследие
-- (физическое удаление — отдельной будущей миграцией); приложение после этой
-- миграции их не читает и в них не пишет. id, связи, даты, категории, шаблоны,
-- описания и временные метки не изменяются.

ALTER TABLE transactions ADD COLUMN amount_minor INTEGER NOT NULL DEFAULT 0;
UPDATE transactions SET amount_minor = CAST(ROUND(amount * 100) AS INTEGER);

ALTER TABLE categories ADD COLUMN monthly_limit_minor INTEGER NOT NULL DEFAULT 0;
UPDATE categories SET monthly_limit_minor = CAST(ROUND(monthly_limit * 100) AS INTEGER);
