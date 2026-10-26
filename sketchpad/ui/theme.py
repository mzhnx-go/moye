"""浅桃主题：暖白粉外壳 + 深可可文字。只放颜色与 QSS，不含业务逻辑。

画布底色仍由 sketchpad/ui/canvas.py 的 CANVAS_BG 决定（草稿保持深色绘图面），
这里管的是窗口外壳、工具栏、状态栏、对话框与浮层。所有颜色集中在这里，
改主题只改这一个文件。
"""
from __future__ import annotations

WINDOW_BG = "#FFE9F1"        # 窗口/状态栏底色（暖白桃）
TOOLBAR_BG = "#FFF4F8"       # 工具栏/标题栏（比外壳更亮）
SURFACE = "#FFFCFD"          # 按钮、输入框、浮层主表面
SURFACE_HOVER = "#FFDCE8"    # 悬停
SURFACE_PRESSED = "#FFCEDF"  # 按下
BORDER = "#F6C7D8"           # 常规分隔
BORDER_STRONG = "#ECABC3"    # 输入框/浮层边界
OUTER_BORDER = "#ECABC3"     # 无边框窗口的外边界
TEXT = "#4B2F38"             # 主文字/图标（深可可，不用玫瑰）
TEXT_DIM = "#7D5862"         # 次级文字
TEXT_MUTED = "#9A7681"       # 辅助文字（仅大字号/非关键）
ACCENT = "#A84E35"           # 主强调：焦点、选中描边、菜单高亮（陶土桃）
ACCENT_HOVER = "#8F3F2A"
ACCENT_SOFT = "#FFD6E3"      # 选中底/浅染
ACCENT_BORDER = "#E59CB2"    # 浅桃描边
SLIDER_ACCENT = ACCENT        # 滑杆已选段与强调色同源
DANGER = "#A33A2A"
DANGER_SOFT = "#FFDDE3"
WARNING = "#8A5A00"
SUCCESS = "#2F7D5B"
PAPER = "#FFF5F8"
RADIUS = 7                    # 按钮圆角
SWATCH = 20                   # 色块直径
# 笔记卡片：白纸感 + 浅桃标题/工具行
NOTE_CARD_BG = "#FFFCFD"
NOTE_HEADER_BG = "#FFE3EE"
NOTE_TOOLS_BG = "#FFF1F6"
NOTE_RADIUS = 12

QSS = f"""
QMainWindow {{ background: {WINDOW_BG}; }}
QWidget#Root {{ background: transparent; }}
QWidget#ScratchRoot {{ background: {WINDOW_BG}; }}
QWidget#Toolbar {{ background: transparent; border: none; }}
QFrame#TopChromePanel {{
    background: {WINDOW_BG};
    border: none;
}}
QWidget#TitleBar {{
    background: rgba(255, 250, 252, 0.48);
    border-bottom: 1px solid rgba(236, 171, 195, 0.55);
}}
QFrame#TopChromePanel QWidget#TitleBar {{ background: transparent; border: none; }}
QWidget#TitleBar QLabel {{ color: {TEXT}; font-size: 13px; font-weight: 600; }}
QToolButton#WinButton {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0;
}}
QToolButton#WinButton:hover {{ background: {SURFACE_HOVER}; }}
QToolButton#WinButton:pressed {{ background: {SURFACE_PRESSED}; }}
QToolButton#WinButton:checked {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT_BORDER}; }}
QToolButton#CloseButton:hover {{ background: {DANGER_SOFT}; }}
/* 滚动区与它的视口：不要用 setStyleSheet 设在控件上——那会级联到所有按钮，把按钮背景刷没 */
QScrollArea#ToolbarScroll {{ background: {WINDOW_BG}; border: none; }}
QScrollArea#ToolbarScroll > QWidget#qt_scrollarea_viewport {{
    background: {WINDOW_BG};
    border: none;
}}
QScrollArea#ToolbarScroll > QWidget,
QScrollArea#ToolbarScroll > QWidget > QWidget {{ background: transparent; }}
QScrollArea#ToolbarScroll > QWidget > QWidget#Toolbar[dock="top"] {{
    background: transparent;
    border: none;
}}
QScrollArea#ToolbarScroll > QWidget > QWidget#Toolbar[dock="right"] {{
    background: {WINDOW_BG};
    border: none;
    border-radius: 0;
}}
QToolButton {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 2px;
}}
QToolButton:hover {{ background: {SURFACE_HOVER}; }}
QToolButton:pressed {{ background: {SURFACE_PRESSED}; }}
QToolButton:checked {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT_BORDER}; }}
/* 全屏批注开关：保持中性，不跟工具选中态抢 */
QToolButton#AnnotateButton:checked {{
    background: {SURFACE_HOVER};
    border: 1px solid {BORDER_STRONG};
}}
QToolButton#AnnotateButton:checked:hover {{
    background: {SURFACE_PRESSED};
}}
/* 截图按钮的下拉菜单区：系统样式会按高亮色刷蓝，改成中性桃色 */
QToolButton#CaptureButton::menu-button {{
    background: transparent;
    border: none;
    border-left: 1px solid {BORDER};
    width: 10px;
}}
QToolButton#CaptureButton::menu-button:hover,
QToolButton#CaptureButton::menu-button:pressed {{
    background: {SURFACE_HOVER};
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {SLIDER_ACCENT};
    border-radius: 2px;
}}
QSlider::add-page:horizontal {{
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border: 2px solid {SLIDER_ACCENT};
    border-radius: 7px;
    background: {SURFACE};
}}
QPushButton {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 5px 12px;
    min-height: 22px;
}}
QPushButton:hover {{ background: {SURFACE_HOVER}; }}
QPushButton:pressed {{ background: {SURFACE_PRESSED}; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; color: {ACCENT}; border-color: {ACCENT_BORDER}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; }}
/* 查找条关闭键：通用按钮的 12px 横向内边距会把 24px 按钮的内容区挤没，图标只剩几像素 */
QPushButton#FindClose, QPushButton#FindSearch {{
    background: transparent;
    padding: 0;
    min-width: 24px;
    min-height: 24px;
    border: none;
    border-radius: 6px;
}}
QPushButton#FindClose:hover, QPushButton#FindSearch:hover {{ background: {SURFACE_HOVER}; }}
QPushButton#FindClose:pressed, QPushButton#FindSearch:pressed {{ background: {SURFACE_PRESSED}; }}
QWidget#FindBar {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: 10px;
}}
QWidget#FindBar QLineEdit {{
    background: {SURFACE_HOVER};
    color: {TEXT};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 8px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QWidget#FindBar QLineEdit:focus {{ border-color: {ACCENT_BORDER}; background: {SURFACE}; }}
QWidget#FindBar QLabel#FindCount {{ color: {TEXT_DIM}; font-size: 11px; }}
/* 笔记卡片：标题/工具两行 + 画布。遮罩自己不画底（scrim 在 paintEvent 里画），
   否则 WA_StyledBackground 会先刷一层不透明的窗口底色，把下面的 PDF 盖死。 */
QWidget#NoteCard {{
    background: {NOTE_CARD_BG};
    border: 1px solid {BORDER_STRONG};
    border-radius: {NOTE_RADIUS}px;
}}
QWidget#NoteHeader {{
    background: {NOTE_HEADER_BG};
    border-top-left-radius: {NOTE_RADIUS}px;
    border-top-right-radius: {NOTE_RADIUS}px;
}}
QWidget#NoteHeader QLabel {{
    color: {TEXT};
    font-size: 12px;
    font-weight: 600;
}}
QWidget#NoteTools {{ background: {NOTE_TOOLS_BG}; }}
QStatusBar {{ background: {WINDOW_BG}; border: none; }}
QStatusBar::item {{ border: none; }}
QFrame#StatusPill {{
    background: transparent;
    border: none;
}}
QFrame#StatusPill QLabel {{
    color: {TEXT_DIM};
    padding: 0 4px;
}}
QFrame#StatusPill QLabel#SaveStatus {{
    color: {TEXT_DIM};
    font-size: 11px;
    background: transparent;
    border: none;
    padding: 0 4px;
}}
QFrame#StatusPill QLabel#StatusValue {{
    color: {TEXT_DIM};
    font-size: 11px;
    background: transparent;
    border: none;
    padding: 0 4px;
}}
QLabel {{ color: {TEXT_DIM}; }}
QToolTip {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 8px;
}}
/* 所有弹出菜单统一浅桃浮层；不写全局 QDialog，避免罩住原生取色器。 */
QMenu {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    background: transparent;
    color: {TEXT};
    padding: 6px 24px 6px 12px;
    border-radius: 5px;
}}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QMenu::item:disabled {{ color: {TEXT_MUTED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
/* 设置对话框：只按 objectName 命中。全局 QDialog 规则会罩住原生 QColorDialog，把它的
   白底刷成深灰导致文字看不清（PROGRESS §5）。 */
QDialog#SettingsDialog {{ background: {WINDOW_BG}; }}
QDialog#SettingsDialog QLabel {{ color: {TEXT}; }}
QDialog#SettingsDialog QCheckBox {{ color: {TEXT}; spacing: 6px; }}
QDialog#SettingsDialog QCheckBox:disabled {{ color: {TEXT_MUTED}; }}
QDialog#SettingsDialog QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {BORDER_STRONG}; background: {SURFACE};
}}
QDialog#SettingsDialog QCheckBox::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT};
}}
QDialog#SettingsDialog QLineEdit, QDialog#SettingsDialog QComboBox {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 4px 7px;
}}
QDialog#SettingsDialog QComboBox QAbstractItemView {{
    background: {SURFACE};
    color: {TEXT};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QDialog#SettingsDialog QPushButton {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 5px 12px;
}}
QDialog#SettingsDialog QPushButton:hover {{ background: {SURFACE_HOVER}; }}
QDialog#SettingsDialog QPushButton:pressed {{ background: {SURFACE_PRESSED}; }}
QDialog#SettingsDialog QTabWidget {{ background: {WINDOW_BG}; }}
QDialog#SettingsDialog QTabBar {{ background: {WINDOW_BG}; }}
QDialog#SettingsDialog QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    background: {SURFACE};
}}
QDialog#SettingsDialog QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 6px 14px;
    margin-right: 4px;
    border-radius: 7px;
    border: 1px solid transparent;
}}
QDialog#SettingsDialog QTabBar::tab:selected {{
    color: {ACCENT};
    background: {ACCENT_SOFT};
    border-color: {ACCENT_BORDER};
}}
QDialog#SettingsDialog QTabBar::tab:hover {{ color: {TEXT}; background: {SURFACE_HOVER}; }}
QDialog#SettingsDialog QGroupBox {{
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 10px 8px 10px;
    font-weight: 600;
}}
QDialog#SettingsDialog QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {TEXT};
}}
QDialog#SettingsDialog QLabel#SettingsHint {{ color: {TEXT_DIM}; font-weight: normal; }}
QDialog#SettingsDialog QLabel#FixedBinding {{ color: {TEXT_DIM}; font-weight: normal; }}
/* 滚动条：浅桃细条，无箭头；滑块留 5px 圆角 */
QScrollBar:vertical {{
    background: {WINDOW_BG};
    width: 10px;
    margin: 0;
    border: none;
}}
QScrollBar:horizontal {{
    background: {WINDOW_BG};
    height: 10px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    min-height: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG};
    min-width: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover {{ background: {ACCENT_BORDER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{
    background: transparent;
    border: none;
    width: 0;
    height: 0;
}}
QScrollBar::up-arrow, QScrollBar::down-arrow,
QScrollBar::left-arrow, QScrollBar::right-arrow {{
    background: transparent;
    border: none;
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
/* 页码跳转输入框：只按 QInputDialog 命中，别写全局 QDialog（会罩住颜色对话框等） */
QInputDialog {{ background: {WINDOW_BG}; }}
QInputDialog QLabel {{ color: {TEXT}; }}
QInputDialog QLineEdit, QInputDialog QSpinBox {{
    background: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 3px 7px;
}}
QInputDialog QSpinBox QLineEdit {{
    background: transparent;
    border: none;
    padding: 0;
}}
"""
