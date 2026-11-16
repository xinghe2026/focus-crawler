"""界面主题：科技硬朗 / 终端风（深黑底 + 荧光绿 + 等宽字体）。

设计 token 来自美术模型产出的主视觉（见 docs/art/），落地为纯 QSS + 字体，
不依赖位图资源，任何 DPI 下都清晰；启动画面另有自绘的数字雨。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# ---- 设计 token -------------------------------------------------
BG = "#040705"          # 主背景（近黑）
BG_ALT = "#070D09"      # 次级背景
PANEL = "#0A130C"       # 面板
PANEL_HI = "#0E1A11"    # 面板高亮 / 悬停
BORDER = "#1B3323"      # 常规边框
BORDER_HI = "#2E6B3E"   # 悬停边框
ACCENT = "#00FF41"      # 主色：荧光绿
ACCENT_DIM = "#12B33B"  # 主色暗部
TEXT = "#CFF7DA"        # 主文本
TEXT_DIM = "#6E8F78"    # 次要文本
DANGER = "#FF4D4D"
WARN = "#FFC24D"

MONO_FAMILIES = (
    "Cascadia Mono",
    "Cascadia Code",
    "JetBrains Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
)

STYLESHEET = f"""
* {{
    outline: 0;
}}
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
    font-size: 12px;
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}
QMainWindow, QDialog {{
    background-color: {BG};
}}
QWidget#contentRoot, QWidget#matrixRain {{
    background: transparent;
}}
QToolTip {{
    background-color: {PANEL};
    color: {ACCENT};
    border: 1px solid {ACCENT_DIM};
    padding: 4px 6px;
}}

/* ---------- 分组 / 面板 ---------- */
QGroupBox {{
    background-color: rgba(10, 19, 12, 215);
    border: 1px solid {BORDER};
    margin-top: 14px;
    padding: 10px 8px 8px 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 6px;
    color: {ACCENT};
    font-weight: 700;
    letter-spacing: 1px;
}}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    border: 1px solid {BORDER};
}}
QScrollArea {{
    border: 1px solid {BORDER};
    background-color: {BG_ALT};
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background-color: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER_HI};
    padding: 5px 14px;
    min-height: 20px;
    letter-spacing: 1px;
}}
QPushButton:hover {{
    background-color: {PANEL_HI};
    border-color: {ACCENT};
    color: {ACCENT};
}}
QPushButton:pressed {{
    background-color: {ACCENT};
    color: {BG};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    border-color: {BORDER};
    background-color: {BG_ALT};
}}
QPushButton:default {{
    border-color: {ACCENT};
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: rgba(7, 13, 9, 225);
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 6px;
    selection-background-color: {ACCENT_DIM};
    selection-color: {BG};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QPlainTextEdit:hover, QTextEdit:hover {{
    border-color: {BORDER_HI};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
    color: {ACCENT};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {TEXT_DIM};
    background-color: {BG};
}}
QComboBox::drop-down {{
    border-left: 1px solid {BORDER};
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {PANEL};
    color: {TEXT};
    border: 1px solid {ACCENT_DIM};
    selection-background-color: {ACCENT_DIM};
    selection-color: {BG};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {PANEL};
    border-left: 1px solid {BORDER};
    width: 14px;
}}

/* ---------- 复选框 ---------- */
QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {TEXT};
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 13px;
    height: 13px;
    border: 1px solid {BORDER_HI};
    background-color: {BG_ALT};
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
}}
QCheckBox::indicator:disabled {{
    border-color: {BORDER};
    background-color: {BG};
}}

/* ---------- 标签 / 链接 ---------- */
QLabel {{
    background: transparent;
    color: {TEXT};
}}
QLabel[role="hint"] {{
    color: {TEXT_DIM};
}}
QLabel[role="accent"] {{
    color: {ACCENT};
}}
QLabel[role="danger"] {{
    color: {DANGER};
}}
QLabel[role="warn"] {{
    color: {WARN};
}}

/* ---------- 表格 ---------- */
QTableWidget, QTableView, QListWidget, QTreeWidget {{
    background-color: rgba(7, 13, 9, 205);
    alternate-background-color: {BG};
    color: {TEXT};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    selection-background-color: {PANEL_HI};
    selection-color: {ACCENT};
}}
QTableWidget::item, QListWidget::item {{
    padding: 3px 4px;
}}
QTableWidget::item:selected, QListWidget::item:selected {{
    background-color: {PANEL_HI};
    color: {ACCENT};
    border-left: 2px solid {ACCENT};
}}
QHeaderView::section {{
    background-color: {PANEL};
    color: {ACCENT};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {ACCENT_DIM};
    padding: 5px 6px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QHeaderView::section:hover {{
    background-color: {PANEL_HI};
}}
QTableCornerButton::section {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
}}

/* ---------- 标签页 ---------- */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    top: -1px;
    background-color: {BG_ALT};
}}
QTabBar::tab {{
    background-color: {BG};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-bottom: none;
    padding: 6px 16px;
    margin-right: 2px;
    letter-spacing: 1px;
}}
QTabBar::tab:hover {{
    color: {TEXT};
    border-color: {BORDER_HI};
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    background-color: {PANEL};
    border-color: {ACCENT_DIM};
    border-bottom: 1px solid {PANEL};
}}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: {BG};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_HI};
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT_DIM};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {BG};
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_HI};
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {ACCENT_DIM};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background-color: {BG_ALT};
    border: 1px solid {BORDER};
    text-align: center;
    color: {ACCENT};
    height: 16px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT_DIM};
    margin: 1px;
}}

/* ---------- 菜单 / 工具栏 / 状态栏 ---------- */
QMenuBar {{
    background-color: {BG};
    color: {TEXT};
}}
QMenuBar::item:selected {{
    background-color: {PANEL_HI};
    color: {ACCENT};
}}
QMenu {{
    background-color: {PANEL};
    border: 1px solid {ACCENT_DIM};
    color: {TEXT};
}}
QMenu::item {{
    padding: 5px 24px 5px 20px;
}}
QMenu::item:selected {{
    background-color: {ACCENT_DIM};
    color: {BG};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 8px;
}}
QStatusBar {{
    background-color: {PANEL};
    color: {ACCENT};
    border-top: 1px solid {BORDER_HI};
}}
QToolBar {{
    background-color: {PANEL};
    border-bottom: 1px solid {BORDER};
    spacing: 4px;
}}
QSplitter::handle {{
    background-color: {BORDER};
}}
QSplitter::handle:hover {{
    background-color: {ACCENT_DIM};
}}
QDialogButtonBox QPushButton {{
    min-width: 88px;
}}
"""


def _pick_font_family() -> str:
    families = set(QFontDatabase.families())
    for family in MONO_FAMILIES:
        if family in families:
            return family
    return "Consolas"


def apply(app: QApplication) -> None:
    """应用主题：Fusion 风格 + 深色调色板 + QSS + 等宽字体。"""
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(BG_ALT))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(BG))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(PANEL))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT_DIM))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(BG))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(PANEL))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(ACCENT))
    app.setPalette(palette)

    font = QFont(_pick_font_family(), 9)
    font.setStyleHint(QFont.StyleHint.Monospace)
    app.setFont(font)

    app.setStyleSheet(STYLESHEET)


__all__ = ["ACCENT", "BG", "apply", "STYLESHEET"]
