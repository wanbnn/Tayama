"""Ícones vetoriais (Material Design Icons via qtawesome), compatíveis com PyQt6."""
import os
os.environ.setdefault("QT_API", "pyqt6")
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon
try:
    import qtawesome as qta
except Exception:  # sem qtawesome: cai para texto
    qta = None


def icon(name, color="#8b949e", active="#ffffff"):
    if qta is None: return QIcon()
    try: return qta.icon(f"mdi6.{name}", color=color, color_active=active)
    except Exception: return QIcon()


def style_button(btn, name, fallback="", size=16, **kw):
    ic = icon(name, **kw)
    if ic.isNull(): btn.setText(fallback)
    else: btn.setText(""); btn.setIcon(ic); btn.setIconSize(QSize(size, size))
