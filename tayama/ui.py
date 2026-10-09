"""Janelas flutuantes, conexões (setas), canvas infinito e diálogos."""
import math, os, shlex, uuid
from PyQt6.QtCore import Qt, QPointF, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF, QPainterPath, QTransform
from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPathItem, QFrame, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QMenu, QDialog, QFormLayout, QLineEdit, QComboBox, QListWidget,
    QListWidgetItem, QDialogButtonBox, QFileDialog, QPlainTextEdit, QMessageBox)
from . import agents, config, layout, trust, workspaces
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

    def mouseReleaseEvent(self, e):
        self._p = None
        if self.target.proxy: self.target.canvas._persist_window(self.target)
        e.accept()


class FloatingWindow(QFrame):
    def __init__(self, canvas, spec):
        super().__init__()
        self.canvas, self.id = canvas, spec.get("id") or uuid.uuid4().hex[:8]
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
        self.agent_label = QLabel(); self.agent_label.setStyleSheet("color:#7d8590;font-size:8.5pt;")
        self.refresh_agent_label()
        for w_ in (self.dot, self.title, chip, self.agent_label): bl.addWidget(w_)
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

    def _tr(self, e):
        self._drag = None
        if self.proxy: self.canvas._persist_window(self)
        e.accept()

    def refresh_agent_label(self):
        """Reescreve 'agente · workspace' — chamado ao abrir e ao renomear o workspace.

        O label é montado em __init__ com o nome do workspace da época; editar
        o workspace troca win.workspace ao vivo, então o texto precisa ser
        refeito aqui (MainWindow.edit_workspace chama este método).
        """
        ws = getattr(self, "workspace", None)
        self.agent_label.setText(self.agent["name"] + (f"  ·  {ws['name']}" if ws else ""))

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


# Rectângulo virtual da grade. Compartilhado por TODAS as cenas: como o Qt
# preserva o transform ao trocar de cena, um mesmo rect mantém as coordenadas
# comparáveis entre workspaces — é o que torna _save_view/_restore_view válidos.
SCENE_RECT = (-50000, -50000, 100000, 100000)


_CURRENT = object()   # sentinela de "não passei workspace" (ver add_panel)


def _ws_id(ws):
    """Id do workspace, ou "" quando o nó não pertence a nenhum.

    O "" é a chave da cena sem workspace (ver InfiniteCanvas._scene_of).
    """
    return ((ws or {}) or {}).get("id") or ""


def _item_scene(item):
    """Cena à qual o item PERTENCE — não a cena exibida.

    QGraphicsScene.removeItem() numa cena errada é NO-OP SILENCIOSO (medido no
    Qt 6.11: só um qWarning, o item continua preso). Por isso todo desaninhamento
    usa a cena do próprio item: com uma cena por workspace, remover da cena
    "atual" deixaria os nós dos outros workspaces grudados — e um BrowserWindow
    preso é o que derruba o processo no teardown (v. _fechar nos testes).
    """
    return item.scene() if item is not None else None


def _out_of(item):
    """Tira o item da cena dele. Idempotente: item já solto não faz nada."""
    sc = _item_scene(item)
    if sc is not None:
        sc.removeItem(item)
    return sc


class InfiniteCanvas(QGraphicsView):
    changed = pyqtSignal()   # terminais adicionados/removidos/encerrados
    notice = pyqtSignal(str)  # avisos para a barra de status (ex.: link recusado)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Uma cena por workspace: clicar num workspace na sidebar troca a cena
        # exibida em vez de revelar tudo misturado. self.gscene é a cena de
        # current_ws, mantida como atributo porque o restante do código (e os
        # testes) fala dela.
        self.scenes, self._views = {}, {}
        self.gscene = self._scene_of(None)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Registros GLOBAIS: contêm os nós de todos os workspaces, mesmo os que
        # não estão na tela (eles continuam rodando em background). É o que
        # mantém a Bridge e a sidebar funcionando sem saber de cenas.
        self.windows, self.edges, self.panels = {}, [], {}
        self._link_src = None; self._panning = False; self._space = False; self._n = 0
        # Workspace corrente: o exibido, e onde nascem painéis novos. Padrão =
        # último da lista, ou None se não houver nenhum (painel ainda funciona,
        # só não persiste).
        wss = workspaces.load()
        self.current_ws = wss[-1] if wss else None
        self._swap_scene(self.current_ws, _save=False)
        t = QTimer(self, interval=33); t.timeout.connect(self._tick_edges); t.start()

    # --- cenas por workspace --------------------------------------------
    def _scene_of(self, ws):
        """Cena do workspace, criando se ainda não existir. ws None/{} -> cena ''.

        A cena '' (sentinela) é onde vivem nós sem workspace. Ela é uma cena
        normal, não um buraco: assim o app com current_ws=None ainda mostra o
        grid, e um painel sem workspace continua visível em vez de sumir.
        """
        k = _ws_id(ws)
        sc = self.scenes.get(k)
        if sc is None:
            sc = QGraphicsScene(self); sc.setSceneRect(*SCENE_RECT)
            self.scenes[k] = sc
        return sc

    def set_current_ws(self, ws):
        """Troca o workspace exibido (a cena) e o que é 'corrente' para nascimentos.

        Salva o enquadramento do workspace que sai e restaura o do que entra —
        necessário porque cenas com o mesmo sceneRect preservam o transform do
        view, então sem isso o pan/zoom vazaria de um workspace para outro.
        """
        ws = ws or None
        # _prev antes da troca: ver _swap_scene.
        self._swap_scene(ws, _prev=self.current_ws)
        self.current_ws = ws
        self.changed.emit()

    def _swap_scene(self, ws, _save=True, _prev=_CURRENT):
        """Troca a cena exibida, movendo o pan/zoom de uma para a outra.

        _prev é o workspace que SAI, e é sob a chave DELE que o enquadramento
        é salvo. Precisa ser explícito: set_current_ws já atribuiu self.current_ws
        ao workspace novo antes de chegar aqui, então salvar por self.current_ws
        gravaria o enquadramento de A na chave de B — e A nunca receberia o seu
        de volta (medido: voltando de B, o zoom era o de B).
        """
        if _save and self.gscene is not None:
            saiu = self.current_ws if _prev is _CURRENT else _prev
            self._views[_ws_id(saiu)] = self._read_view()
        self.gscene = self._scene_of(ws)
        self.setScene(self.gscene)
        self._restore_view(_ws_id(ws))
        self._refresh_edges()

    def _read_view(self):
        """Enquadramento atual como (transform, centro_em_coord_de_cena).

        Guardamos centro+transform, não os valores dos scrollbars: elas estão
        desligadas (ScrollBarAlwaysOff) e só servem para o panning.
        """
        return (QTransform(self.transform()),
                self.mapToScene(self.viewport().rect().center()))

    def _restore_view(self, key):
        """Restaura o enquadramento salvo do workspace. Sem estado salvo, a cena
        entra com o transform que já estava no view — comportamento medido do
        Qt, e é o que evita um workspace vazio abrir com o zoom do anterior."""
        st = self._views.get(key)
        if not st: return
        t, c = st
        self.setTransform(t); self.centerOn(c)

    def forget_workspace(self, ws_id):
        """Descarta cena e enquadramento de um workspace removido.

        Sem isto a cena ficaria em self.scenes para sempre, vazia, segurando
        o _views. Se a cena removida era a exibida, cai para a primeira que
        sobrar (ou a sentinel, que é sempre válida).
        """
        self._views.pop(ws_id, None)
        sc = self.scenes.pop(ws_id, None)
        if sc is None: return
        if self.gscene is sc:
            self._swap_scene(None, _save=False)
            self.setScene(self._scene_of(None))
        # não destruímos a cena: um QGraphicsScene com proxy vivo destrói o
        # widget em cascata (medido: crash). Os nós já foram fechados antes,
        # um a um, por quem removeu o workspace.

    def _drop_point(self, scene):
        """Onde nasce um nó novo na cena `scene`.

        NÃO pode usar self.mapToScene(): ele é relativo à cena EXIBIDA (medido:
        com a cena A visível devolve (318,238) para um proxy de B em (1100,1100)
        — uma posição sem sentido em B). Usamos o centro do conteúdo da cena
        alvo, que itemsBoundingRect() devolve corretamente mesmo oculta.

        Cena vazia não tem conteúdo: usamos a origem da cena, que é o ponto
        neutro e previsível para o primeiro nó.
        """
        r = scene.itemsBoundingRect()
        if r.isEmpty(): return QPointF(0, 0)
        return QPointF(r.center())

    def _tick_edges(self):
        """Redesenha as setas da cena EXIBIDA a cada 33ms.

        Só as visíveis: as de cenas ocultas não se movem (não são pintadas) e
        recalculá-las seria trabalho invisível.
        """
        sc = self.scene()
        for e in self.edges:
            if _item_scene(e) is sc and e.src.proxy and e.dst.proxy: e.refresh()

    def _refresh_edges(self):
        """Redesenha as setas da cena exibida — chamado na troca de cena, para
        os nós movidos enquanto estavam ocultos voltarem com a geometria certa."""
        self._tick_edges()

    def drawBackground(self, p, rect):
        p.fillRect(rect, QColor("#0d1117")); g = 32
        p.setPen(QPen(QColor("#2a303b"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for x in range(int(rect.left()) - int(rect.left()) % g, int(rect.right()), g):
            for y in range(int(rect.top()) - int(rect.top()) % g, int(rect.bottom()), g): p.drawPoint(x, y)

    # --- terminais e conexões -----------------------------------------
    def add_terminal(self, spec, _persist=True):
        win = FloatingWindow(self, spec)
        # Cena do PRÓPRIO workspace, não a exibida: um terminal restaurado de um
        # workspace que não está na tela precisa nascer na cena dele (é o que
        # faz o startup com vários workspaces montar cada um no seu canvas).
        scene = self._scene_of(win.workspace)
        proxy = scene.addWidget(win); win.proxy = proxy
        pos = spec.pop("_restored_pos", None); size = spec.pop("_restored_size", None)
        if pos and size:
            proxy.setPos(*pos); win.resize(*size)
        else:
            c = self._drop_point(scene); off = 30 * (self._n % 8); self._n += 1
            proxy.setPos(c.x() - win.width() / 2 + off, c.y() - win.height() / 2 + off)
        self.windows[win.id] = win
        if _persist: agents.save_one(win)
        self.changed.emit(); return win

    def remove_window(self, win):
        for e in [e for e in self.edges if win in (e.src, e.dst)]: self.remove_edge(e)
        agents.remove_one(win.id, win.workspace["id"])
        self.windows.pop(win.id, None)
        _out_of(win.proxy)
        self.changed.emit()

    def peers_of(self, win): return [e.dst for e in self.edges if e.src is win]

    def start_link(self, win):
        if self._link_src is None:
            self._link_src = win; win.link_btn.setStyleSheet(BTN + "QPushButton{background:#e5a400;}")
            win.link_btn.setIcon(icon("link-variant", "#101114")); return
        src, self._link_src = self._link_src, None
        src.link_btn.setStyleSheet(BTN); src.link_btn.setIcon(icon("link-variant"))
        if src is win: return
        # Workspaces são ambientes separados: uma seta entre dois deles não tem
        # onde ser desenhada (as pontas vivem em cenas diferentes) nem sentido.
        # Recusamos com aviso — silenciosamente o usuário acharia que a seta
        # sumiu sozinha.
        a, b = _ws_id(getattr(src, "workspace", None)), _ws_id(getattr(win, "workspace", None))
        if a != b:
            self.notice.emit(f"'{src.name}' e '{win.name}' estão em workspaces diferentes "
                             f"— a conexão foi recusada.")
            return
        if not any(e.src is src and e.dst is win for e in self.edges):
            # A seta entra na cena do workspace da origem — que é o mesmo dos
            # dois, já que recusamos pares cross-workspace acima.
            e = Edge(self, src, win); self._scene_of(src.workspace).addItem(e); self.edges.append(e)
            self._persist_links(src)

    def remove_edge(self, e):
        if e in self.edges:
            self.edges.remove(e)
            self._persist_links(e.src)      # recria a lista sem esta seta
        _out_of(e)

    def _persist_links(self, src):
        """Grava as conexões no workspace da origem da seta.

        Terminal↔terminal, terminal↔painel e painel↔painel entram no mesmo arquivo.
        Se a origem não tem workspace, nada é gravado.

        Só entram pares cujas DUAS pontas estão no workspace de origem: uma seta
        cross-workspace não existe mais (start_link recusa), e exigir as duas
        pontas faz o arquivo se auto-curar de links antigos gravados antes.
        """
        ws_id = _ws_id(getattr(src, "workspace", None))
        if not ws_id: return
        layout.save_links(ws_id, [(e.src.id, e.dst.id) for e in self.edges
                                  if _ws_id(getattr(e.src, "workspace", None)) == ws_id
                                  and _ws_id(getattr(e.dst, "workspace", None)) == ws_id])

    # --- painéis web ----------------------------------------------------
    # Import tardio: tayama.browser importa daqui (BTN/ResizeGrip) e o QtWebEngine
    # precisa ser carregado antes do QApplication.
    def add_panel(self, url="about:blank", name=None, pos=None, size=None, _id=None, _persist=True,
                  workspace=_CURRENT):
        """Cria um painel na cena do workspace dado.

        `workspace` tem default sentinela e não None de propósito: None é um valor
        legítimo ("painel sem workspace, que não persiste" — test_restore_flow
        cobre isso) e precisa ser distinguível de "não passei nada, usa o corrente".
        """
        from .browser import BrowserWindow
        ws = self.current_ws if workspace is _CURRENT else workspace
        win = BrowserWindow(self, url, name, _id)
        win.workspace = ws or None            # None se não houver workspace: aí não persiste
        scene = self._scene_of(win.workspace)  # cena do PRÓPRIO ws, não a exibida
        proxy = scene.addWidget(win); win.proxy = proxy
        if pos and size:
            proxy.setPos(*pos); win.resize(*size)
        else:
            c = self._drop_point(scene)
            off = 30 * (self._n % 8); self._n += 1
            proxy.setPos(c.x() - win.width() / 2 + off, c.y() - win.height() / 2 + off)
        self.panels[win.id] = win
        if _persist: layout.save_panel(win)
        self.changed.emit(); return win

    def remove_panel(self, win):
        for e in [e for e in self.edges if win in (e.src, e.dst)]: self.remove_edge(e)
        layout.remove_panel(win.id, (getattr(win, "workspace", None) or {}).get("id"))
        self.panels.pop(win.id, None)
        _out_of(win.proxy)
        self.changed.emit()

    # --- persistência --------------------------------------------------
    def _persist_window(self, win):
        """Grava posição/tamanho após drag ou resize.

        Terminais vão para agents.py; painéis vão para layout.py.
        Sem workspace não há para qual arquivo gravar — degrada sem crash.
        """
        if not getattr(win, "workspace", None): return
        if win in self.panels.values(): layout.save_panel(win)
        else: agents.save_one(win)

    def _nodes_by_id(self):
        """Todos os nós do canvas (terminais + painéis) indexados por id."""
        return {**self.windows, **self.panels}

    def restore_agents(self, ws_id=None):
        """Restaura os terminais. ws_id filtra por workspace; None = todos.

        O filtro existe para workspaces.duplicate: o clone nasce no disco e
        precisa virar objeto vivo no canvas na hora, senão a sidebar (que monta
        a árvore a partir de self.windows) mostra o clone vazio.
        """
        cfg = config.load()
        wss = {w["id"]: w for w in workspaces.load()}
        # ws_id None = todos os workspaces (startup). Com filtro, load_one() é o
        # mesmo caminho de leitura por workspace que load_all() usa por dentro.
        entries = agents.load_all() if ws_id is None else \
            [e for e in agents.load_one(ws_id) if isinstance(e, dict)]
        self.blockSignals(True)
        try:
            for e in entries:
                agent = next((a for a in cfg["agents"] if a["name"] == e["agent_name"]), None)
                if not agent:
                    print(f"Tayama: agente '{e['name']}' ignorado — '{e['agent_name']}' não está mais em config"); continue
                role = next((r for r in cfg["roles"] if r["name"] == e["role_name"]), None) if e.get("role_name") else None
                ws = wss.get(e["workspace_id"])
                if not ws:
                    print(f"Tayama: agente '{e['name']}' ignorado — workspace removido"); continue
                # Já materializado (mesmo workspace restaurado duas vezes): pula.
                # add_terminal() sobrescreveria canvas.windows[id] e deixaria o
                # terminal antigo órfão na cena, com o PTY ainda vivo.
                if e["id"] in self.windows: continue
                skills = [s for s in cfg["skills"] if s["name"] in e.get("skills", [])]
                self.add_terminal({
                    "id": e["id"], "name": e["name"], "agent": agent, "role": role, "workspace": ws,
                    "cwd": ws["path"], "skills": skills,
                    "_restored_pos": (e["x"], e["y"]), "_restored_size": (e["w"], e["h"]),
                }, _persist=False)
        finally:
            self.blockSignals(False)
            self.changed.emit()

    def restore_panels(self, ws_id=None):
        """Restaura os painéis e as conexões. ws_id filtra por workspace; None = todos.

        Uma seta só volta se os dois nós voltarem; nó ausente = descarta em silêncio.

        O workspace corrente NÃO é tocado aqui: cada painel nasce com
        `workspace=ws`, na cena do próprio workspace. Antes o laço chamava
        set_current_ws() e o finally devolvia o valor (o "hack do previous_ws"),
        que existia só porque add_panel herdava o ws corrente. Sem isso, o
        restore materializar um clone não pode mais roubar o foco de quem está
        usando o app — e a invariante é explícita, não efeito colateral.

        Par com pontas em workspaces diferentes é de dado antigo: connections
        cross-workspace eram permitidas antes de cada workspace ter a sua cena,
        e estão possivelmente em links/<ws>.json. Descartamos com log — uma
        seta entre cenas diferentes não teria onde ser desenhada.
        """
        wss = {w["id"]: w for w in workspaces.load()}
        if ws_id is None:
            panels = layout.load_all_panels()
            links = layout.load_all_links()
        else:
            # load_panels() devolve o que _read() normalizou (pode não ser dict) —
            # repetimos o filtro que load_all_panels faz, já que a spec é usada
            # direto. load_links() ja devolve so pares válidos.
            panels = [(ws_id, s) for s in layout.load_panels(ws_id) if isinstance(s, dict)]
            links = [(ws_id, pair) for pair in layout.load_links(ws_id)]
        self.blockSignals(True)
        try:
            for w_id, spec in panels:
                ws = wss.get(w_id)
                if not ws:
                    print(f"Tayama: painel '{spec.get('name')}' ignorado — workspace removido"); continue
                # Já materializado (mesmo workspace restaurado duas vezes): pula.
                # add_panel() recriaria o BrowserWindow, sobrescreveria
                # canvas.panels[id] e deixaria o painel antigo órfão na cena.
                if spec.get("id") in self.panels: continue
                self.add_panel(spec.get("url") or "about:blank", name=spec.get("name"),
                               pos=(spec["x"], spec["y"]), size=(spec["w"], spec["h"]),
                               _id=spec.get("id"), _persist=False, workspace=ws)
            for w_id, (src_id, dst_id) in links:
                nodes = self._nodes_by_id(); src, dst = nodes.get(src_id), nodes.get(dst_id)
                if src is None or dst is None: continue     # nó não voltou: descarta em silêncio
                # dedup por ID, não por objeto: um nó recriado (restore repetido)
                # é um objeto novo, e a comparação por identidade deixaria passar
                # a seta antiga — duplicando a conexão no canvas.
                if src_id == dst_id or any(e.src.id == src_id and e.dst.id == dst_id for e in self.edges): continue
                # conexão cross-workspace: dado antigo, de antes de cada workspace
                # ter a sua própria cena. Descarta em vez de criar uma seta cujas
                # pontas vivem em cenas diferentes.
                if _ws_id(getattr(src, "workspace", None)) != _ws_id(getattr(dst, "workspace", None)):
                    print(f"Tayama: conexão {src.name} → {dst.name} ignorada — "
                          f"workspaces diferentes (dado antigo)")
                    continue
                e = Edge(self, src, dst); self._scene_of(src.workspace).addItem(e); self.edges.append(e)
        finally:
            self.blockSignals(False)
            self.changed.emit()

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
    def __init__(self, existing=None, parent=None):
        # existing: dict do workspace a editar (id preservado) ou None para criar.
        super().__init__(parent); self.existing = existing or None
        self.setWindowTitle("Editar workspace" if self.existing else "Novo workspace"); self.resize(500, 150)
        f = QFormLayout(self)
        self.name = QLineEdit(); self.name.setPlaceholderText("ex.: meu-projeto")
        self.path = QLineEdit(); self.path.setPlaceholderText("pasta onde os agentes vão trabalhar")
        if self.existing:
            self.name.setText(self.existing.get("name", "")); self.path.setText(self.existing.get("path", ""))
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
