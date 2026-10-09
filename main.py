import sys, subprocess
from PyQt6.QtWidgets import QApplication, QMainWindow, QToolBar, QInputDialog, QMessageBox, QWidget, QHBoxLayout, QToolButton, QSizePolicy
from PyQt6.QtCore import QSize, Qt, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QAction
from tayama import config, workspaces
from tayama.sidebar import Sidebar
from tayama.icons import icon
from tayama.ui import InfiniteCanvas, NewTerminalDialog, ConfigDialog, WorkspaceDialog
from tayama.bridge import Bridge


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        config.ensure()
        self.setWindowTitle("Tayama – Gerenciador de agentes CLI"); self.resize(1400, 800)
        self.canvas = InfiniteCanvas(self); self.canvas.setFrameShape(InfiniteCanvas.Shape.NoFrame)
        self.sidebar = Sidebar(self.canvas); self.sidebar.setMaximumWidth(self.SIDE_W)
        self.sidebar.new_agent.connect(self.new_agent); self.sidebar.new_workspace.connect(self.new_workspace)
        self.rail = QToolButton(); self.rail.setObjectName("rail"); self.rail.setFixedWidth(18); self.rail.setToolTip("Mostrar/ocultar workspaces (Ctrl+B)")
        self.rail.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding); self.rail.clicked.connect(self.toggle_sidebar)
        self.anim = QPropertyAnimation(self.sidebar, b"maximumWidth", self); self.anim.setDuration(180); self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        central = QWidget(); h = QHBoxLayout(central); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        h.addWidget(self.canvas, 1); h.addWidget(self.rail); h.addWidget(self.sidebar); self.setCentralWidget(central)
        self._open = True; self._rail_icon()
        self.bridge = Bridge(self.canvas)
        tb = QToolBar("Ferramentas"); tb.setMovable(False); self.addToolBar(tb)
        for ic, text, fn in (("plus", "Novo agente", lambda: self.new_agent()), ("bullhorn-outline", "Enviar a todos", self.broadcast),
                             ("dock-right", "Workspaces", self.toggle_sidebar), ("cog-outline", "Agentes/Cargos/Skills", self.edit_cfg), ("folder-open-outline", "Pasta de skills", self.open_skills),
                             ("crosshairs-gps", "Centralizar", lambda: self.canvas.centerOn(0, 0))):
            act = QAction(icon(ic, "#c9d1d9", "#ffffff"), text, self); act.triggered.connect(fn); tb.addAction(act)
        tb.setIconSize(QSize(18, 18)); tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.statusBar().showMessage("Ícone de link: conecta terminais (origem → destino) · botão direito na seta remove · "
                                     "Ctrl+scroll zoom · botão do meio/Espaço+arrastar move a tela")

    SIDE_W = 280

    def _rail_icon(self):
        self.rail.setIcon(icon("chevron-right" if self._open else "chevron-left", "#8b949e")); self.rail.setIconSize(QSize(14, 14))

    def toggle_sidebar(self):
        self._open = not self._open; self._rail_icon()
        self.anim.stop(); self.anim.setStartValue(self.sidebar.maximumWidth()); self.anim.setEndValue(self.SIDE_W if self._open else 0); self.anim.start()

    def new_workspace(self):
        d = WorkspaceDialog(self)
        if d.exec(): workspaces.add(*d.value()); self.sidebar.refresh(); return True
        return False

    def new_agent(self, ws=None):
        wss = workspaces.load()
        if not wss:
            if not self.new_workspace(): return
            wss = workspaces.load()
        d = NewTerminalDialog(config.load(), wss, ws["id"] if isinstance(ws, dict) else None, self)
        if d.exec(): self.canvas.add_terminal(d.spec())

    def broadcast(self):
        text, ok = QInputDialog.getMultiLineText(self, "Enviar a todos", "Mensagem para todos os terminais:")
        if ok and text.strip():
            for w in self.canvas.windows.values(): w.terminal.send_text(f"[Tayama/usuário] {text}")

    def edit_cfg(self): ConfigDialog(self).exec()

    def open_skills(self):
        try: subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", str(config.SKILLS)])
        except OSError: QMessageBox.information(self, "Skills", str(config.SKILLS))


QSS = """
QMainWindow, QDialog { background:#0d1117; }
QToolBar { background:#161b22; border:none; border-bottom:1px solid #30363d; padding:6px; spacing:4px; }
QToolButton { color:#e6edf3; padding:6px 12px; border-radius:7px; font-size:10pt; }
QToolButton:hover { background:#262c36; }
QStatusBar { background:#161b22; color:#8b949e; border-top:1px solid #30363d; }
QLabel, QCheckBox { color:#e6edf3; }
QWidget#sidebar { background:#0f141b; border-left:1px solid #30363d; }
QToolButton#rail { background:#161b22; border:none; border-left:1px solid #30363d; border-radius:0; padding:0; } QToolButton#rail:hover { background:#262c36; }
QTreeWidget { background:transparent; color:#e6edf3; outline:0; } QTreeWidget::item { padding:5px 4px; border-radius:6px; }
QTreeWidget::item:hover { background:#1b222c; } QTreeWidget::item:selected { background:#1f2a3a; color:#fff; }
QLineEdit, QComboBox, QListWidget, QPlainTextEdit { background:#0d1117; color:#e6edf3; border:1px solid #30363d; border-radius:6px; padding:5px; selection-background-color:#1f6feb; }
QComboBox QAbstractItemView { background:#161b22; color:#e6edf3; selection-background-color:#1f6feb; }
QPushButton { background:#238636; color:white; border:none; border-radius:6px; padding:6px 16px; }
QPushButton:hover { background:#2ea043; }
QMenu { background:#161b22; color:#e6edf3; border:1px solid #30363d; padding:4px; } QMenu::item { padding:6px 18px; border-radius:4px; } QMenu::item:selected { background:#1f6feb; }
"""

if __name__ == "__main__":
    app = QApplication(sys.argv); app.setStyle("Fusion"); app.setStyleSheet(QSS); w = MainWindow(); w.show(); sys.exit(app.exec())
