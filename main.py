import sys, subprocess
from PyQt6.QtWidgets import QApplication, QMainWindow, QToolBar, QInputDialog, QMessageBox, QWidget, QHBoxLayout, QToolButton, QSizePolicy
from PyQt6.QtCore import QSize, Qt, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QAction
# O QtWebEngine exige que QtWebEngineWidgets seja importado ANTES do QApplication.
# Este import também puxa tayama/browser.py, garantindo isso.
import tayama.browser  # noqa: F401
from tayama import config, workspaces
from tayama.sidebar import Sidebar
from tayama.icons import icon
from tayama.ui import InfiniteCanvas, NewTerminalDialog, ConfigDialog, WorkspaceDialog
from tayama.bridge import Bridge


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        config.ensure()
        self.setWindowTitle("Tayama — Agent Workspace")
        self.resize(1440, 900)
        self.setMinimumSize(900, 600)

        self.canvas = InfiniteCanvas(self)
        self.canvas.setFrameShape(InfiniteCanvas.Shape.NoFrame)
        self.canvas.setObjectName("canvas")

        self.sidebar = Sidebar(self.canvas)
        self.sidebar.setMaximumWidth(self.SIDE_W)
        self.sidebar.new_agent.connect(self.new_agent)
        self.sidebar.new_workspace.connect(self.new_workspace)
        self.sidebar.edit_workspace.connect(self.edit_workspace)
        self.sidebar.duplicate_workspace.connect(self.duplicate_workspace)

        self.rail = QToolButton()
        self.rail.setObjectName("rail")
        self.rail.setFixedWidth(24)
        self.rail.setToolTip("Mostrar/ocultar workspaces (Ctrl+B)")
        self.rail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.rail.clicked.connect(self.toggle_sidebar)

        self.anim = QPropertyAnimation(self.sidebar, b"maximumWidth", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        central = QWidget()
        central.setObjectName("appSurface")
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self.canvas, 1)
        h.addWidget(self.rail)
        h.addWidget(self.sidebar)
        self.setCentralWidget(central)

        self._open = True
        self._rail_icon()
        self.bridge = Bridge(self.canvas)
        self.canvas.restore_agents()
        self.canvas.restore_panels()

        tb = QToolBar("Workspace")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(17, 17))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        actions = (
            ("plus", "Novo agente", lambda: self.new_agent()),
            ("earth", "Painel web", self.new_panel),
            ("bullhorn-outline", "Enviar a todos", self.broadcast),
            ("dock-right", "Workspaces", self.toggle_sidebar),
            ("cog-outline", "Agentes, cargos e skills", self.edit_cfg),
            ("folder-open-outline", "Pasta de skills", self.open_skills),
            ("crosshairs-gps", "Centralizar canvas", lambda: self.canvas.centerOn(0, 0)),
        )
        for ic, label, fn in actions:
            act = QAction(icon(ic, "#a5b4fc", "#f8fafc"), label, self)
            act.triggered.connect(fn)
            tb.addAction(act)

        self.statusBar().showMessage(
            "Conexões: clique no link de origem e depois no destino  ·  "
            "Ctrl + scroll: zoom  ·  Espaço + arrastar: mover canvas"
        )

    SIDE_W = 292

    def _rail_icon(self):
        self.rail.setIcon(icon("chevron-right" if self._open else "chevron-left", "#94a3b8"))
        self.rail.setIconSize(QSize(13, 13))

    def toggle_sidebar(self):
        self._open = not self._open
        self._rail_icon()
        self.anim.stop()
        self.anim.setStartValue(self.sidebar.maximumWidth())
        self.anim.setEndValue(self.SIDE_W if self._open else 0)
        self.anim.start()

    def new_workspace(self):
        d = WorkspaceDialog(parent=self)
        if d.exec():
            ws = workspaces.add(*d.value())
            self.canvas.set_current_ws(ws)      # painéis futuros nascem aqui
            self.sidebar.refresh()
            return True
        return False

    def edit_workspace(self, ws):
        d = WorkspaceDialog(ws, self)
        if not d.exec(): return
        updated = workspaces.update(ws["id"], *d.value())
        if not updated: return
        # janelas/painéis abertos guardam o dict antigo: atualizamos ao vivo,
        # senão o nome novo só apareceria no label e na persistência após reiniciar.
        for w in self.canvas.windows.values():
            if w.workspace.get("id") == ws["id"]:
                w.workspace = updated; w.refresh_agent_label()
        for p in self.canvas.panels.values():
            if (getattr(p, "workspace", None) or {}).get("id") == ws["id"]: p.workspace = updated
        if self.canvas.current_ws and self.canvas.current_ws.get("id") == ws["id"]:
            self.canvas.current_ws = updated   # painéis futuros continuam nascendo aqui
        self.sidebar.refresh()

    def duplicate_workspace(self, ws):
        workspaces.duplicate(ws)
        self.sidebar.refresh()     # o corrente não muda: duplicar não deve trocar o foco

    def new_agent(self, ws=None):
        wss = workspaces.load()
        if not wss:
            if not self.new_workspace():
                return
            wss = workspaces.load()
        d = NewTerminalDialog(config.load(), wss, ws["id"] if isinstance(ws, dict) else None, self)
        if d.exec():
            spec = d.spec()
            self.canvas.set_current_ws(spec["workspace"])
            self.canvas.add_terminal(spec)

    def new_panel(self):
        self.canvas.add_panel("about:blank")

    def broadcast(self):
        text, ok = QInputDialog.getMultiLineText(self, "Enviar a todos", "Mensagem para todos os terminais:")
        if ok and text.strip():
            for w in self.canvas.windows.values():
                w.terminal.send_text(f"[Tayama/usuário] {text}")

    def edit_cfg(self):
        ConfigDialog(self).exec()

    def open_skills(self):
        try:
            subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", str(config.SKILLS)])
        except OSError:
            QMessageBox.information(self, "Skills", str(config.SKILLS))


QSS = """
/* Tayama — graphite workspace */
* {
    font-family: "Inter", "SF Pro Display", "Segoe UI", sans-serif;
    outline: none;
}
QMainWindow, QDialog {
    background: #0b0d12;
    color: #e8ecf4;
}
QMainWindow::separator {
    background: #222735;
    width: 1px;
}
QToolBar#mainToolbar {
    background: #11141c;
    border: none;
    border-bottom: 1px solid #252a38;
    padding: 8px 12px;
    spacing: 7px;
}
QToolBar#mainToolbar QToolButton {
    color: #c5ccda;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 8px 11px;
    min-height: 20px;
    font-size: 9pt;
    font-weight: 500;
}
QToolBar#mainToolbar QToolButton:hover {
    color: #f8fafc;
    background: #202535;
    border-color: #30384b;
}
QToolBar#mainToolbar QToolButton:pressed {
    background: #293149;
    border-color: #5967a0;
}
QToolBar#mainToolbar QToolButton:checked {
    color: #c7d2fe;
    background: #202846;
}
QStatusBar {
    background: #10131a;
    color: #8993a8;
    border-top: 1px solid #252a38;
    font-size: 8pt;
}
QStatusBar::item { border: none; }
QLabel, QCheckBox, QRadioButton {
    color: #e2e8f0;
    spacing: 8px;
}
QWidget#sidebar {
    background: #10131a;
    border-left: 1px solid #252a38;
}
QToolButton#rail {
    background: #10131a;
    color: #94a3b8;
    border: none;
    border-left: 1px solid #252a38;
    border-radius: 0;
    padding: 0;
}
QToolButton#rail:hover { background: #202535; color: #e2e8f0; }
QTreeWidget {
    background: transparent;
    color: #d8deea;
    border: none;
    outline: 0;
    alternate-background-color: #141925;
}
QTreeWidget::item {
    padding: 7px 6px;
    border-radius: 7px;
    margin: 1px 3px;
}
QTreeWidget::item:hover { background: #1a2030; }
QTreeWidget::item:selected {
    background: #252e49;
    color: #f8fafc;
}
QLineEdit, QComboBox, QListWidget, QPlainTextEdit {
    background: #0d1017;
    color: #e8ecf4;
    border: 1px solid #303649;
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: #4f5fa8;
    selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus, QListWidget:focus, QPlainTextEdit:focus {
    border: 1px solid #7182d5;
    background: #10141e;
}
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView {
    background: #171b26;
    color: #e8ecf4;
    border: 1px solid #303649;
    selection-background-color: #303b60;
    padding: 5px;
}
QPushButton {
    background: #5969b8;
    color: #ffffff;
    border: 1px solid #7181d0;
    border-radius: 8px;
    padding: 8px 15px;
    font-weight: 600;
}
QPushButton:hover { background: #6879ca; }
QPushButton:pressed { background: #48579e; }
QPushButton:disabled { background: #252a38; color: #6f788b; border-color: #303649; }
QDialogButtonBox QPushButton { min-width: 78px; }
QMenu {
    background: #171b26;
    color: #e8ecf4;
    border: 1px solid #303649;
    border-radius: 10px;
    padding: 5px;
}
QMenu::item { padding: 8px 24px; border-radius: 6px; }
QMenu::item:selected { background: #303b60; color: #ffffff; }
QScrollBar:vertical {
    background: transparent;
    width: 9px;
    margin: 3px;
}
QScrollBar::handle:vertical {
    background: #3a4154;
    min-height: 28px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #59627b; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; border: none; }
QScrollBar:horizontal {
    background: transparent;
    height: 9px;
    margin: 3px;
}
QScrollBar::handle:horizontal { background: #3a4154; min-width: 28px; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; border: none; }
"""


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
