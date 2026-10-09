#!/usr/bin/env python3
"""CONTRATO DE UI de editar/duplicar workspace (a camada que os testes de
workspaces.py NAO cobriam).

    ./run.sh                                  # roda com as dependencias
    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_workspace_ui.py -v

Por que este arquivo existe
---------------------------
tests/test_workspace_clone.py exercita so tayama/workspaces.py (funcoes puras
sobre arquivos). Um metodo placed na classe errada passa essa suite inteira e
so estoura quando o usuario abre o app: foi exatamente o que aconteceu com
refresh_agent_label, que foi parar em WorkspaceDialog em vez de
FloatingWindow — 254 testes verdes e o app morrendo ao abrir o primeiro
terminal. Estes testes montam widgets de verdade (QApplication offscreen) para
que esse erro apareca na suite.

Se PyQt6 nao estiver instalado, pula com o motivo explicito.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- PyQt6 tem de vir ANTES do QApplication (restricao do QtWebEngine) ------
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PyQt6.QtWidgets import QApplication
    HAS_QT = True
    _ERR = ""
except Exception as exc:  # pragma: no cover
    HAS_QT = False
    _ERR = f"{type(exc).__name__}: {exc}"

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
                   or os.environ.get("QT_QPA_PLATFORM") == "offscreen")

pytestmark = [
    pytest.mark.skipif(
        not (HAS_QT and HAS_DISPLAY),
        reason=(f"contrato de UI exige PyQt6 + display. qt={HAS_QT} ({_ERR}); "
                f"display={HAS_DISPLAY}. Rode com QT_QPA_PLATFORM=offscreen sob um "
                "venv com os requisitos instalados."),
    ),
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """TAYAMA_HOME isolado — nada toca no ~/.tayama do usuario."""
    fake = tmp_path / "tayama-home"
    fake.mkdir()
    monkeypatch.setenv("TAYAMA_HOME", str(fake))
    from tayama import config
    monkeypatch.setattr(config, "DIR", fake)
    monkeypatch.setattr(config, "CFG", fake / "config.json")
    monkeypatch.setattr(config, "SKILLS", fake / "skills")
    config.ensure()
    return fake


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(["tayama-test"])
    yield app


@pytest.fixture
def ws(home):
    """Workspace persistido, com um agente 'sleep' configurado (nada e lancado)."""
    from tayama import config, workspaces
    cfg = json.loads((home / "config.json").read_text("utf-8"))
    cfg["agents"] = [{"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0}]
    (home / "config.json").write_text(json.dumps(cfg), "utf-8")
    return workspaces.add("Original", str(home))


def term_spec(ws, name="alfa", pid="term-1"):
    """Spec de terminal — mesmo formato que agents.save_one grava."""
    return {"id": pid, "name": name,
            "agent": {"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0},
            "role": {"name": "Dev", "color": "#58a6ff"},
            "workspace": ws, "cwd": ws["path"], "skills": []}


class _FakeCanvas:
    """Canvas mínimo para FloatingWindow.

    A janela só toca o canvas em lambda/sinal (start_link, peers_of, changed),
    nunca no __init__ — entao um stub cobre o contrato do label sem subir o
    InfiniteCanvas inteiro nem o QtWebEngine.
    """
    def peers_of(self, win): return []

    def start_link(self, win): pass

    def remove_window(self, win): pass

    def _persist_window(self, win): pass


@pytest.fixture
def canvas():
    return _FakeCanvas()


# ==========================================================================
# 1. WorkspaceDialog com ws existente = edicao (id preservado)
# ==========================================================================
def test_dialog_com_ws_existente_preenche_campos(qapp, ws):
    from tayama.ui import WorkspaceDialog
    d = WorkspaceDialog(ws)
    assert d.name.text() == "Original"
    assert d.path.text() == str(ws["path"])
    assert d.windowTitle() == "Editar workspace"


def test_dialog_com_ws_existente_marca_o_ws(qapp, ws):
    """O dialog precisa saber qual workspace esta editando — e o id nao muda."""
    from tayama.ui import WorkspaceDialog
    d = WorkspaceDialog(ws)
    assert d.existing == ws
    assert d.value() == ("Original", str(ws["path"]))


def test_dialog_com_ws_existente_preserva_o_browse(qapp, ws):
    """Editar precisa continuar permitindo trocar o diretorio."""
    from PyQt6.QtWidgets import QPushButton
    from tayama.ui import WorkspaceDialog
    d = WorkspaceDialog(ws)
    assert any(isinstance(b, QPushButton) for b in d.findChildren(QPushButton)), \
        "o botao de browse sumiu no modo edicao"


def test_dialog_com_ws_existente_nao_sobrescreve_workspace_nenhum(qapp, ws):
    """Abrir o dialog e recusar não pode gravar nada."""
    from tayama import workspaces
    from tayama.ui import WorkspaceDialog
    antes = workspaces.load()
    d = WorkspaceDialog(ws)
    d.name.setText("Outro nome"); d.reject()
    assert workspaces.load() == antes


# ==========================================================================
# 2. WorkspaceDialog sem ws = criar (comportamento intacto)
# ==========================================================================
def test_dialog_sem_ws_fica_vazio_e_titulado_novo(qapp, home):
    from tayama.ui import WorkspaceDialog
    d = WorkspaceDialog()
    assert d.name.text() == "" and d.path.text() == ""
    assert d.windowTitle() == "Novo workspace"
    assert d.existing is None


def test_dialog_novo_aceita_parent_como_keyword(qapp, home):
    """main.py abre WorkspaceDialog(parent=self) — o primeiro posicional virou
    'existing', entao o uso por keyword tem de continuar funcionando."""
    from tayama.ui import WorkspaceDialog
    d = WorkspaceDialog(parent=None)
    assert d.existing is None and d.windowTitle() == "Novo workspace"


# ==========================================================================
# 3. FloatingWindow: o metodo tem de estar AQUI
# ==========================================================================
def test_refresh_agent_label_mora_em_floating_window(qapp):
    """O defeito: refresh_agent_label foi definido em WorkspaceDialog.
    O chamador (__init__) e MainWindow.edit_workspace vivem em FloatingWindow,
    entao o metodo na classe errada quebrava o app ao abrir o 1o terminal."""
    from tayama.ui import FloatingWindow, WorkspaceDialog
    assert hasattr(FloatingWindow, "refresh_agent_label"), \
        "refresh_agent_label ausente em FloatingWindow (colocado na classe errada?)"
    assert not hasattr(WorkspaceDialog, "refresh_agent_label"), \
        "refresh_agent_label nao pertence a WorkspaceDialog"


def test_floating_window_constroi_sem_attributeerror(qapp, ws, canvas):
    """Abrir um terminal nao pode estourar."""
    from tayama.ui import FloatingWindow
    win = FloatingWindow(canvas, term_spec(ws))
    assert win.agent_label.text() == "sleep  ·  Original"


def test_floating_window_sem_workspace_nao_quebra(qapp, ws, canvas):
    """Sem workspace o label mostra so o agente — o sufixo some."""
    from tayama.ui import FloatingWindow
    spec = term_spec(ws); spec["workspace"] = {}
    win = FloatingWindow(canvas, spec)
    assert win.agent_label.text() == "sleep"


def test_refresh_agent_label_reflete_workspace_novo(qapp, ws, canvas):
    """MainWindow.edit_workspace troca win.workspace ao vivo; o label tem de
    acompanhar, senao o nome novo so aparece depois de reiniciar."""
    from tayama.ui import FloatingWindow
    win = FloatingWindow(canvas, term_spec(ws))
    win.workspace = {"id": ws["id"], "name": "Renomeado", "path": ws["path"]}
    win.refresh_agent_label()
    assert win.agent_label.text() == "sleep  ·  Renomeado"


def test_refresh_agent_label_e_idempotente(qapp, ws, canvas):
    """Chamar duas vezes nao pode duplicar o sufixo."""
    from tayama.ui import FloatingWindow
    win = FloatingWindow(canvas, term_spec(ws))
    win.refresh_agent_label(); win.refresh_agent_label()
    assert win.agent_label.text() == "sleep  ·  Original"


def test_ciclo_de_editar_workspace_com_janela_aberta(qapp, ws, canvas):
    """O caminho do main.py: dialogo aceito -> update no disco -> dict da
    janela trocado -> label refrescado. Tudo sem tocar no app inteiro."""
    from tayama import workspaces
    from tayama.ui import FloatingWindow, WorkspaceDialog
    win = FloatingWindow(canvas, term_spec(ws))

    d = WorkspaceDialog(ws)
    d.name.setText("Renomeado")
    nome, path = d.value()
    updated = workspaces.update(ws["id"], nome, path)

    if win.workspace.get("id") == ws["id"]:
        win.workspace = updated
        win.refresh_agent_label()
    assert win.agent_label.text() == "sleep  ·  Renomeado"
    assert updated["id"] == ws["id"], "editar nao pode trocar o id do workspace"
    assert workspaces.load()[0]["name"] == "Renomeado"


# ==========================================================================
# 4. O DEFEITO: duplicar nao trazia o clone para a tela
# ==========================================================================
# O sintoma: workspaces.duplicate() grava agentes/paineis/links do clone
# CORRETOS no disco (ids novos, links remapeados), mas Sidebar.refresh() monta a
# arvore a partir de self.canvas.windows / self.canvas.panels — os objetos VIVOS.
# O clone so existia no disco, entao a sidebar mostrava o clone VAZIO e o usuario
# so veria os terminais/paineis depois de reiniciar o app.
#
# Estos testes sobem o MainWindow de verdade (caminho do usuario: sidebar ->
# duplicate_workspace) e olham o canvas, nao o disco.

TERM_ID, PAINEL_ID = "term-orig", "panel-orig"


def _cenario_persistido(ws):
    """Grava no disco o que o 'Original' teria: 1 terminal, 1 painel e 1 seta.

    Montar pelo disco (e nao pelo canvas) mantem o teste no defeito: o que
    importa e o que o app le ao abrir, nao como a janela foi criada.
    """
    from tayama import agents, layout
    agents.save_all(ws["id"], [{
        "id": TERM_ID, "name": "alfa", "agent_name": "sleep", "role_name": "Dev",
        "workspace_id": ws["id"], "skills": [],
        "x": 40, "y": 60, "w": 640, "h": 480,
    }])
    layout.save_panels(ws["id"], [{
        "id": PAINEL_ID, "name": "painel", "url": "about:blank",
        "x": 700, "y": 80, "w": 800, "h": 600,
    }])
    layout.save_links(ws["id"], [(TERM_ID, PAINEL_ID)])


def _config_com_agente(home):
    """Agente 'sleep' no config — o terminal pode ser restaurado sem estourar.
    Nenhum agente de verdade e lancado."""
    from tayama import config
    cfg = json.loads((home / "config.json").read_text("utf-8"))
    cfg["agents"] = [{"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0}]
    cfg["roles"] = [{"name": "Dev", "color": "#58a6ff", "prompt": "desenvolve"}]
    (home / "config.json").write_text(json.dumps(cfg), "utf-8")


def _clone_de(ws, *conhecidos):
    """O workspace clonado de `ws` (o unico que sobra alem dos ids informados)."""
    from tayama import workspaces
    vistos = {ws["id"]} | {w["id"] for w in conhecidos}
    outros = [w for w in workspaces.load() if w["id"] not in vistos]
    assert len(outros) == 1, f"esperava 1 clone, achei {[w['name'] for w in outros]}"
    return outros[0]


def test_duplicar_materializa_o_clone_no_canvas(qapp, home):
    """O DEFEITO: apos duplicar, canvas.windows/panels devem conter os itens do
    clone (com o workspace_id novo), com a seta entre eles — sem reiniciar."""
    _config_com_agente(home)
    from tayama import workspaces
    original = workspaces.add("Original", str(home))
    _cenario_persistido(original)

    import main
    w = main.MainWindow()
    try:
        c = w.canvas
        # premissa: o app abriu com o Original materializado
        assert TERM_ID in c.windows, f"premissa: terminal do original nao abriu; {sorted(c.windows)}"
        assert PAINEL_ID in c.panels, f"premissa: painel do original nao abriu; {sorted(c.panels)}"
        assert len(c.edges) == 1, f"premissa: esperava 1 seta, tem {len(c.edges)}"
        windows_antes, panels_antes = set(c.windows), set(c.panels)

        w.duplicate_workspace(original)
        clone = _clone_de(original)

        # --- o clone virou objeto vivo, com o id do workspace novo ---
        terms = [x for x in c.windows.values() if x.workspace["id"] == clone["id"]]
        assert len(terms) == 1, f"terminal do clone nao materializou; canvas tem {sorted(c.windows)}"
        assert terms[0].id != TERM_ID, "o terminal do clone reusou o id do original"

        paineis = [p for p in c.panels.values() if (getattr(p, "workspace", None) or {}).get("id") == clone["id"]]
        assert len(paineis) == 1, f"painel do clone nao materializou; canvas tem {sorted(c.panels)}"
        assert paineis[0].id != PAINEL_ID, "o painel do clone reusou o id do original"

        # --- a seta do clone tambem existe (link remapeado pelo duplicate) ---
        ligacoes = {(e.src.id, e.dst.id) for e in c.edges}
        assert (terms[0].id, paineis[0].id) in ligacoes, \
            f"a seta do clone nao foi recriada; edges={sorted(ligacoes)}"

        # --- e o Original continua intacto na tela ---
        assert windows_antes <= set(c.windows) and panels_antes <= set(c.panels), \
            "duplicar nao pode remover o que ja estava no canvas"
    finally:
        w.deleteLater()


def test_duplicar_nao_troca_o_workspace_corrente(qapp, home):
    """restore_panels() chama set_current_ws() por painel (o painel nasce no ws
    dele). Ao materializar um clone, o painel do clone passaria a devolver o foco
    para o workspace clonado — duplicar nao pode trocar o ws corrente."""
    _config_com_agente(home)
    from tayama import workspaces
    original = workspaces.add("Original", str(home))
    _cenario_persistido(original)

    import main
    w = main.MainWindow()
    try:
        c = w.canvas
        # o usuario esta trabalhando em 'Outro', nao no Original nem no clone
        outro = workspaces.add("Outro", str(home))
        c.set_current_ws(outro)
        antes = c.current_ws

        w.duplicate_workspace(original)
        clone = _clone_de(original, outro)

        assert c.current_ws is not None, "duplicar zerou o workspace corrente"
        assert c.current_ws["id"] == antes["id"], \
            f"duplicar trocou o ws corrente: {antes['name']!r} -> {c.current_ws['name']!r}"
        assert c.current_ws["id"] != clone["id"], "o ws corrente virou o clone"

        # o painel do clone ainda pertence ao clone, mesmo com o foco no 'Outro'
        paineis = [p for p in c.panels.values() if (getattr(p, "workspace", None) or {}).get("id") == clone["id"]]
        assert len(paineis) == 1, "o painel do clone ficou no workspace errado"
    finally:
        w.deleteLater()

def _encerrar(w, qapp):
    """Fecha uma MainWindow com terminais e paineis reais sem derrubar o processo.

    Os testes abaixo sobem o app de verdade, com QWebEngineView dentro de
    proxies. `w.deleteLater()` sozinho NAO basta: sem processar a fila de
    DeferredDelete o Chromium continua vivo e morre na saida do interpretador
    (SIGSEGV em QQuickWindow::~QQuickWindow, medido aqui). Fecha os nos um a
    um — cada um sai da SUA cena — e so entao destroi a janela.

    Equivalente ao _fechar() dos arquivos de canvas, para o nivel MainWindow.
    """
    from PyQt6.QtCore import QCoreApplication, QEvent, QTimer
    c = w.canvas
    for tmr in c.findChildren(QTimer):
        tmr.stop()
    for win in list(c.windows.values()):
        win.close_window()
    for pan in list(c.panels.values()):
        pan.close_panel()
    for _ in range(12):
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    w.close()
    w.deleteLater()
    for _ in range(8):
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# ==========================================================================
# 4. ISOLAMENTO ENTRE WORKSPACES (o MainWindow real, ponta a ponta)
# ==========================================================================
# Ate aqui os testes atacam workspaces.py; os tres de baixo sobem o MainWindow
# e verificam o comportamento que o usuario pediu: clicar num workspace leva ao
# canvas DELE, e nada atravessa essa fronteira.
def test_broadcast_so_chega_ao_workspace_exibido(qapp, home):
    """"Enviar a todos" = todos os terminais DO WORKSPACE VISIVEL.

    A barra dizia "todos" e mandava para todos os workspaces — o que numa
    ferramenta onde cada workspace e um ambiente separado entrega a mensagem no
    ambiente errado. Aqui os dois workspaces estao abertos, com um terminal cada.

    O QInputDialog e o QMessageBox sao monkeypatchados: o teste e sobre o
    roteamento, nao sobre a caixa de dialogo.
    """
    _config_com_agente(home)
    import main
    from tayama import workspaces

    w = main.MainWindow()
    try:
        c = w.canvas
        alfa = workspaces.add("Alfa", str(home))
        beta = workspaces.add("Beta", str(home))
        ta = c.add_terminal(term_spec(alfa, "alfa-1", "term-alfa"))
        tb = c.add_terminal(term_spec(beta, "beta-1", "term-beta"))
        qapp.processEvents()

        enviados = []
        for t in (ta, tb):
            t.terminal.send_text = lambda txt, _t=t: enviados.append((_t.id, txt))

        import PyQt6.QtWidgets as W
        monkey = W.QInputDialog.getMultiLineText
        W.QInputDialog.getMultiLineText = staticmethod(
            lambda *a, **k: ("deploy agora", True))
        try:
            # com Alfa na tela: so o terminal de Alfa recebe
            c.set_current_ws(alfa); qapp.processEvents()
            w.broadcast()
            assert [i for i, _ in enviados] == ["term-alfa"], \
                f"o broadcast vazou para outro workspace: {enviados}"

            # com Beta na tela: so o de Beta — e nao acumula com o envio anterior
            enviados.clear()
            c.set_current_ws(beta); qapp.processEvents()
            w.broadcast()
            assert [i for i, _ in enviados] == ["term-beta"], \
                f"o broadcast nao acompanhou a troca de canvas: {enviados}"
        finally:
            W.QInputDialog.getMultiLineText = monkey
    finally:
        _encerrar(w, qapp)


def test_clique_no_workspace_na_sidebar_troca_o_canvas(qapp, home):
    """O gatilho do pedido: clicar num workspace leva ao canvas DELE.

    Antes nao havia itemClicked nenhum — so itemDoubleClicked, que apenas
    centralizava. Clicar no nome do workspace nao trocava nada: todos os
    terminais de todos os ambientes apareciam juntos no mesmo canvas.
    """
    _config_com_agente(home)
    import main
    from tayama import workspaces
    from PyQt6.QtCore import Qt

    w = main.MainWindow()
    try:
        c, sb = w.canvas, w.sidebar
        alfa = workspaces.add("Alfa", str(home))
        beta = workspaces.add("Beta", str(home))
        c.add_terminal(term_spec(alfa, "alfa-1", "term-alfa"))
        c.add_terminal(term_spec(beta, "beta-1", "term-beta"))
        c.set_current_ws(alfa)
        sb.refresh()
        qapp.processEvents()
        assert c.scene() is c._scene_of(alfa), "premissa: Alfa nao esta no canvas"

        # achar o item do workspace Beta na arvore e "clicar" nele
        alvo = None
        for i in range(sb.tree.topLevelItemCount()):
            it = sb.tree.topLevelItem(i)
            kind, obj = it.data(0, Qt.ItemDataRole.UserRole)
            if kind == "ws" and obj["id"] == beta["id"]:
                alvo = it
        assert alvo is not None, "Beta nao apareceu na arvore da sidebar"

        sb.tree.itemClicked.emit(alvo, 0)      # o sinal que sidebar.py:26 conecta
        qapp.processEvents()                   # a troca e adiada (QTimer.singleShot)

        assert c.scene() is c._scene_of(beta), \
            "clicar em Beta nao trocou o canvas exibido"
        assert (c.current_ws or {}).get("id") == beta["id"], \
            "o workspace corrente nao acompanhou o clique"
        visiveis = {id(i) for i in c.scene().items()}
        assert id(c.windows["term-alfa"].proxy) not in visiveis, \
            "o terminal de Alfa continua no canvas de Beta"
    finally:
        _encerrar(w, qapp)


def test_focar_no_de_outro_workspace_troca_o_canvas_antes_de_centralizar(qapp, home):
    """Duplo clique / "Ir até o terminal" num no de outro workspace.

    centerOn() num item de OUTRA cena nao levanta: ele centraliza em coordenadas
    sem sentido, em silencio (medido no Qt 6.11: centralizou (1299,1249) para um
    proxy em (1100,1100)). A sidebar precisa trocar a cena antes.
    """
    _config_com_agente(home)
    import main
    from tayama import workspaces
    from PyQt6.QtCore import QPointF

    w = main.MainWindow()
    try:
        c, sb = w.canvas, w.sidebar
        alfa = workspaces.add("Alfa", str(home))
        beta = workspaces.add("Beta", str(home))
        c.add_terminal(term_spec(alfa, "alfa-1", "term-alfa"))
        alvo = c.add_terminal(term_spec(beta, "beta-1", "term-beta"))
        alvo.proxy.setPos(QPointF(1100, 1100))
        c.set_current_ws(alfa)                 # o usuario esta no canvas de Alfa
        qapp.processEvents()
        assert c.scene() is c._scene_of(alfa), "premissa: Alfa nao esta no canvas"

        sb.focus(alvo)
        qapp.processEvents()

        assert c.scene() is c._scene_of(beta), \
            "focar num terminal de Beta nao trocou o canvas"
        # centerOn(proxy) centraliza o CENTRO do retangulo do proxy, nao o seu
        # setPos (o canto superior esquerdo) — medido no Qt 6.11.
        esperado = alvo.proxy.sceneBoundingRect().center()
        centro = c.mapToScene(c.viewport().rect().center())
        assert (centro - esperado).manhattanLength() < 40, (
            f"o canvas nao centralizou no terminal: esperava perto de "
            f"({esperado.x():.0f},{esperado.y():.0f}), "
            f"veio ({centro.x():.0f},{centro.y():.0f})")
    finally:
        _encerrar(w, qapp)
