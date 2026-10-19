"""Исключения предметной области приложения «Казначей»."""


class KaznacheiError(Exception):
    """Базовое исключение приложения."""


class ValidationError(KaznacheiError):
    """Ошибка валидации данных. errors — словарь поле -> сообщение."""

    def __init__(self, errors: dict[str, str], message: str = "Проверьте правильность заполнения полей"):
        super().__init__(message)
        self.errors = errors


class DatabaseError(KaznacheiError):
    """Ошибка базы данных. Изменения не сохранены."""

    USER_MESSAGE = (
        "Ошибка базы данных. Изменения не сохранены. "
        "Создайте резервную копию и обратитесь к разработчику."
    )

    def __init__(self, detail: str = ""):
        super().__init__(f"{self.USER_MESSAGE} {detail}".strip())
        self.detail = detail


class BackupError(KaznacheiError):
    """Ошибка создания резервной копии."""

    USER_MESSAGE = (
        "Не удалось создать резервную копию. Миграция не будет выполнена. "
        "Укажите другую папку для бэкапов."
    )

    def __init__(self, detail: str = ""):
        super().__init__(f"{self.USER_MESSAGE} {detail}".strip())
        self.detail = detail


class MigrationError(KaznacheiError):
    """Ошибка применения миграции схемы БД."""


class CategoryInUseError(KaznacheiError):
    """Категорию с операциями нельзя физически удалить."""

    USER_MESSAGE = (
        "В категории есть операции. Физическое удаление запрещено. "
        "Вы можете деактивировать категорию."
    )

    def __init__(self):
        super().__init__(self.USER_MESSAGE)


class RestoreError(KaznacheiError):
    """Ошибка восстановления из резервной копии."""
