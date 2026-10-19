# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-спецификация «Казначей» (ТЗ, раздел 4): .exe без необходимости установки Python.
# Сборка:  pyinstaller kaznachei.spec
# FR-16 (Этап 1): миграции включаются в бандл — exe самодостаточен и не требует
# внешней папки migrations/. В frozen-режиме загрузчик читает SQL из sys._MEIPASS/migrations.

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ("migrations", "migrations"),
    ],
    hiddenimports=['PyQt6.QtTest'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest.test'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='kaznachei',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI-приложение: консольное окно не показываем
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
