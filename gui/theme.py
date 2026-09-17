# -*- coding: utf-8 -*-
################################
# File Name   : theme.py
# Author      : liyanqing.1987
# Created On  : 2026-08-24
# Description : Global QSS theme for lsfMonitor. Light theme follows Ant Design 5
#               / Fluent Design style (same palette as PMP).
################################
from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QIcon, QPainter, QColor, QPen, QPixmap
import os as _os


def make_action_icon(draw_fn, size=16, color='#8C8C8C'):
    """Build a QIcon by calling draw_fn(QPainter, size_float, QColor) on a transparent pm.

    Used for menu-item icons so they share a consistent muted-gray color and
    don't depend on external bitmap assets.
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    draw_fn(p, float(size), QColor(color))
    p.end()
    return QIcon(pm)


def draw_setup_icon(p, s, c):
    """Gear/cog symbol for Setup menu items — simplified for clarity at 16px."""
    import math
    pen = QPen(c)
    pen.setWidthF(max(1.0, s * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    cx, cy = s * 0.5, s * 0.5
    r_ring = s * 0.26
    # Outer ring
    p.drawEllipse(QPointF(cx, cy), r_ring, r_ring)
    # 6 rectangular teeth sticking out from the ring
    tooth_w = s * 0.10
    tooth_l = s * 0.10

    for i in range(6):
        a = i * math.pi / 3  # 60° apart
        dx, dy = math.cos(a), math.sin(a)
        # Outer tip of tooth
        x1 = cx + dx * (r_ring + tooth_l)
        y1 = cy + dy * (r_ring + tooth_l)
        # Base on the ring (a tiny bit inside to meet the stroke cleanly)
        x0 = cx + dx * (r_ring - pen.widthF() * 0.3)
        y0 = cy + dy * (r_ring - pen.widthF() * 0.3)
        # Perpendicular for tooth width
        px, py = -dy, dx
        p.drawLine(QPointF(x0 - px * tooth_w * 0.5, y0 - py * tooth_w * 0.5),
                   QPointF(x1 - px * tooth_w * 0.5, y1 - py * tooth_w * 0.5))
        p.drawLine(QPointF(x0 + px * tooth_w * 0.5, y0 + py * tooth_w * 0.5),
                   QPointF(x1 + px * tooth_w * 0.5, y1 + py * tooth_w * 0.5))
        p.drawLine(QPointF(x1 - px * tooth_w * 0.5, y1 - py * tooth_w * 0.5),
                   QPointF(x1 + px * tooth_w * 0.5, y1 + py * tooth_w * 0.5))

    # Center hole (filled dot)
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(cx, cy), s * 0.075, s * 0.075)


def draw_debug_icon(p, s, c):
    """Bug/beetle symbol for Debug — simplified for 16px: no legs, bold body."""
    pen = QPen(c)
    pen.setWidthF(max(1.0, s * 0.10))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    cx, cy = s * 0.5, s * 0.58
    r = s * 0.28
    # Body (filled ellipse — cleaner at small sizes)
    p.setBrush(c)
    p.drawEllipse(QPointF(cx, cy), r, r * 0.82)
    # Center highlight line on body (white line for the wing seam, on top)
    p.setBrush(Qt.NoBrush)
    hl_pen = QPen(QColor('#FFFFFF'))
    hl_pen.setWidthF(max(1.0, s * 0.07))
    hl_pen.setCapStyle(Qt.RoundCap)
    p.setPen(hl_pen)
    p.drawLine(QPointF(cx, cy - r * 0.55), QPointF(cx, cy + r * 0.65))
    # Head (small filled circle on top)
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    head_r = s * 0.10
    head_cy = cy - r - head_r * 0.2
    p.drawEllipse(QPointF(cx, head_cy), head_r, head_r)
    # Antennae (two short curved strokes going up/out)
    p.setBrush(Qt.NoBrush)
    p.setPen(pen)
    ant_y0 = head_cy - head_r * 0.3
    p.drawLine(QPointF(cx, ant_y0), QPointF(cx - s * 0.14, ant_y0 - s * 0.18))
    p.drawLine(QPointF(cx, ant_y0), QPointF(cx + s * 0.14, ant_y0 - s * 0.18))


SETUP_ICON_CACHE = {}
DEBUG_ICON_CACHE = {}


def setup_icon(color='#8C8C8C', size=16):
    """Return a cached gear QIcon for Setup menu actions."""
    key = (color, size)

    if key not in SETUP_ICON_CACHE:
        SETUP_ICON_CACHE[key] = make_action_icon(draw_setup_icon, size=size, color=color)

    return SETUP_ICON_CACHE[key]


def debug_icon(color='#8C8C8C', size=16):
    """Return a cached bug QIcon for Debug menu actions."""
    key = (color, size)

    if key not in DEBUG_ICON_CACHE:
        DEBUG_ICON_CACHE[key] = make_action_icon(draw_debug_icon, size=size, color=color)

    return DEBUG_ICON_CACHE[key]


# ==========================================================================
# Palette (Ant Design Pro 风格蓝白主题)
# --------------------------------------------------------------------------
# 设计要点:
#   * 页面底用冷灰蓝 (#F1F5F9),白卡片浮在上面,形成"纸在桌面"的层次;
#   * 表头用 AntD primary-50 浅蓝底 (#F0F5FF) + primary-800 深蓝字 (#1E40AF),
#     而不是无彩灰,让表头既区分数据行又带品牌感;
#   * 边框加深一档到 #DCE3ED / #C3CDDB,轮廓更清晰,解决"发白无力";
#   * 侧边栏选中项: 浅蓝底 + 左侧 3px 主色竖条 (AntD 菜单标志性样式);
#   * Tab 选中下划线加粗到 3px,让当前 tab 一眼可辨;
#   * 表格行 hover/选中/zebra 都在同一蓝色系里递进,无突兀跳变;
#   * 状态色 RUN 绿 / EXIT 红 / PEND 蓝 保持高饱和,作为画面"彩色锚点"。
# ==========================================================================
PRIMARY = '#2563EB'              # AntD blue-600 主品牌色
PRIMARY_HOVER = '#4080FF'        # AntD blue-5 (hover 亮一档)
PRIMARY_PRESSED = '#1D4ED8'      # AntD blue-7
PRIMARY_BG = '#E8F1FF'           # 选中/按下浅蓝底 (比 #DBEAFE 稍柔和,更 AntD)
PRIMARY_BG_HOVER = '#BAE0FF'     # 更深一档
PRIMARY_LIGHT = '#F0F5FF'        # AntD blue-50,表头蓝底
# JOB页 MEMORY/IDLE_FACTOR 子tab容器底色;比 PRIMARY_LIGHT 更白一档,补偿
# Box+Raised 阴影叠加带来的偏蓝,使其与主 tab bar 视觉一致
CHART_FRAME_BG = '#F5F8FC'

# 大面块色
HEADER_BG = PRIMARY_LIGHT        # 表头:AntD 蓝-50 浅蓝 (带品牌色,区别于纯白数据行)
HEADER_BG_HOVER = '#E0EBFF'
HEADER_TEXT = '#1E40AF'          # 表头字:AntD 蓝-800 深蓝,比灰字更"定调"
SIDEBAR_SELECTED_BG = '#E8F1FF'
SIDEBAR_SELECTED_TEXT = PRIMARY_PRESSED

# 层级背景
BG_PAGE = '#F1F5F9'             # 页面底:slate-100
BG_SIDEBAR = '#FAFBFC'           # 侧边栏:比白稍灰,区分主区
BG_WHITE = '#FFFFFF'
BG_CARD = '#FFFFFF'
BG_TABLE_ALT = '#FAFBFD'         # 斑马纹:比纯白稍冷一度
BG_TABLE_HOVER = '#EAF3FF'       # 行 hover:比 primary_bg 更浅
BG_TABLE_SELECTED = PRIMARY_BG   # 选中行
BG_DISABLED = '#F1F5F9'

# 文字色
TEXT_PRIMARY = '#0F172A'         # Slate-900
TEXT_REGULAR = '#1F2937'         # Slate-800
TEXT_SECONDARY = '#4B5563'       # Slate-600
TEXT_DISABLED = '#94A3B8'
TEXT_INVERSE = '#FFFFFF'

# 边框 (加深一档,解决"惨白看不清轮廓")
BORDER = '#DCE3ED'               # 常规分隔线
BORDER_STRONG = '#C3CDDB'        # 输入框/按钮/卡片边


# White check mark icon for QCheckBox::indicator:checked (drawn over the blue
# fill). Stored under data/pictures so QSS image: url() resolves reliably.
CHECK_ICON = _os.path.join(_os.environ.get('LSFMONITOR_INSTALL_PATH', '.'), 'data', 'pictures', 'check.png')

# 状态色:用 Ant Design 6 色板,比旧版稍微沉稳一点
STATUS_RUN = '#16A34A'     # Green-600 (比 #52C41A 稍深,更耐看)
STATUS_PEND = PRIMARY     # 排队中 = 品牌蓝
STATUS_DONE = '#94A3B8'   # 完成 = 中性灰 (Slate-400)
STATUS_EXIT = '#DC2626'   # 失败 = Red-600 (比 #FF4D4F 稳重)

# ==========================================================================
# Chart palette (matplotlib) — aligns chart colors with the AntD-ish UI theme.
# Two parallel dicts keyed by dark_mode bool so drawing code can do
# CHART_COLORS[bool(self.dark_mode)] and get a coherent palette.
# Kept here (no matplotlib import) so theme.py stays dependency-free; consumed
# by common_pyqt5.style_axes / apply_chart_style.
# ==========================================================================
# Sequential series for multi-line charts (AntD blue-leaning, color-blind safe).
CHART_SERIES = ['#2563EB', '#16A34A', '#F59E0B', '#8B5CF6', '#06B6D4', '#EC4899']

CHART_COLORS = {
    False: {  # light
        'facecolor':    BG_WHITE,        # axes background
        'figure_face':  BG_WHITE,        # figure background (matches card)
        'text':         TEXT_PRIMARY,
        'label':        TEXT_SECONDARY,
        'grid':         '#CBD5E1',       # slate-300, y-grid only
        'spine':        BORDER_STRONG,
        'tick':         TEXT_SECONDARY,
    },
    True: {   # dark
        'facecolor':    '#19232D',
        'figure_face':  '#19232D',
        'text':         '#FFFFFF',
        'label':        '#CBD5E1',
        'grid':         '#3B4A5A',
        'spine':        '#3B4A5A',
        'tick':         '#CBD5E1',
    },
}

# Semantic colors for single-series charts (mem / idle / ut / run / pend...).
# Falls back to CHART_SERIES for generic multi-series.
CHART_MEM_COLOR = STATUS_RUN          # memory curve = green-600
CHART_IDLE_COLOR = PRIMARY             # idle_factor curve = brand blue
CHART_UT_COLOR = '#2563EB'          # utilization = brand blue
CHART_RUN_COLOR = STATUS_RUN         # RUN jobs count = green
CHART_PEND_COLOR = '#F59E0B'          # PEND jobs count = amber-500 (warn)
CHART_EXIT_COLOR = STATUS_EXIT        # EXIT / over-rusage = red

# Chart line widths — single source of truth so all curves stay consistent.
# Detail mode (dense sample points, e.g. per-minute ut) uses a thinner line.
CHART_LINEWIDTH = 1.2
CHART_LINEWIDTH_DETAIL = 0.6

FONT_FAMILY = (
    '"PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei", '
    '"Segoe UI", "Helvetica Neue", Arial, sans-serif'
)
FONT_SIZE = 13

# ==========================================================================
# Sidebar (QListWidget outer navigation)
# ==========================================================================
SIDEBAR_LIGHT_QSS = f'''
QListWidget#navList {{
    background: {BG_SIDEBAR};
    border: none;
    border-right: 1px solid {BORDER};
    outline: none;
    font-size: {FONT_SIZE}px;
    font-family: {FONT_FAMILY};
    padding-top: 8px;
}}
QListWidget#navList::item {{
    color: {TEXT_REGULAR};
    padding: 12px 16px 12px 20px;
    margin: 2px 0;
    border: none;
    border-left: 3px solid transparent;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    border-top-left-radius: 0px;
    border-bottom-left-radius: 0px;
}}
QListWidget#navList::item:selected {{
    background: {SIDEBAR_SELECTED_BG};
    color: {SIDEBAR_SELECTED_TEXT};
    font-weight: 600;
    border-left: 3px solid {PRIMARY};
}}
QListWidget#navList::item:hover:!selected {{
    background: {BG_TABLE_HOVER};
    color: {TEXT_PRIMARY};
}}
QListWidget#navList::item:hover:selected {{
    background: {PRIMARY_BG};
}}
'''

SIDEBAR_DARK_QSS = '''
QListWidget#navList {
    background: #1e1e1e;
    border: none;
    border-right: 1px solid #2d2d2d;
    outline: none;
    font-size: 13px;
    padding-top: 8px;
}
QListWidget#navList::item {
    color: #cccccc;
    padding: 12px 16px 12px 20px;
    margin: 2px 0;
    border-radius: 6px;
}
QListWidget#navList::item:selected {
    background: #2a2d2e;
    color: #4a9eff;
    font-weight: 600;
}
QListWidget#navList::item:hover:!selected {
    background: #262626;
}
'''

# ==========================================================================
# Light theme (global application QSS)
# ==========================================================================
LIGHT_THEME_QSS = f'''
* {{
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE}px;
}}

QMainWindow {{
    background: {BG_PAGE};
}}

QWidget#centralWidget {{
    background: {BG_PAGE};
}}

/* ---- Menu bar ---- */
QMenuBar {{
    background: {BG_WHITE};
    color: {TEXT_PRIMARY};
    border-bottom: 1px solid {BORDER};
    padding: 4px 8px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 6px 12px;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background: {PRIMARY_BG};
    color: {PRIMARY};
}}
QMenuBar::item:pressed {{
    background: {PRIMARY_BG_HOVER};
}}
QMenu {{
    background: {BG_WHITE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 24px;
    border-radius: 4px;
    color: {TEXT_PRIMARY};
}}
QMenu::item:selected {{
    background: {PRIMARY_BG};
    color: {PRIMARY};
}}
/* Show a check mark for checkable (toggle) menu items so the user can see
   whether the option is on or off. Uses the same check.png icon as
   QCheckBox::indicator:checked for visual consistency. */
QMenu::indicator {{
    width: 16px;
    height: 16px;
    margin-left: 8px;
}}
QMenu::indicator:unchecked {{
    border: 1.5px solid {BORDER_STRONG};
    border-radius: 3px;
    background: {BG_WHITE};
}}
QMenu::indicator:checked {{
    background: {PRIMARY};
    border: 1.5px solid {PRIMARY};
    border-radius: 3px;
    image: url("{CHECK_ICON}");
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 8px;
}}

/* ---- Main panel inner tabs (JOB/JOBS/HOSTS/..., FEATURE/USER/..., RUN/LOG)
   仅作用于 PanelBase.main_tab (objectName="mainTab"),不影响嵌套的小 tab
   (例如 JOB tab 里的 chart 子 QTabWidget)。

   视觉 (Chrome / AntD card-type tabs):
     * tab 条整体是浅蓝底 (AntD primary-50),与页面冷灰底呼应并稍显色;
     * 每个 tab 是一张独立的白色小卡片,顶部圆角,左右 margin 留出缝隙
       (缝隙处透出浅蓝底色),形成"一排小白纸飘在蓝底上"的层次;
     * 未选中 tab: 白底 + 淡灰边 + 顶圆角,底边闭合,独立悬浮;
     * Hover tab: 浅蓝底反馈;
     * 选中 tab: 白底 + 深一点的边 + 底边延伸 -1px"咬"掉 pane 顶线,
       看起来直接"长"在下面的白卡片上,3px 主色下划线强化选中;
     * pane: 白卡片 + 四周边框,和选中 tab 自然连成一体。*/
/* Give the QTabWidget itself a light-blue background so the tab-strip row
   is blue all the way to the right edge even when the QTabBar does not
   expand to fill (or the style leaves a gap between bar and pane). The
   ::pane below keeps the content area white as a card. */
QTabWidget#mainTab {{
    background: {PRIMARY_LIGHT};
}}
QTabWidget#mainTab::pane {{
    background: {BG_WHITE};
    border: 1px solid {BORDER_STRONG};
    top: -1px;
}}
QTabWidget#mainTab > QTabBar {{
    background: {PRIMARY_LIGHT};
    alignment: left;
}}
QTabWidget#mainTab > QTabBar::tab {{
    background: {BG_WHITE};
    color: {TEXT_SECONDARY};
    padding: 8px 20px;
    margin: 6px 4px 0 4px;
    border: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    min-width: 60px;
    font-size: {FONT_SIZE}px;
}}
QTabWidget#mainTab > QTabBar::tab:first {{
    margin-left: 8px;
}}
QTabWidget#mainTab > QTabBar::tab:hover {{
    color: {PRIMARY_HOVER};
    background: {BG_TABLE_HOVER};
    border-color: {BORDER_STRONG};
}}
QTabWidget#mainTab > QTabBar::tab:selected {{
    color: {PRIMARY};
    background: {BG_WHITE};
    border-color: {BORDER_STRONG};
    border-bottom: 2px solid {BG_WHITE};
    margin-bottom: -1px;
}}
QTabWidget#mainTab > QTabBar::tab:disabled {{
    color: {TEXT_DISABLED};
    background: {BG_DISABLED};
}}

/* ---- Chart card frame (the QFrame.Box that wraps the MEMORY/IDLE_FACTOR
   sub-tab widget on the JOB page).
   On screen the QTabWidget leaves the tab-strip area to the right of the last
   (non-expanding) tab button unpainted, so that gap shows through to this
   frame. Painting it fills the gap so the MEMORY/IDLE_FACTOR tab strip extends
   to the right edge; the white chartTab ::pane below still covers the
   chart/toolbar content.

   NOTE: uses CHART_FRAME_BG (a near-white blue tuned by eye) instead of
   PRIMARY_LIGHT — this frame carries a Box+Raised border/shadow and sits over
   the white pane, so a flat PRIMARY_LIGHT here reads slightly bluer than the
   main tab bar; CHART_FRAME_BG compensates so they match visually. */
QFrame#chartFrame {{
    background: {CHART_FRAME_BG};
}}
/* ---- Chart sub-tabs (MEMORY/IDLE_FACTOR inside JOB tab).
   The QTabWidget gets a light-blue background as a fallback (visible when the
   widget is grabbed standalone); on screen the chartFrame behind it is what
   fills the right-hand tab-strip gap. The ::pane stays white so the
   chart/toolbar content reads as a white card below the strip. ---- */
QTabWidget#chartTab {{
    background: {PRIMARY_LIGHT};
}}
QTabWidget#chartTab::pane {{
    background: {BG_WHITE};
    border: 1px solid {BORDER_STRONG};
    top: -1px;
}}
QTabWidget#chartTab > QTabBar {{
    background: {PRIMARY_LIGHT};
    alignment: left;
}}
QTabWidget#chartTab > QTabBar::tab {{
    background: {BG_WHITE};
    color: {TEXT_SECONDARY};
    padding: 5px 14px;
    margin: 4px 3px 0 3px;
    border: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    min-width: 50px;
    font-size: {FONT_SIZE}px;
}}
QTabWidget#chartTab > QTabBar::tab:first {{
    margin-left: 6px;
}}
QTabWidget#chartTab > QTabBar::tab:hover {{
    color: {PRIMARY_HOVER};
    background: {BG_TABLE_HOVER};
    border-color: {BORDER_STRONG};
}}
QTabWidget#chartTab > QTabBar::tab:selected {{
    color: {PRIMARY};
    background: {BG_WHITE};
    border-color: {BORDER_STRONG};
    border-bottom: 2px solid {BG_WHITE};
    margin-bottom: -1px;
}}

/* ---- ANALYZE sub-tabs wrapping frame (JOB/USER/QUEUE/CLUSTER inside
   AI→ANALYZE). Exact mirror of QFrame#chartFrame (lsf JOB page): QTabWidget
   does not paint the area to the right of the last tab under Fusion+QSS,
   so the wrapping Box+Raised QFrame fills that gap. CHART_FRAME_BG is used
   (rather than PRIMARY_LIGHT) for the same reason as chartFrame — the
   Box+Raised border/shadow adds a subtle blue tint, and CHART_FRAME_BG
   compensates so the tab strip visually matches the outer mainTab bar. */
QFrame#analyzeFrame {{
    background: {CHART_FRAME_BG};
}}
/* ---- ANALYZE sub-tabs (JOB/USER/QUEUE/CLUSTER) inside AI→ANALYZE.
   Same treatment as #chartTab: QTabWidget gets a light-blue fallback
   background (visible standalone); on screen the analyzeFrame behind fills
   the right-hand tab-strip gap. QTabBar/tabs use chartTab's smaller
   5px/14px padding since this is a second-level strip. */
QTabWidget#analyzeSubTab {{
    background: {PRIMARY_LIGHT};
}}
QTabWidget#analyzeSubTab::pane {{
    background: {BG_WHITE};
    border: 1px solid {BORDER_STRONG};
    top: -1px;
}}
QTabWidget#analyzeSubTab > QTabBar {{
    background: {PRIMARY_LIGHT};
    alignment: left;
}}
QTabWidget#analyzeSubTab > QTabBar::tab {{
    background: {BG_WHITE};
    color: {TEXT_SECONDARY};
    padding: 5px 14px;
    margin: 4px 3px 0 3px;
    border: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    min-width: 50px;
    font-size: {FONT_SIZE}px;
}}
QTabWidget#analyzeSubTab > QTabBar::tab:first {{
    margin-left: 6px;
}}
QTabWidget#analyzeSubTab > QTabBar::tab:hover {{
    color: {PRIMARY_HOVER};
    background: {BG_TABLE_HOVER};
    border-color: {BORDER_STRONG};
}}
QTabWidget#analyzeSubTab > QTabBar::tab:selected {{
    color: {PRIMARY};
    background: {BG_WHITE};
    border-color: {BORDER_STRONG};
    border-bottom: 2px solid {BG_WHITE};
    margin-bottom: -1px;
}}

/* ---- Generic (nested) QTabWidget: card-style tabs matching #mainTab so
   secondary/chart tabs (e.g. MEMORY/IDLE_FACTOR) sit on a light-blue bar as
   white cards, with the selected tab blue text + clear border isolation. ---- */
QTabWidget::pane {{
    background: {BG_WHITE};
    border: 1px solid {BORDER_STRONG};
    top: -1px;
}}
QTabBar {{
    background: {PRIMARY_LIGHT};
    alignment: left;
}}
QTabBar::tab {{
    background: {BG_WHITE};
    color: {TEXT_SECONDARY};
    padding: 5px 14px;
    margin: 4px 3px 0 3px;
    border: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    min-width: 50px;
    font-size: {FONT_SIZE}px;
}}
QTabBar::tab:first {{
    margin-left: 6px;
}}
QTabBar::tab:hover {{
    color: {PRIMARY_HOVER};
    background: {BG_TABLE_HOVER};
    border-color: {BORDER_STRONG};
}}
QTabBar::tab:selected {{
    color: {PRIMARY};
    background: {BG_WHITE};
    border-color: {BORDER_STRONG};
    border-bottom: 2px solid {BG_WHITE};
    margin-bottom: -1px;
}}

/* ---- Tables ---- */
QTableWidget {{
    background-color: {BG_WHITE};
    alternate-background-color: {BG_TABLE_ALT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    gridline-color: {BORDER};
    selection-background-color: {BG_TABLE_SELECTED};
    selection-color: {TEXT_PRIMARY};
    outline: none;
}}
QTableWidget::item {{
    padding: 4px 8px;
    border-bottom: 1px solid {BORDER};
}}
QTableWidget::item:hover {{
    background-color: {BG_TABLE_HOVER};
}}
QTableWidget::item:selected {{
    background-color: {BG_TABLE_SELECTED};
    color: {TEXT_PRIMARY};
}}
QTableWidget QHeaderView {{
    background: {BG_WHITE};
    border: none;
}}
QTableWidget QHeaderView::section {{
    background-color: {HEADER_BG};
    color: {HEADER_TEXT};
    font-weight: 600;
    padding: 9px 12px;
    border: none;
    border-right: 1px solid rgba(37, 99, 235, 0.08);
    border-bottom: 2px solid rgba(37, 99, 235, 0.12);
}}
QTableWidget QHeaderView::section:last {{
    border-right: none;
}}
QTableWidget QHeaderView::section:hover {{
    background-color: {HEADER_BG_HOVER};
    color: {PRIMARY_PRESSED};
}}
QTableCornerButton::section {{
    background-color: {HEADER_BG};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
}}

/* ---- Buttons ---- */
QPushButton {{
    background: {BG_WHITE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    padding: 5px 16px;
    min-height: 20px;
}}
QPushButton:hover {{
    color: {PRIMARY};
    border-color: {PRIMARY_HOVER};
    background: {PRIMARY_BG};
}}
QPushButton:pressed {{
    color: {PRIMARY_PRESSED};
    border-color: {PRIMARY_PRESSED};
    background: {PRIMARY_BG_HOVER};
}}
QPushButton:disabled {{
    color: {TEXT_DISABLED};
    background: {BG_DISABLED};
    border-color: {BORDER};
}}
QPushButton:default {{
    background: {PRIMARY};
    color: {TEXT_INVERSE};
    border-color: {PRIMARY};
}}
QPushButton:default:hover {{
    background: {PRIMARY_HOVER};
    border-color: {PRIMARY_HOVER};
    color: {TEXT_INVERSE};
}}
QPushButton:default:pressed {{
    background: {PRIMARY_PRESSED};
    border-color: {PRIMARY_PRESSED};
    color: {TEXT_INVERSE};
}}

/* ---- Inputs ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {BG_WHITE};
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    padding: 4px 10px;
    color: {TEXT_PRIMARY};
    selection-background-color: {PRIMARY_BG_HOVER};
    selection-color: {TEXT_PRIMARY};
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover,
QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {PRIMARY_HOVER};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {PRIMARY};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    background: {BG_DISABLED};
    color: {TEXT_DISABLED};
}}
QDateEdit, QComboBox {{
    background: {BG_WHITE};
    border: 1px solid {BORDER_STRONG};
    border-radius: 4px;
    padding: 3px 8px;
    color: {TEXT_PRIMARY};
    min-height: 22px;
}}
QDateEdit:hover, QComboBox:hover {{
    border-color: {PRIMARY_HOVER};
}}
QDateEdit:focus, QComboBox:focus, QComboBox:on {{
    border-color: {PRIMARY};
}}
QComboBox:!editable {{
    /* Non-editable combo gets room on the right for the arrow without
       overlapping the text. QComboCheckBox is editable (has a QLineEdit)
       so it already adds padding through its own line-edit. */
    padding-right: 24px;
}}
QDateEdit::drop-down, QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
    border-left: 1px solid {BORDER};
    background: transparent;
    border-top-right-radius: 4px;
    border-bottom-right-radius: 4px;
}}
QDateEdit::drop-down:hover, QComboBox::drop-down:hover {{
    background: {PRIMARY_BG};
    border-left-color: {PRIMARY_HOVER};
}}
/* Draw a visible dark triangle arrow ourselves; the Fusion default can render
   nearly white on some setups, making the dropdown affordance invisible. */
QComboBox::down-arrow, QDateEdit::down-arrow {{
    image: none;
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {TEXT_SECONDARY};
}}
QComboBox QLineEdit, QDateEdit QLineEdit {{
    /* The inner QLineEdit of an editable combo (QComboCheckBox) / QDateEdit
       must NOT draw its own border — the outer widget already draws the card
       border. Without this rule, editable combos render a nested double
       border and visually look like a plain QLineEdit with no dropdown. */
    border: none;
    background: transparent;
    padding: 0px;
    /* Reserve room on the right for the drop-down arrow; otherwise the
       read-only line edit text fills the whole width and the arrow (which
       QComboCheckBox relies on as the only popup trigger besides the
       content-click filter) is squeezed out of view. */
    margin-right: 22px;
}}
QComboBox QAbstractItemView {{
    background: {BG_WHITE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    selection-background-color: {PRIMARY_BG};
    selection-color: {PRIMARY};
    outline: none;
    padding: 4px;
}}
QComboBox QAbstractItemView::item {{
    padding: 4px 8px;
    border-radius: 3px;
    min-height: 22px;
}}
QComboBox QAbstractItemView::item:hover {{
    background: {PRIMARY_BG};
    color: {PRIMARY};
}}

/* ---- CheckBox / RadioButton ----
   Indicator dimensions are explicit so the box renders as a clean square
   inside QComboBox drop-down lists (where item padding otherwise squeezes
   the indicator to a collapsed "two vertical lines" look). Border is
   1.5px solid gray-400 for the unchecked state, blue fill + white tick
   when checked. */
QCheckBox, QRadioButton {{
    color: {TEXT_PRIMARY};
    spacing: 8px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
}}
QCheckBox::indicator:unchecked {{
    border: 1.5px solid {BORDER_STRONG};
    background: {BG_WHITE};
    border-radius: 3px;
}}
QCheckBox::indicator:checked {{
    background: {PRIMARY};
    border: 1.5px solid {PRIMARY};
    border-radius: 3px;
    image: url("{CHECK_ICON}");
}}
QCheckBox::indicator:indeterminate {{
    background: {PRIMARY_BG};
    border: 1.5px solid {PRIMARY};
    border-radius: 3px;
}}
QRadioButton::indicator {{
    border: 1.5px solid {BORDER_STRONG};
    background: {BG_WHITE};
    border-radius: 8px;
}}
QRadioButton::indicator:checked {{
    background: {BG_WHITE};
    border: 4px solid {PRIMARY};
    border-radius: 8px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {PRIMARY_HOVER};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {TEXT_DISABLED};
    background: {BG_DISABLED};
}}

/* ---- Frames (card style for Box frames / filter bars) ---- */
QFrame[frameShape="1"] {{
    border: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    background: {BG_CARD};
}}

/* ---- Scroll bars (thin) ---- */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG};
    border-radius: 4px;
    min-height: 30px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover {{
    background: #94A3B8;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG};
    border-radius: 4px;
    min-width: 30px;
    margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #94A3B8;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

/* ---- Tooltip ---- */
QToolTip {{
    background: {BG_WHITE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}}

/* ---- QMessageBox / QDialog ---- */
QDialog {{
    background: {BG_WHITE};
}}
QMessageBox {{
    background: {BG_WHITE};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 8px;
}}
QMessageBox QLabel {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE}px;
    font-family: {FONT_FAMILY};
    padding: 8px 4px;
}}
QMessageBox QPushButton {{
    min-width: 72px;
    padding: 6px 20px;
    margin: 0px 4px;
}}
QMessageBox QPushButton:first {{
    margin-left: 0px;
}}
QMessageBox QPushButton:last {{
    margin-right: 0px;
}}

/* ---- GroupBox ---- */
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 10px;
    background: {BG_WHITE};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: {TEXT_PRIMARY};
    font-weight: 600;
}}

/* ---- Status bar ---- */
QStatusBar {{
    background: {BG_WHITE};
    border-top: 1px solid {BORDER};
    color: {TEXT_SECONDARY};
}}
QStatusBar::item {{
    border: none;
}}

/* ---- Splitter ---- */
QSplitter::handle {{
    background: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}

/* ---- Progress bar ---- */
QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    background: {BG_DISABLED};
    text-align: center;
    color: {TEXT_PRIMARY};
}}
QProgressBar::chunk {{
    background: {PRIMARY};
    border-radius: 3px;
}}
'''
