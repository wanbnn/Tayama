"""Painel web flutuante no canvas: Chromium embarcado (QtWebEngine).

Importa PyQt6.QtWebEngineWidgets no topo do módulo de propósito — o Qt exige
que o pacote seja importado *antes* da criação do QApplication, senão
`QWebEngineView` falha com "must be imported or Qt.AA_ShareOpenGLContexts
must be set before a QCoreApplication instance is created".
"""
import uuid
from PyQt6.QtCore import Qt, QPointF, QUrl, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .ui import BTN, ResizeGrip
from .icons import icon, style_button

COLOR = "#38bdf8"   # ciano — distingue painéis de terminais no canvas

# Estados expostos para a API HTTP (parte 2) ler.
OK, LOADING, CRASHED = "ok", "loading", "crashed"


class BrowserWindow(QFrame):
    """Janela flutuante com navegador; mesma linguagem visual da FloatingWindow."""

    def __init__(self, canvas, url="about:blank", name=None):
        super().__init__()
        self.canvas = canvas
        self.id = f"panel-{uuid.uuid4().hex[:8]}"
        self.name = name or f"painel-{uuid.uuid4().hex[:3]}"
        # papel/agent fictícios: o Edge e a Bridge usam estes campos para desenhar
        # a seta e listar peers. receive() ignora texto (painel não é um terminal).
        self.role = {"name": "painel web", "color": COLOR}
        self.role_name = "painel web"
        self.agent = {"name": "Chromium"}
        self.proxy = None; self._drag = None
        self.last_message = ""
        self.state = OK          # ok | loading | crashed — lido pela API HTTP (parte 2)

        self.setObjectName("panel")
        self.setMinimumSize(420, 280); self.resize(760, 520)
        self.setStyleSheet(
            f"QFrame#panel{{background:#121212;border:1px solid {COLOR};border-radius:10px;}}")

        lay = QVBoxLayout(self); lay.setContentsMargins(1, 1, 1, 1); lay.setSpacing(0)

        # --- barra de título (36px, igual à do terminal) ---
        bar = QWidget(); bar.setFixedHeight(36); bar.setObjectName("bar")
        bar.setStyleSheet("QWidget#bar{background:#1c2028;border-top-left-radius:9px;border-top-right-radius:9px;}")
        bl = QHBoxLayout(bar); bl.setContentsMargins(12, 0, 8, 0); bl.setSpacing(8)
        self.dot = QLabel("●"); self.dot.setStyleSheet(f"color:{COLOR};font-size:9px;")
        self.title = QLabel("Novo painel")
        self.title.setStyleSheet("color:#e6edf3;font-weight:600;font-size:10pt;")
        chip = QLabel("PAINEL")
        chip.setStyleSheet(f"background:{COLOR};color:#101114;border-radius:8px;padding:0 9px;font-size:8pt;font-weight:700;")
        chip.setFixedHeight(18)
        for w_ in (self.dot, self.title, chip): bl.addWidget(w_)
        bl.addStretch()
        for ic, fb, tip, fn in (
                ("link-variant", "L", "Conectar a outro terminal ou painel (clique aqui e depois no ícone de link do destino)", lambda: canvas.start_link(self)),
                ("close", "X", "Fechar painel", self.close_panel)):
            b = QPushButton(); b.setFixedSize(26, 26); b.setToolTip(tip); b.setStyleSheet(BTN)
            style_button(b, ic, fb); b.clicked.connect(fn); bl.addWidget(b)
            if ic == "link-variant": self.link_btn = b
        lay.addWidget(bar)

        # --- barra de endereço ---
        addr = QWidget(); addr.setFixedHeight(34); addr.setObjectName("addr")
        addr.setStyleSheet(f"QWidget#addr{{background:#161a22;border-top:1px solid #232833;}}")
        al = QHBoxLayout(addr); al.setContentsMargins(8, 0, 8, 0); al.setSpacing(6)
        self.back = self._nav_btn("arrow-left", "Voltar", self.go_back)
        self.fwd = self._nav_btn("arrow-right", "Avançar", self.go_forward)
        self.reload = self._nav_btn("refresh", "Recarregar", self.go_reload)
        for b in (self.back, self.fwd, self.reload): al.addWidget(b)
        self.url = QLineEdit(); self.url.setText(url)
        self.url.setPlaceholderText("endereço ou busca")
        self.url.setStyleSheet(
            f"QLineEdit{{background:#0d1017;color:#e6edf3;border:1px solid #303649;border-radius:7px;padding:5px 9px;font-size:9pt;}}"
            f"QLineEdit:focus{{border:1px solid {COLOR};}}")
        self.url.returnPressed.connect(self._go)
        al.addWidget(self.url, 1)
        self.spin = QLabel(""); self.spin.setFixedWidth(46)
        self.spin.setStyleSheet("color:#8b949e;font-size:8pt;")
        al.addWidget(self.spin)
        lay.addWidget(addr)

        # --- navegador ---
        # Perfil off-the-record: um por painel, sem cookies nem cache em disco.
        # O padrão do Qt seria o perfil compartilhado do usuário real — não queremos.
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)

        self.view = QWebEngineView()
        self.page = QWebEnginePage(self.profile, self.view)   # página precisa de um view como parent
        self.view.setPage(self.page)
        self.view.setStyleSheet("QWebEngineView{background:#ffffff;border:none;}")
        self.page.setBackgroundColor(QColor("#ffffff"))
        self.view.loadStarted.connect(lambda: self._loading(0))
        self.view.loadProgress.connect(self._loading)
        self.view.loadFinished.connect(self._load_finished)
        self.view.urlChanged.connect(self._url_changed)
        self.view.titleChanged.connect(self._title_changed)
        self.view.renderProcessTerminated.connect(self._crashed)
        lay.addWidget(self.view, 1)

        # --- rodapé ---
        foot = QWidget(); foot.setFixedHeight(20); foot.setObjectName("foot")
        foot.setStyleSheet("QWidget#foot{background:#1c2028;border-bottom-left-radius:9px;border-bottom-right-radius:9px;}")
        fl = QHBoxLayout(foot); fl.setContentsMargins(8, 0, 2, 0)
        self.dim = QLabel(""); self.dim.setStyleSheet("color:#6e7681;font-size:8pt;")
        fl.addWidget(self.dim); fl.addStretch(); fl.addWidget(ResizeGrip(self))
        lay.addWidget(foot)

        bar.mousePressEvent, bar.mouseMoveEvent, bar.mouseReleaseEvent = self._tp, self._tm, self._tr
        self.update_status()
        if url and url != "about:blank": QTimer.singleShot(0, self._go)

    # --- UI ------------------------------------------------------------
    def _nav_btn(self, ic, tip, fn):
        b = QPushButton(); b.setFixedSize(26, 26); b.setToolTip(tip); b.setStyleSheet(BTN)
        style_button(b, ic, ""); b.clicked.connect(fn); return b

    def _loading(self, pct):
        if pct is None:
            self.spin.setText(""); self.back.setEnabled(self.view.history().canGoBack())
            self.fwd.setEnabled(self.view.history().canGoForward()); return
        self.state = LOADING
        self.spin.setText(f"{pct}%" if pct < 100 else "")
        self.back.setEnabled(self.view.history().canGoBack())
        self.fwd.setEnabled(self.view.history().canGoForward())

    def _load_finished(self, ok):
        """loadFinished(ok=False) costuma ser só erro de rede; crashed é outro caso."""
        if self.state != CRASHED: self.state = OK
        self._loading(None)

    def _crashed(self):
        self.state = CRASHED
        self.spin.setText(""); self.dot.setStyleSheet("color:#f85149;font-size:9px;")
        self.title.setText("renderizador caiu — feche o painel para reabrir")

    def _url_changed(self, u):
        if not self.url.hasFocus(): self.url.setText(u.toDisplayString())
        self.update_status()

    def _title_changed(self, t):
        self.title.setText(t or "Novo painel")

    def update_status(self):
        u = self.url.text().strip() or "about:blank"
        self.dim.setText(f"{self.view.width()}x{self.view.height()}  ·  {u[:60]}")

    def resizeEvent(self, e):
        super().resizeEvent(e); self.update_status()

    # --- navegação ------------------------------------------------------
    def _normalize(self, text):
        t = text.strip()
        if not t: return None
        if "://" in t: return QUrl(t)
        if t.startswith("about:") or t.startswith("file:"): return QUrl(t)
        if "." in t.split("/")[0] and " " not in t: return QUrl("https://" + t)
        return QUrl.fromUserInput(t)          # treatou como busca

    def _go(self):
        u = self._normalize(self.url.text())
        if u: self.view.setUrl(u)

    def go_back(self):
        if self.view.history().canGoBack(): self.view.back()

    def go_forward(self):
        if self.view.history().canGoForward(): self.view.forward()

    def go_reload(self): self.view.reload()

    # --- arrasto --------------------------------------------------------
    def _tp(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.proxy:
            self._drag = e.globalPosition().toPoint(); self._start = self.proxy.pos(); e.accept()

    def _tm(self, e):
        if self._drag is not None:
            d = e.globalPosition().toPoint() - self._drag
            self.proxy.setPos(self._start + QPointF(d.x(), d.y())); e.accept()

    def _tr(self, e):
        self._drag = None; e.accept()

    # --- interface com a Bridge ----------------------------------------
    def receive(self, src, text):
        """Painéis não são terminais: guarda a mensagem e a mostra no rodapé."""
        self.last_message = f"[Tayama de {src.name}] {text}"
        self.update_status()

    def close_panel(self):
        # nada fica no disco: limpa cookies e cache do perfil antes de fechar
        try:
            # Qt6: cookies ficam no cookieStore(); cache é do perfil
            self.profile.cookieStore().deleteAllCookies()
            self.profile.clearHttpCache()
        except (RuntimeError, AttributeError):
            pass          # perfil já destruído pelo Qt
        self.canvas.remove_panel(self); self.deleteLater()