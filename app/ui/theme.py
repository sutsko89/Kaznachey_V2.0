"""Светлая тема «Казначей»: единый лаконичный современный стиль.

Тёмная тема удалена по требованию продукта: приложение всегда работает
в светлой теме. Здесь собраны палитра, глобальный QSS и константы цветов
для собственной отрисовки (диаграмма дашборда, акценты таблиц).
"""
from __future__ import annotations

# ---------- палитра (светлая) ----------
BG = "#f6f7f9"          # фон окна
CARD_BG = "#ffffff"     # фон карточек/таблиц/полей
BORDER = "#e3e6ea"      # тонкие границы
TEXT = "#1f2430"        # основной текст
MUTED = "#6b7280"       # второстепенный текст
ACCENT = "#2f6fed"      # акцент (выделение, ссылка)
ACCENT_HOVER = "#2559c9"
ACCENT_TEXT = "#ffffff"
DANGER = "#d93025"      # красные ошибки
ROW_ALT = "#fafbfc"     # чередование строк
SELECTION = "#dbe7ff"   # выделенная строка таблицы

INCOME_COLOR = "#0f8a34"    # читаемый зелёный на белом фоне
EXPENSE_COLOR = "#c62828"   # читаемый красный на белом фоне

# совместимость с существующим кодом/тестами (светлая — единственная тема)
INCOME_COLOR_LIGHT = INCOME_COLOR
EXPENSE_COLOR_LIGHT = EXPENSE_COLOR
INCOME_COLOR_DARK = INCOME_COLOR
EXPENSE_COLOR_DARK = EXPENSE_COLOR


def light_qss() -> str:
    """Глобальный stylesheet: применяется к QApplication один раз на старте."""
    return f"""
    * {{ outline: none; }}

    QWidget {{
        background-color: {BG};
        color: {TEXT};
        font-family: "Segoe UI", "Inter", Arial, sans-serif;
        font-size: 14px;
    }}

    QMainWindow, QDialog {{ background-color: {BG}; }}

    /* ---------- верхняя панель ---------- */
    QLabel#appTitle {{
        font-size: 20px;
        font-weight: 600;
        color: {TEXT};
        background: transparent;
        padding: 2px 6px;
    }}
    QLabel#appSubtitle {{
        font-size: 12px;
        color: {MUTED};
        background: transparent;
        padding: 0 6px;
    }}

    /* ---------- вкладки ---------- */
    QTabWidget::pane {{
        border: 1px solid {BORDER};
        border-radius: 10px;
        background: {CARD_BG};
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {MUTED};
        padding: 9px 18px;
        margin-right: 2px;
        border: none;
        border-bottom: 2px solid transparent;
        font-size: 14px;
    }}
    QTabBar::tab:hover {{ color: {TEXT}; }}
    QTabBar::tab:selected {{
        color: {ACCENT};
        font-weight: 600;
        border-bottom: 2px solid {ACCENT};
        background: transparent;
    }}

    /* ---------- кнопки ---------- */
    QPushButton {{
        background: {CARD_BG};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 7px 14px;
        font-size: 14px;
    }}
    QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
    QPushButton:pressed {{ background: {SELECTION}; }}
    QPushButton:disabled {{ color: #b6bcc6; border-color: {BORDER}; background: {BG}; }}
    QPushButton:focus {{ border-color: {ACCENT}; }}

    QPushButton#primaryButton {{
        background: {ACCENT};
        color: {ACCENT_TEXT};
        border: none;
        font-weight: 600;
        padding: 8px 18px;
    }}
    QPushButton#primaryButton:hover {{ background: {ACCENT_HOVER}; }}
    QPushButton#primaryButton:pressed {{ background: {ACCENT_HOVER}; }}
    QPushButton#primaryButton:disabled {{ background: #b9c8ef; color: #eef2fb; }}

    QPushButton#dangerButton {{ color: {DANGER}; border-color: #f2c7c2; }}
    QPushButton#dangerButton:hover {{ background: #fdecea; border-color: {DANGER}; }}

    QPushButton#exitButton {{
        color: {MUTED}; border: 1px solid {BORDER}; border-radius: 8px;
        padding: 6px 16px; background: transparent;
    }}
    QPushButton#exitButton:hover {{ color: {DANGER}; border-color: {DANGER}; }}

    /* ---------- поля ввода ---------- */
    QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox, QSpinBox {{
        background: {CARD_BG};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 6px 10px;
        selection-background-color: {ACCENT};
        selection-color: {ACCENT_TEXT};
    }}
    QLineEdit:focus, QComboBox:focus, QDateEdit:focus,
    QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
    QLineEdit[error="true"] {{ border: 1px solid {DANGER}; }}
    QLineEdit:read-only {{ color: {MUTED}; background: {BG}; }}

    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {MUTED};
        margin-right: 8px;
    }}
    QComboBox QAbstractItemView {{
        background: {CARD_BG};
        border: 1px solid {BORDER};
        border-radius: 8px;
        selection-background-color: {SELECTION};
        selection-color: {TEXT};
        outline: none;
        padding: 4px;
    }}

    QCheckBox {{ spacing: 8px; background: transparent; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid {BORDER}; background: {CARD_BG};
    }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

    /* ---------- таблицы ---------- */
    QTableWidget, QTableView {{
        background: {CARD_BG};
        alternate-background-color: {ROW_ALT};
        border: 1px solid {BORDER};
        border-radius: 10px;
        gridline-color: transparent;
        selection-background-color: {SELECTION};
        selection-color: {TEXT};
    }}
    QTableWidget::item {{ padding: 5px 8px; border: none; }}
    QTableWidget::item:selected {{ background: {SELECTION}; color: {TEXT}; }}
    QHeaderView::section {{
        background: {BG};
        color: {MUTED};
        font-weight: 600;
        font-size: 12px;
        border: none;
        border-bottom: 1px solid {BORDER};
        padding: 7px 8px;
    }}
    QTableCornerButton::section {{ background: {BG}; border: none; }}

    /* ---------- списки ---------- */
    QListWidget {{
        background: {CARD_BG};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 4px;
        selection-background-color: {SELECTION};
        selection-color: {TEXT};
    }}
    QListWidget::item {{ padding: 7px 10px; border-radius: 6px; }}
    QListWidget::item:selected {{ background: {SELECTION}; color: TEXT; }}

    /* ---------- группы ---------- */
    QGroupBox {{
        background: {CARD_BG};
        border: 1px solid {BORDER};
        border-radius: 10px;
        margin-top: 12px;
        padding: 10px 12px 12px 12px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: {MUTED};
        background: {BG};
    }}

    /* ---------- прогресс-бары ---------- */
    QProgressBar {{
        background: #edf0f3;
        border: none;
        border-radius: 6px;
        height: 12px;
        text-align: center;
        color: {TEXT};
    }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}

    /* ---------- прочее ---------- */
    QLabel[secondary="true"] {{ color: {MUTED}; font-size: 12px; background: transparent; }}
    QToolTip {{
        background: {TEXT}; color: {CARD_BG}; border: none;
        padding: 6px 8px; border-radius: 6px;
    }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: #d3d8df; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: #d3d8df; border-radius: 5px; min-width: 30px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QMessageBox {{ background: {BG}; }}
    """


def apply_light_theme(app) -> None:
    """Применяет светлую тему ко всему приложению (один раз на старте)."""
    app.setStyle("Fusion")
    app.setStyleSheet(light_qss())
