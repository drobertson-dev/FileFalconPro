"""
Light and dark themes for File Falcon Pro.

Colours are defined once as tokens and turned into both a QPalette (for native-drawn
bits like checkboxes and scrollbars) and a stylesheet (for everything custom). The theme
follows the system appearance and switches live when it changes.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFontDatabase, QGuiApplication, QPalette
from PyQt6.QtWidgets import QApplication


@dataclass(frozen=True)
class Tokens:
	dark: bool
	window: str
	sidebar: str
	surface: str
	surface_alt: str
	border: str
	border_strong: str
	text: str
	muted: str
	faint: str
	accent: str
	accent_hover: str
	accent_text: str
	accent_soft: str
	success: str
	success_soft: str
	warning: str
	warning_soft: str
	danger: str
	danger_soft: str
	selection: str


LIGHT = Tokens(
	dark=False,
	window="#f6f6f8",
	sidebar="#ececf1",
	surface="#ffffff",
	surface_alt="#f9f9fb",
	border="#e3e3e9",
	border_strong="#cfcfd8",
	text="#1c1c21",
	muted="#6b6b76",
	faint="#a3a3ad",
	accent="#4f5bd5",
	accent_hover="#4350c8",
	accent_text="#ffffff",
	accent_soft="#ebedfc",
	success="#1f8f4e",
	success_soft="#e4f5ea",
	warning="#b26a00",
	warning_soft="#fdf1dc",
	danger="#c93636",
	danger_soft="#fbe6e6",
	selection="#dfe3fb",
)

DARK = Tokens(
	dark=True,
	window="#1b1b1f",
	sidebar="#151518",
	surface="#242429",
	surface_alt="#202025",
	border="#34343b",
	border_strong="#45454e",
	text="#ececf1",
	muted="#9a9aa6",
	faint="#6a6a75",
	accent="#7c86f2",
	accent_hover="#8d96f5",
	accent_text="#0f1030",
	accent_soft="#2c2f52",
	success="#4cc47e",
	success_soft="#1d3326",
	warning="#f0a63a",
	warning_soft="#3a2d17",
	danger="#f06b6b",
	danger_soft="#3d2022",
	selection="#33375e",
)

_current: Tokens = LIGHT


def tokens() -> Tokens:
	"""The tokens of the theme currently applied."""
	return _current


def system_prefers_dark() -> bool:
	return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def _palette(t: Tokens) -> QPalette:
	palette = QPalette()
	roles = {
		QPalette.ColorRole.Window: t.window,
		QPalette.ColorRole.WindowText: t.text,
		QPalette.ColorRole.Base: t.surface,
		QPalette.ColorRole.AlternateBase: t.surface_alt,
		QPalette.ColorRole.Text: t.text,
		QPalette.ColorRole.PlaceholderText: t.faint,
		QPalette.ColorRole.Button: t.surface,
		QPalette.ColorRole.ButtonText: t.text,
		QPalette.ColorRole.BrightText: t.accent_text,
		QPalette.ColorRole.Highlight: t.accent,
		QPalette.ColorRole.HighlightedText: t.accent_text,
		QPalette.ColorRole.ToolTipBase: t.surface,
		QPalette.ColorRole.ToolTipText: t.text,
		QPalette.ColorRole.Link: t.accent,
		QPalette.ColorRole.Mid: t.border,
		QPalette.ColorRole.Midlight: t.border,
		QPalette.ColorRole.Dark: t.border_strong,
		QPalette.ColorRole.Light: t.surface,
		QPalette.ColorRole.Shadow: "#000000",
	}
	for role, colour in roles.items():
		palette.setColor(role, QColor(colour))
	for role in (
		QPalette.ColorRole.Text,
		QPalette.ColorRole.WindowText,
		QPalette.ColorRole.ButtonText,
	):
		palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.faint))
	return palette


def _indicator_images(t: Tokens) -> dict[str, str]:
	"""Write checkbox images for this theme and return their paths for the stylesheet."""
	folder = Path(tempfile.gettempdir()) / f"filefalcon-theme-{'dark' if t.dark else 'light'}"
	folder.mkdir(exist_ok=True)
	box = '<rect x="1" y="1" width="16" height="16" rx="4.5"'
	check = '<path d="M5 9.2l2.6 2.6L13 6.4" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
	images = {
		"unchecked": f'{box} fill="{t.surface}" stroke="{t.border_strong}" stroke-width="1.3"/>',
		"checked": f'{box} fill="{t.accent}"/>' + check.format(c=t.accent_text),
		"checked_disabled": f'{box} fill="{t.border_strong}"/>' + check.format(c=t.surface),
		"unchecked_disabled": f'{box} fill="{t.surface_alt}" stroke="{t.border}" stroke-width="1.3"/>',
	}
	chevron = '<path d="M5 7l4 4 4-4" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
	images["chevron"] = chevron.format(c=t.muted)
	images["chevron_disabled"] = chevron.format(c=t.faint)
	paths = {}
	for name, body in images.items():
		path = folder / f"{name}.svg"
		svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">{body}</svg>'
		if not path.exists() or path.read_text() != svg:
			path.write_text(svg)
		paths[name] = path.as_posix()
	return paths


def stylesheet(t: Tokens) -> str:
	ind = _indicator_images(t)
	mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
	return f"""
	* {{ outline: none; }}
	QWidget {{ color: {t.text}; font-size: 13px; }}
	QMainWindow, QWidget#Page {{ background: {t.window}; }}
	QToolTip {{
		background: {t.surface}; color: {t.text}; border: 1px solid {t.border_strong};
		border-radius: 6px; padding: 5px 8px;
	}}

	/* Sidebar ------------------------------------------------------------------ */
	QFrame#Sidebar {{ background: {t.sidebar}; border-right: 1px solid {t.border}; }}
	QLabel#AppName {{ font-size: 15px; font-weight: 700; }}
	QLabel#AppTagline {{ color: {t.muted}; font-size: 11px; }}
	QPushButton#NavButton {{
		text-align: left; padding: 8px 12px; border: none; border-radius: 8px;
		background: transparent; color: {t.muted}; font-weight: 500;
	}}
	QPushButton#NavButton:hover {{ background: {t.border}; color: {t.text}; }}
	QPushButton#NavButton:checked {{ background: {t.surface}; color: {t.text}; font-weight: 600; }}
	QLabel#SidebarFooter {{ color: {t.faint}; font-size: 11px; }}

	/* Typography --------------------------------------------------------------- */
	QLabel#PageTitle {{ font-size: 22px; font-weight: 700; }}
	QLabel#PageSubtitle {{ color: {t.muted}; font-size: 13px; }}
	QLabel#CardTitle {{ font-size: 14px; font-weight: 600; }}
	QLabel#EmptyTitle {{ font-size: 16px; font-weight: 600; }}
	QLabel#SectionLabel {{
		color: {t.muted}; font-size: 11px; font-weight: 600; letter-spacing: 0.6px;
	}}
	QLabel#Muted {{ color: {t.muted}; }}
	QLabel#Faint {{ color: {t.faint}; }}
	QLabel#Hint {{ color: {t.muted}; font-size: 12px; }}
	QLabel#Error {{ color: {t.danger}; font-size: 12px; }}
	QLabel#PathExample {{
		background: {t.surface_alt}; border: 1px solid {t.border}; border-radius: 7px;
		padding: 7px 9px; color: {t.muted}; font-family: "{mono}";
		font-size: 11px;
	}}

	/* Cards -------------------------------------------------------------------- */
	QFrame#Card {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px; }}
	QFrame#FolderCard {{
		background: {t.surface}; border: 1px solid {t.border}; border-radius: 12px;
	}}
	QFrame#FolderCard:hover {{ border-color: {t.border_strong}; }}
	QFrame#FolderCard[dropping="true"] {{
		border: 2px dashed {t.accent}; background: {t.accent_soft};
	}}
	QFrame#FolderCard[empty="true"] {{ border-style: dashed; border-color: {t.border_strong}; }}
	QLabel#FolderPath {{ font-size: 14px; font-weight: 600; }}
	QLabel#FolderPlaceholder {{ font-size: 14px; color: {t.muted}; }}

	/* Buttons ------------------------------------------------------------------ */
	QPushButton {{
		background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 8px;
		padding: 6px 14px; font-weight: 500;
	}}
	QPushButton:hover {{ background: {t.surface_alt}; border-color: {t.faint}; }}
	QPushButton:pressed {{ background: {t.border}; }}
	QPushButton:disabled {{ color: {t.faint}; border-color: {t.border}; }}
	QPushButton#Primary {{
		background: {t.accent}; color: {t.accent_text}; border: none; padding: 9px 20px;
		font-weight: 600; font-size: 13px;
	}}
	QPushButton#Primary:hover {{ background: {t.accent_hover}; }}
	QPushButton#Primary:disabled {{ background: {t.border}; color: {t.faint}; }}
	QPushButton#Danger {{
		background: {t.danger}; color: #ffffff; border: none; padding: 9px 20px; font-weight: 600;
	}}
	QPushButton#Danger:hover {{ background: {t.danger}; border: 1px solid {t.danger_soft}; }}
	QPushButton#Danger:disabled {{ background: {t.border}; color: {t.faint}; }}
	QPushButton#Ghost {{ background: transparent; border: none; color: {t.accent}; padding: 4px 8px; }}
	QPushButton#Ghost:hover {{ background: {t.accent_soft}; }}
	QPushButton#Ghost:disabled {{ color: {t.faint}; }}
	QPushButton#Small {{ padding: 4px 10px; font-size: 12px; }}

	QToolButton#Chip {{
		background: {t.surface}; border: 1px solid {t.border}; border-radius: 9px;
		padding: 7px 10px; text-align: left; color: {t.muted}; font-weight: 500;
	}}
	QToolButton#Chip:hover {{ border-color: {t.border_strong}; color: {t.text}; }}
	QToolButton#Chip:checked {{
		background: {t.accent_soft}; border: 1px solid {t.accent}; color: {t.text}; font-weight: 600;
	}}
	QToolButton#Chip:disabled {{ color: {t.faint}; background: {t.surface_alt}; }}

	QFrame#Segmented {{
		background: {t.surface_alt}; border: 1px solid {t.border}; border-radius: 9px;
	}}
	QFrame#Segmented QPushButton {{
		border: none; background: transparent; border-radius: 7px; padding: 6px 12px;
		color: {t.muted}; font-weight: 500;
	}}
	QFrame#Segmented QPushButton:checked {{
		background: {t.surface}; color: {t.text}; font-weight: 600; border: 1px solid {t.border};
	}}

	/* Inputs ------------------------------------------------------------------- */
	QLineEdit, QComboBox, QSpinBox {{
		background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 7px;
		padding: 5px 8px; selection-background-color: {t.selection}; selection-color: {t.text};
	}}
	QLineEdit:focus, QComboBox:focus {{ border: 1px solid {t.accent}; }}
	QLineEdit:disabled, QComboBox:disabled {{ color: {t.faint}; background: {t.surface_alt}; }}
	QLineEdit#Search {{ padding-left: 8px; border-radius: 8px; background: {t.surface_alt}; }}
	QComboBox {{ padding-right: 22px; }}
	QComboBox::drop-down {{ border: none; width: 24px; subcontrol-position: center right; }}
	QComboBox::down-arrow {{ image: url({ind["chevron"]}); width: 14px; height: 14px; }}
	QComboBox::down-arrow:disabled {{ image: url({ind["chevron_disabled"]}); }}
	QComboBox QAbstractItemView {{
		background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 6px;
		selection-background-color: {t.selection}; selection-color: {t.text}; padding: 4px;
	}}

	/* Table -------------------------------------------------------------------- */
	QTableView {{
		background: {t.surface}; border: none; gridline-color: transparent;
		selection-background-color: {t.selection}; selection-color: {t.text};
		alternate-background-color: {t.surface_alt};
	}}
	QTableView::item {{ padding: 0 6px; border: none; }}
	QHeaderView {{ background: {t.surface}; border: none; }}
	QHeaderView::section {{
		background: {t.surface}; color: {t.muted}; border: none;
		border-bottom: 1px solid {t.border}; padding: 6px 6px; font-size: 11px; font-weight: 600;
	}}
	QTableCornerButton::section {{ background: {t.surface}; border: none; }}
	QTableView::indicator, QCheckBox::indicator {{ width: 16px; height: 16px; }}
	QTableView::indicator:unchecked, QCheckBox::indicator:unchecked {{ image: url({ind["unchecked"]}); }}
	QTableView::indicator:checked, QCheckBox::indicator:checked {{ image: url({ind["checked"]}); }}
	QTableView::indicator:checked:disabled, QCheckBox::indicator:checked:disabled {{
		image: url({ind["checked_disabled"]});
	}}
	QTableView::indicator:unchecked:disabled, QCheckBox::indicator:unchecked:disabled {{
		image: url({ind["unchecked_disabled"]});
	}}

	/* Scrollbars --------------------------------------------------------------- */
	QScrollArea {{ background: transparent; border: none; }}
	QScrollArea > QWidget > QWidget {{ background: transparent; }}
	QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
	QScrollBar::handle:vertical {{ background: {t.border_strong}; border-radius: 3px; min-height: 30px; }}
	QScrollBar::handle:vertical:hover {{ background: {t.faint}; }}
	QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
	QScrollBar::handle:horizontal {{ background: {t.border_strong}; border-radius: 3px; min-width: 30px; }}
	QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
	QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

	/* Progress & action bar ---------------------------------------------------- */
	QFrame#ActionBar {{ background: {t.surface}; border-top: 1px solid {t.border}; }}
	QProgressBar {{
		background: {t.border}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px;
	}}
	QProgressBar::chunk {{ background: {t.accent}; border-radius: 3px; }}

	/* Banners ------------------------------------------------------------------ */
	QFrame#Banner {{ border-radius: 10px; border: 1px solid {t.border}; background: {t.surface}; }}
	QFrame#Banner[tone="success"] {{ background: {t.success_soft}; border-color: {t.success_soft}; }}
	QFrame#Banner[tone="warning"] {{ background: {t.warning_soft}; border-color: {t.warning_soft}; }}
	QFrame#Banner[tone="danger"] {{ background: {t.danger_soft}; border-color: {t.danger_soft}; }}
	QLabel#BannerText {{ font-weight: 500; }}

	QFrame#Tile {{ background: {t.surface_alt}; border: 1px solid {t.border}; border-radius: 10px; }}
	QFrame#Tile:hover {{ border-color: {t.border_strong}; }}
	QFrame#Tile[marked="true"] {{ background: {t.danger_soft}; border: 1px solid {t.danger}; }}
	QLabel#Thumb {{ background: {t.border}; border-radius: 6px; color: {t.faint}; font-size: 11px; }}
	QLabel#TileName {{ font-weight: 600; }}

	QLabel#Pill {{
		border-radius: 9px; padding: 2px 8px; font-size: 11px; font-weight: 600;
		background: {t.border}; color: {t.muted};
	}}
	QLabel#Pill[tone="success"] {{ background: {t.success_soft}; color: {t.success}; }}
	QLabel#Pill[tone="warning"] {{ background: {t.warning_soft}; color: {t.warning}; }}
	QLabel#Pill[tone="danger"] {{ background: {t.danger_soft}; color: {t.danger}; }}
	QLabel#Pill[tone="accent"] {{ background: {t.accent_soft}; color: {t.accent}; }}

	QMenu {{
		background: {t.surface}; border: 1px solid {t.border_strong}; border-radius: 8px; padding: 4px;
	}}
	QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
	QMenu::item:selected {{ background: {t.selection}; }}
	QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 6px; }}
	"""


def apply_theme(app: QApplication, dark: bool | None = None) -> Tokens:
	"""Apply the light or dark theme (system choice when ``dark`` is None)."""
	global _current
	_current = DARK if (system_prefers_dark() if dark is None else dark) else LIGHT
	app.setPalette(_palette(_current))
	app.setStyleSheet(stylesheet(_current))
	return _current
