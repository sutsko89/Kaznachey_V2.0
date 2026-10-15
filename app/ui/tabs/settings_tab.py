"""Вкладка «Настройки»: данные и отображение, резервные копии и откат.

Управление ПИН/паролем отсутствует (однопользовательская редакция).
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from app.domain.service import TreasuryService
from app.errors import BackupError, DatabaseError, RestoreError, ValidationError


class SettingsTab(QWidget):
    def __init__(self, service: TreasuryService, parent=None):
        super().__init__(parent)
        self.service = service
        self.restart_requested = False

        layout = QVBoxLayout(self)

        # ---------- Данные и отображение ----------
        data_group = QGroupBox("Данные и отображение")
        form = QFormLayout(data_group)
        layout.addWidget(data_group)

        self.base_currency_combo = QComboBox()
        self.base_currency_combo.setEditable(True)
        self.base_currency_combo.addItems(["RUB", "USD", "EUR"])
        form.addRow("Базовая валюта:", self.base_currency_combo)

        db_path_row = QHBoxLayout()
        self.db_path_label = QLineEdit(str(service.db.path))
        self.db_path_label.setReadOnly(True)
        db_path_row.addWidget(self.db_path_label)
        form.addRow("Путь к БД:", db_path_row)

        self.date_format_combo = QComboBox()
        self.date_format_combo.addItems(["DD.MM.YYYY", "YYYY-MM-DD"])
        form.addRow("Формат даты:", self.date_format_combo)

        self.number_format_combo = QComboBox()
        self.number_format_combo.addItems(["12 345,67 (русский)"])
        form.addRow("Формат чисел:", self.number_format_combo)

        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["100%", "125%", "150%"])
        form.addRow("Масштаб интерфейса (после перезапуска):", self.scale_combo)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Светлая", "Тёмная"])
        form.addRow("Тема интерфейса (после перезапуска):", self.theme_combo)

        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(["25", "50"])
        form.addRow("Строк на страницу:", self.page_size_combo)

        self.btn_save_settings = QPushButton("Сохранить настройки")
        self.btn_save_settings.clicked.connect(self._save_settings)
        layout.addWidget(self.btn_save_settings)

        # ---------- Резервные копии и откат ----------
        backup_group = QGroupBox("Резервные копии и откат")
        bform = QFormLayout(backup_group)
        layout.addWidget(backup_group)

        backup_dir_row = QHBoxLayout()
        self.backup_dir_edit = QLineEdit()
        self.backup_dir_edit.setPlaceholderText("Папка для бэкапов")
        self.btn_browse_backup_dir = QPushButton("Выбрать папку…")
        self.btn_browse_backup_dir.clicked.connect(self._browse_backup_dir)
        backup_dir_row.addWidget(self.backup_dir_edit)
        backup_dir_row.addWidget(self.btn_browse_backup_dir)
        bform.addRow("Папка для бэкапов:", backup_dir_row)

        btns = QHBoxLayout()
        self.btn_create_backup = QPushButton("Создать бэкап")
        self.btn_restore = QPushButton("Восстановить из выбранного бэкапа")
        self.btn_refresh_backups = QPushButton("Обновить список")
        btns.addWidget(self.btn_create_backup)
        btns.addWidget(self.btn_restore)
        btns.addWidget(self.btn_refresh_backups)
        bform.addRow(btns)

        self.backups_list = QListWidget()
        bform.addRow(self.backups_list)

        self.btn_create_backup.clicked.connect(self.create_backup)
        self.btn_restore.clicked.connect(self.restore_selected_backup)
        self.btn_refresh_backups.clicked.connect(self.refresh_backups_list)

        layout.addStretch(1)
        self.load_values()

    # ---------- значения ----------
    def load_values(self) -> None:
        s = self.service
        self.base_currency_combo.setCurrentText(s.get_setting("base_currency", "RUB"))
        idx = self.date_format_combo.findText(s.get_setting("date_format", "DD.MM.YYYY"))
        if idx >= 0:
            self.date_format_combo.setCurrentIndex(idx)
        scale = s.get_setting("ui_scale", "100")
        idx = self.scale_combo.findText(f"{scale}%")
        if idx >= 0:
            self.scale_combo.setCurrentIndex(idx)
        theme = s.get_setting("theme", "light")
        self.theme_combo.setCurrentIndex(1 if theme == "dark" else 0)
        ps = s.get_setting("page_size", "50")
        idx = self.page_size_combo.findText(ps)
        if idx >= 0:
            self.page_size_combo.setCurrentIndex(idx)
        self.backup_dir_edit.setText(s.get_setting("backup_path", "") or "")
        self.refresh_backups_list()

    def _save_settings(self) -> None:
        try:
            s = self.service
            s.set_setting("base_currency", self.base_currency_combo.currentText().strip().upper())
            s.set_setting("date_format", self.date_format_combo.currentText())
            s.set_setting("number_format", "ru")
            s.set_setting("ui_scale", self.scale_combo.currentText().rstrip("%"))
            s.set_setting("theme", "dark" if self.theme_combo.currentIndex() == 1 else "light")
            s.set_setting("page_size", self.page_size_combo.currentText())
            raw = self.backup_dir_edit.text().strip()
            s.set_setting("backup_path", str(Path(raw).expanduser()) if raw else "")
        except (ValidationError, DatabaseError) as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return
        old_scale = None  # масштаб/тема применяются после перезапуска
        self.restart_requested = True
        QMessageBox.information(
            self, "Казначей",
            "Настройки сохранены. Тема и масштаб будут применены после перезапуска приложения.",
        )

    # ---------- бэкапы ----------
    def backup_dir(self) -> Path:
        return self.service.backup_dir()

    def _browse_backup_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Папка для бэкапов", str(self.backup_dir()))
        if chosen:
            self.backup_dir_edit.setText(chosen)
            try:
                self.service.set_setting("backup_path", chosen)
            except (ValidationError, DatabaseError) as exc:
                QMessageBox.critical(self, "Казначей", str(exc))
            self.refresh_backups_list()

    def refresh_backups_list(self) -> None:
        from app.services.backup import list_backups

        self.backups_list.clear()
        for p in list_backups(self.backup_dir()):
            item = QListWidgetItem(p.name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            self.backups_list.addItem(item)

    def create_backup(self) -> bool:
        """Публичный метод: Ctrl+B вызывает его. Возвращает успех."""
        from app.services.backup import create_backup as do_backup
        from app.services.migrations import get_current_version

        try:
            version = get_current_version(self.service.db)
            dest = do_backup(self.service.db.path, self.backup_dir(), current_version=version)
        except BackupError as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return False
        except DatabaseError as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return False
        self.refresh_backups_list()
        QMessageBox.information(self, "Казначей", f"Резервная копия создана: {dest}")
        return True

    def restore_selected_backup(self) -> bool:
        """Откат только вручную и только с подтверждением. Возвращает успех."""
        item = self.backups_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Казначей", "Выберите бэкап из списка.")
            return False
        backup_path = Path(item.data(Qt.ItemDataRole.UserRole))
        answer = QMessageBox.question(
            self, "Подтверждение восстановления",
            "Вы уверены, что хотите заменить текущую БД выбранной резервной копией? "
            "Все изменения после даты бэкапа будут потеряны.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        from app.services.backup import restore_backup

        try:
            self.service.db.close()
            restore_backup(backup_path, self.service.db.path)
        except RestoreError as exc:
            QMessageBox.critical(self, "Казначей", str(exc))
            return False
        QMessageBox.information(
            self, "Казначей",
            "Восстановление выполнено. Приложение будет закрыто — запустите его снова.",
        )
        top = self.window()
        if hasattr(top, "quit_after_restore"):
            top.quit_after_restore()
        return True
