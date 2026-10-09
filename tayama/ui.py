"""Janelas flutuantes, conexões (setas), canvas infinito e diálogos."""
import math, os, shlex, uuid
from PyQt6.QtCore import Qt, QPointF, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QPainterPath
from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPathItem, QFrame, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QMenu, QDialog, QFormLayout, QLineEdit, QComboBox, QListWidget,
    QListWidgetItem, QDialogButtonBox, QFileDialog, QPlainTextEdit, QMessageBox)
from . import config, trust
from .icons import icon, style_button
from .terminal import PtyTerminal

BTN = "QPushButton{background:transparent;color:#8b949e;border:none;font-size:11pt;border-radius:6px;}QPushButton:hover{background:#30363d;color:#fff;}"


class ResizeGrip(QLabel):
    def __init__(self, target):
        super().__init__("◢"); self.target = target
        self.setFixedSize(16, 16); self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setStyleSheet("color:#666;font-size:11px;"); self._p = None

    def mousePressEvent(self, e):
        self._p = e.globalPosition().toPoint(); self._s = self.target.size(); e.accept()

    def mouseMoveEvent(self, e):
        if self._p is None: return
        d = e.globalPosition().toPoint() - self._p
        self.target.resize(max(self.target.minimumWidth(), self._s.width() + d.x()),
                           max(self.target.minimumHeight(), self._s.height() + d.y()))
        self.target.update_status(); e.accept()

    def mouseReleaseEvent(self, e): self._p = None; e.accept()


class FloatingWindow(QFrame):
    def __init__(self, canvas, spec):
        super().__init__()
        self.canvas, self.id = canvas, uuid.uuid4().hex[:8]
        self.name, self.agent = spec["name"], spec["agent"]
        self.role, self.skills = spec.get("role"), spec.get("skills", [])
        self.workspace = spec.get("workspace") or {}
        self.cwd = spec.get("cwd") or os.path.expanduser("~")
        self.proxy = None; self._drag = None
        self.role_name = self.role["name"] if self.role else "sem cargo"
        color = self.role["color"] if self.role else "#555555"
        self.setObjectName("win"); self.setMinimumSize(360, 220); self.resize(620, 380)
        self.setStyleSheet(f"QFrame#win{{background:#121212;border:1px solid {color};border-radius:10px;}}")

        lay = QVBoxLayout(self); lay.setContentsMargins(1, 1, 1, 1); lay.setSpacing(0)
        bar = QWidget(); bar.setFixedHeight(36); bar.setObjectName("bar"); bar.setStyleSheet("QWidget#bar{background:#1c2028;border-top-left-radius:9px;border-top-right-radius:9px;}")
        bl = QHBoxLayout(bar); bl.setContentsMargins(12, 0, 8, 0); bl.setSpacing(8)
        self.dot = QLabel("●"); self.dot.setStyleSheet("color:#3fb950;font-size:9px;")
        self.title = QLabel(self.name); self.title.setStyleSheet("color:#e6edf3;font-weight:600;font-size:10pt;")
        chip = QLabel(self.role_name); chip.setStyleSheet(f"background:{color};color:#101114;border-radius:8px;padding:0 9px;font-size:8pt;font-weight:700;"); chip.setFixedHeight(18)
        ag = QLabel(self.agent["name"] + (f"  ·  {self.workspace['name']}" if self.workspace else "")); ag.setStyleSheet("color:#7d8590;font-size:8.5pt;")
        for w_ in (self.dot, self.title, chip, ag): bl.addWidget(w_)
        bl.addStretch()
        for ic, fb, tip, fn in (("clipboard-text-outline", "B", "Reenviar briefing (cargo + skills)", self.send_briefing),
                                ("link-variant", "L", "Conectar a outro terminal (clique aqui e depois no ícone de link do destino)", lambda: canvas.start_link(self)),
                                ("close", "X", "Fechar", self.close_window)):
            b = QPushButton(); b.setFixedSize(26, 26); b.setToolTip(tip); b.setStyleSheet(BTN)
            style_button(b, ic, fb); b.clicked.connect(fn); bl.addWidget(b)
            if ic == "link-variant": self.link_btn = b
        lay.addWidget(bar)

        env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor", TAYAMA_ID=self.id,
                   TAYAMA_NAME=self.name, TAYAMA_ROLE=self.role_name, TAYAMA_SOCK=config.SOCK,
                   PATH=config.BIN + os.pathsep + os.environ.get("PATH", ""))
        os.environ["SHELL"] = os.environ.get("SHELL") or "/bin/bash"
        qenv, qargs = trust.quiet(self.agent) if self.agent.get("no_update_prompts", True) else ({}, [])
        env.update(qenv); args = qargs + list(self.agent.get("args", []))   # sem avisos/instalação de updates
        if self.agent.get("login_shell", self.agent["command"].strip() != "$SHELL"):
            # Roda via shell de login interativo (zsh/bash) para carregar .zshrc/.zprofile e achar o PATH do usuário
            cmd = " ".join([self.agent["command"]] + [shlex.quote(a) for a in args])
            argv = [os.environ["SHELL"], "-l", "-i", "-c", "exec " + cmd]
        else:
            argv = [os.path.expandvars(self.agent["command"])] + args
        if self.agent.get("auto_trust", True):
            try: trust.ensure(self.agent, self.cwd)
            except Exception as e: print("Tayama: não foi possível pré-aprovar a pasta:", e)
        self.terminal = PtyTerminal(argv, self.cwd, env, self)
        self.terminal.finished.connect(self._ended)
        lay.addWidget(self.terminal)

        foot = QWidget(); foot.setFixedHeight(20); foot.setObjectName("foot"); foot.setStyleSheet("QWidget#foot{background:#1c2028;border-bottom-left-radius:9px;border-bottom-right-radius:9px;}")
        fl = QHBoxLayout(foot); fl.setContentsMargins(8, 0, 2, 0)
        self.dim = QLabel("80x24"); self.dim.setStyleSheet("color:#6e7681;font-size:8pt;")
        fl.addWidget(self.dim); fl.addStretch(); fl.addWidget(ResizeGrip(self)); lay.addWidget(foot)

        bar.mousePressEvent, bar.mouseMoveEvent, bar.mouseReleaseEvent = self._tp, self._tm, self._tr
        if self.agent.get("auto_briefing", True):
            QTimer.singleShot(int(self.agent.get("startup_delay_ms", 5000)), self.send_briefing)

    def _ended(self): self.dot.setStyleSheet("color:#f85149;font-size:9px;"); self.canvas.changed.emit()

    def update_status(self): self.dim.setText(f"{self.terminal.cols}x{self.terminal.rows}")
    def resizeEvent(self, e): super().resizeEvent(e); self.update_status()

    def _tp(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.proxy:
            self._drag = e.globalPosition().toPoint(); self._start = self.proxy.pos(); e.accept()

    def _tm(self, e):
        if self._drag is not None:
            d = e.globalPosition().toPoint() - self._drag
            self.proxy.setPos(self._start + QPointF(d.x(), d.y())); e.accept()

    def _tr(self, e): self._drag = None; e.accept()

    # --- comunicação ---------------------------------------------------
    def briefing(self) -> str:
        peers = self.canvas.peers_of(self)
        out = [f'Você é "{self.name}", cargo "{self.role_name}", em uma equipe de agentes CLI coordenada pelo Tayama.']
        if self.role: out.append(self.role["prompt"])
        if self.skills:
            out.append("## Skills")
            out += [f"### {s['name']}\n{s['text']}" for s in self.skills]
        out.append('## Comunicação\nUse estes comandos de shell para falar com os terminais conectados a você:\n'
                   '- tayama peers\n- tayama send <nome-ou-cargo> "mensagem"\n- tayama broadcast "mensagem"\n'
                   'Mensagens recebidas chegam no seu input no formato "[Tayama de <nome> (<cargo>)] texto".')
        out.append("Conectados agora: " + (", ".join(f"{p.name} ({p.role_name})" for p in peers) or "ninguém ainda") + ".")
        out.append("Confirme em uma linha que entendeu e aguarde instruções.")
        return "\n\n".join(out)

    def send_briefing(self): self.terminal.send_text(self.briefing())

    def receive(self, src, text):
        self.terminal.send_text(f"[Tayama de {src.name} ({src.role_name})] {text}")

    def close_window(self):
        self.terminal.terminate(); self.canvas.remove_window(self); self.deleteLater()


class Edge(QGraphicsPathItem):
    def __init__(self, canvas, src, dst):
        super().__init__(); self.canvas, self.src, self.dst = canvas, src, dst
        col = QColor(src.role["color"] if src.role else "#8b949e")
        self.setPen(QPen(col, 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)); self.setZValue(-1)
        self.arrow = QGraphicsPathItem(self); self.arrow.setBrush(QBrush(col)); self.arrow.setPen(QPen(Qt.PenStyle.NoPen))
        self.setToolTip(f"{src.name} → {dst.name} (botão direito para remover)")
        self.refresh()

    def refresh(self):
        a, b = self.src.proxy.sceneBoundingRect().center(), self.dst.proxy.sceneBoundingRect().center()
        dx = max(90.0, abs(b.x() - a.x()) / 2) * (1 if b.x() >= a.x() else -1)
        c1, c2 = a + QPointF(dx, 0), b - QPointF(dx, 0)
        p = QPainterPath(a); p.cubicTo(c1, c2, b); self.setPath(p)
        m = (a + 3 * c1 + 3 * c2 + b) / 8; t = 0.75 * (c2 + b - c1 - a)
        L = math.hypot(t.x(), t.y()) or 1; u = QPointF(t.x() / L, t.y() / L); n = QPointF(-u.y(), u.x())
        ar = QPainterPath(); ar.addPolygon(QPolygonF([m + u * 13, m - u * 9 + n * 9, m - u * 9 - n * 9])); self.arrow.setPath(ar)

    def contextMenuEvent(self, e):
        menu = QMenu(); act = menu.addAction("Remover conexão")
        if menu.exec(e.screenPos()) == act: self.canvas.remove_edge(self)


class InfiniteCanvas(QGraphicsView):
    changed = pyqtSignal()   # terminais adicionados/removidos/encerrados

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gscene = QGraphicsScene(self); self.setScene(self.gscene)
        self.gscene.setSceneRect(-50000, -50000, 100000, 100000)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.windows, self.edges = {}, []
        self._link_src = None; self._panning = False; self._space = False; self._n = 0
        t = QTimer(self, interval=33); t.timeout.connect(lambda: [e.refresh() for e in self.edges]); t.start()

    def drawBackground(self, p, rect):
        p.fillRect(rect, QColor("#0d1117")); g = 32
        p.setPen(QPen(QColor("#2a303b"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for x in range(int(rect.left()) - int(rect.left()) % g, int(rect.right()), g):
            for y in range(int(rect.top()) - int(rect.top()) % g, int(rect.bottom()), g): p.drawPoint(x, y)

    # --- terminais e conexões -----------------------------------------
    def add_terminal(self, spec):
        win = FloatingWindow(self, spec); proxy = self.gscene.addWidget(win); win.proxy = proxy
        c = self.mapToScene(self.viewport().rect().center()); off = 30 * (self._n % 8); self._n += 1
        proxy.setPos(c.x() - win.width() / 2 + off, c.y() - win.height() / 2 + off)
        self.windows[win.id] = win; self.changed.emit(); return win

    def remove_window(self, win):
        for e in [e for e in self.edges if win in (e.src, e.dst)]: self.remove_edge(e)
        self.windows.pop(win.id, None)
        if win.proxy and win.proxy.scene(): self.gscene.removeItem(win.proxy)
        self.changed.emit()

    def peers_of(self, win): return [e.dst for e in self.edges if e.src is win]

    def start_link(self, win):
        if self._link_src is None:
            self._link_src = win; win.link_btn.setStyleSheet(BTN + "QPushButton{background:#e5a400;}")
            win.link_btn.setIcon(icon("link-variant", "#101114")); return
        src, self._link_src = self._link_src, None
        src.link_btn.setStyleSheet(BTN); src.link_btn.setIcon(icon("link-variant"))
        if src is not win and not any(e.src is src and e.dst is win for e in self.edges):
            e = Edge(self, src, win); self.gscene.addItem(e); self.edges.append(e)

    def remove_edge(self, e):
        if e in self.edges: self.edges.remove(e)
        self.gscene.removeItem(e)

    # --- navegação -----------------------------------------------------
    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat(): self._space = True; self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat(): self._space = False; self.setCursor(Qt.CursorShape.ArrowCursor)
        super().keyReleaseEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton or (e.button() == Qt.MouseButton.LeftButton and self._space):
            self._panning = True; self._pp = e.position(); self.setCursor(Qt.CursorShape.ClosedHandCursor); e.accept()
        else: super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._panning:
            d = e.position() - self._pp; self._pp = e.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y())); e.accept()
        else: super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._panning:
            self._panning = False; self.setCursor(Qt.CursorShape.ArrowCursor); e.accept()
        else: super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        # Ctrl+scroll = zoom; scroll normal vai para o terminal sob o mouse
        if not (e.modifiers() & Qt.KeyboardModifier.ControlModifier): return super().wheelEvent(e)
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15; z = self.transform().m11()
        if (z > 4 and f > 1) or (z < 0.2 and f < 1): return
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse); self.scale(f, f)


class WorkspaceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("Novo workspace"); self.resize(500, 150)
        f = QFormLayout(self)
        self.name = QLineEdit(); self.name.setPlaceholderText("ex.: meu-projeto")
        self.path = QLineEdit(); self.path.setPlaceholderText("pasta onde os agentes vão trabalhar")
        br = QPushButton(); br.setFixedWidth(34); style_button(br, "folder-open-outline", "...", color="#ffffff"); br.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self.path); row.addWidget(br)
        f.addRow("Nome", self.name); f.addRow("Diretório", row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject); f.addRow(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Diretório de trabalho", self.path.text() or os.path.expanduser("~"))
        if d:
            self.path.setText(d)
            if not self.name.text().strip(): self.name.setText(os.path.basename(d))

    def _ok(self):
        if not os.path.isdir(os.path.expanduser(self.path.text().strip())):
            QMessageBox.warning(self, "Diretório inválido", "Escolha uma pasta que exista."); return
        self.accept()

    def value(self):
        p = os.path.abspath(os.path.expanduser(self.path.text().strip()))
        return self.name.text().strip() or os.path.basename(p) or p, p


class NewTerminalDialog(QDialog):
    def __init__(self, cfg, wss, current=None, parent=None):
        super().__init__(parent); self.cfg, self.wss = cfg, wss; self.setWindowTitle("Novo agente"); self.resize(480, 460)
        f = QFormLayout(self)
        self.name = QLineEdit(); self.name.setPlaceholderText("ex.: lider, dev-1, qa (usado nos comandos send)")
        self.ws = QComboBox()
        for w in wss: self.ws.addItem(f"{w['name']}   —   {w['path']}")
        self.ws.setCurrentIndex(next((i for i, w in enumerate(wss) if w["id"] == current), 0))
        self.agent = QComboBox(); self.agent.addItems([a["name"] for a in cfg["agents"]])
        self.role = QComboBox(); self.role.addItem("(sem cargo)"); self.role.addItems([r["name"] for r in cfg["roles"]])
        self.skills = QListWidget()
        for sk in cfg["skills"]:
            it = QListWidgetItem(sk["name"]); it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked); self.skills.addItem(it)
        for lbl, w in (("Workspace", self.ws), ("Nome", self.name), ("Agente CLI", self.agent), ("Cargo", self.role), ("Skills", self.skills)):
            f.addRow(lbl, w)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject); f.addRow(bb)

    def spec(self):
        i = self.role.currentIndex(); ws = self.wss[self.ws.currentIndex()]
        return {"name": self.name.text().strip() or f"{self.agent.currentText().split()[0].lower()}-{uuid.uuid4().hex[:3]}",
                "agent": self.cfg["agents"][self.agent.currentIndex()],
                "role": self.cfg["roles"][i - 1] if i > 0 else None,
                "workspace": ws, "cwd": ws["path"],
                "skills": [self.cfg["skills"][k] for k in range(self.skills.count())
                           if self.skills.item(k).checkState() == Qt.CheckState.Checked]}


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("Agentes, cargos e skills (JSON)"); self.resize(720, 560)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"Edite livremente. Skills em .md também são lidas de: {config.SKILLS}"))
        self.ed = QPlainTextEdit(config.raw()); self.ed.setStyleSheet("font-family:monospace;"); v.addWidget(self.ed)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.save); bb.rejected.connect(self.reject); v.addWidget(bb)

    def save(self):
        try: config.save_raw(self.ed.toPlainText()); self.accept()
        except ValueError as e: QMessageBox.warning(self, "JSON inválido", str(e))
