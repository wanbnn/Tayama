#!/usr/bin/env python3
"""CONTRATO DE ISOLAMENTO: cada workspace tem a SUA cena do canvas.

O QUE MUDOU
-----------
Antes, todos os workspaces viviam na MESMA QGraphicsScene: o workspace era só
uma etiqueta em win.workspace["id"], usada para agrupar na sidebar e escolher o
arquivo JSON. Clicar num workspace na sidebar não levava a lugar nenhum — o
usuário via todos os terminais de todos os ambientes misturados no mesmo canvas.

Agora existe uma cena por workspace: clicar num workspace troca a cena exibida
e só os nós daquele ambiente aparecem.

ESTES TESTES FIXAM O QUE QUEBRARIA
----------------------------------
Os registros canvas.windows / canvas.panels / canvas.edges continuam GLOBAIS (por
isso a Bridge e a sidebar seguem funcionando sem saber de cenas). Isso é
deliberado — e é o que permite manter verde a suite existente, que conta nós de
vários workspaces ao mesmo tempo (ver tests/test_workspace_clone_ui.py:341-364).

O risco real de uma cena por workspace é o SILÊNCIO: QGraphicsScene.removeItem()
numa cena errada é no-op (medido no Qt 6.11: só um qWarning, o item continua
preso). Um BrowserWindow preso é o que derruba o processo no teardown. Daí os
testes de remoção e de teardown abaixo.

    QT_QPA_PLATFORM=offscreen DISPLAY=:99 .venv/bin/python -m pytest \
        tests/test_workspace_scene_isolation.py -v

Se PyQt6 não estiver instalado, pula com o motivo explícito.
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

# --- PyQt6 tem de vir ANTES do QApplication (restrição do QtWebEngine) ------
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
        reason=(f"isolamento de cenas exige PyQt6 + display. "
                f"qt={HAS_QT} ({_ERR}); display={HAS_DISPLAY}"),
    ),
]


# ==========================================================================
# fixtures
# ==========================================================================
@pytest.fixture
def home(tmp_path, monkeypatch):
    """TAYAMA_HOME isolado — nada toca no ~/.tayama do usuário."""
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
def config_com_agente(home):
    """Config com o agente 'sleep' e o cargo 'Dev' — sem lançar agente de verdade."""
    cfg = json.loads((home / "config.json").read_text("utf-8"))
    cfg["agents"] = [{"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0}]
    cfg["roles"] = [{"name": "Dev", "color": "#58a6ff", "prompt": "desenvolve"}]
    (home / "config.json").write_text(json.dumps(cfg), "utf-8")
    return cfg


def _drain(qapp, n=5):
    """Processa a fila de eventos E a de deleteLater (v. os outros arquivos)."""
    from PyQt6.QtCore import QCoreApplication, QEvent
    for _ in range(n):
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _fechar(c, qapp):
    """Fecha um canvas com vários workspaces vivos.

    Ordem a mesma dos outros arquivos (e arazão dela): cada item sai da SUA
    cena. Usar c.gscene aqui seria no-op silencioso para tudo que não estivesse
    no workspace exibido — que é exatamente a maioria, num canvas multi-workspace.
    """
    from PyQt6.QtCore import QTimer
    for tmr in c.findChildren(QTimer):
        tmr.stop()
    for win in list(c.windows.values()):
        try:
            win.terminal.terminate()
        except Exception:
            pass
    for e in list(c.edges):
        sc = e.scene()
        if sc is not None:
            sc.removeItem(e)
    c.edges.clear()
    for pan in list(c.panels.values()):
        try:
            sc = pan.proxy.scene() if pan.proxy is not None else None
            if sc is not None:
                sc.removeItem(pan.proxy)
            pan.proxy = None
            pan.deleteLater()
        except RuntimeError:
            pass
    for win in list(c.windows.values()):
        sc = win.proxy.scene() if win.proxy is not None else None
        if sc is not None:
            sc.removeItem(win.proxy)
        win.proxy = None
    c.panels.clear()
    _drain(qapp)
    c.close()
    c.deleteLater()
    _drain(qapp)


@pytest.fixture
def canvas(qapp, home, config_com_agente):
    from tayama.ui import InfiniteCanvas
    c = InfiniteCanvas()
    c.resize(1200, 800)
    yield c
    _fechar(c, qapp)


def _spec(ws, name="alfa", pid="t1"):
    return {"id": pid, "name": name,
            "agent": {"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0},
            "role": {"name": "Dev", "color": "#58a6ff"},
            "workspace": ws, "cwd": ws["path"], "skills": []}


# ==========================================================================
# 1. O CONTRATO CENTRAL: cada nó mora na cena do SEU workspace
# ==========================================================================
def test_no_de_workspace_fica_na_cena_do_seu_workspace(qapp, home, canvas):
    """O núcleo da mudança.

    Dois workspaces, terminal + painel em cada. Cada proxy tem de estar na cena
    do SEU workspace — e cenas diferentes são, de fato, objetos diferentes.
    """
    from tayama import workspaces

    a = workspaces.add("Alfa", str(home))
    b = workspaces.add("Beta", str(home))

    ta = canvas.add_terminal(_spec(a, "alfa-1", "id-ta"))
    tb = canvas.add_terminal(_spec(b, "beta-1", "id-tb"))
    pa = canvas.add_panel("about:blank", name="painel-alfa", workspace=a)
    pb = canvas.add_panel("about:blank", name="painel-beta", workspace=b)
    qapp.processEvents()

    cena_a = canvas._scene_of(a)
    cena_b = canvas._scene_of(b)
    assert cena_a is not cena_b, "os dois workspaces acabaram na mesma cena"

    assert ta.proxy.scene() is cena_a, "terminal de Alfa nao foi para a cena de Alfa"
    assert pa.proxy.scene() is cena_a, "painel de Alfa nao foi para a cena de Alfa"
    assert tb.proxy.scene() is cena_b, "terminal de Beta nao foi para a cena de Beta"
    assert pb.proxy.scene() is cena_b, "painel de Beta nao foi para a cena de Beta"

    # e a cena de Alfa não tem nenhum nó de Beta (nem proxy nem seta)
    for item in cena_a.items():
        dono = getattr(item, "widget", None)
        dono = getattr(dono, "workspace", None) or {}
        assert dono.get("id") != b["id"], f"no de Beta vazou para a cena de Alfa: {item}"


def test_set_current_ws_troca_a_cena_exibida(qapp, home, canvas):
    """Clicar num workspace troca a cena — só a dele fica visível."""
    from tayama import workspaces

    a = workspaces.add("Alfa", str(home))
    b = workspaces.add("Beta", str(home))
    canvas.add_terminal(_spec(a, "alfa-1", "id-ta"))
    canvas.add_terminal(_spec(b, "beta-1", "id-tb"))
    qapp.processEvents()

    canvas.set_current_ws(b)
    qapp.processEvents()
    assert canvas.scene() is canvas._scene_of(b), "a cena exibida nao e a de Beta"

    visiveis = {id(i) for i in canvas.scene().items()}
    assert id(canvas.windows["id-ta"].proxy) not in visiveis, \
        "o terminal de Alfa continua visivel na cena de Beta"


def test_registros_globais_continuam_contendo_todos_os_workspaces(qapp, home, canvas):
    """GUARDA: os dicionarios nao foram filtrados por cena.

    A Bridge (bridge.py:30) e a sidebar (sidebar.py:36) leem canvas.windows /
    canvas.panels, e os testes existentes contam nos de vários workspaces ao
    mesmo tempo. Se alguém "otimizar" esses registros para só o workspace
    visível, esta falha.
    """
    from tayama import workspaces

    a = workspaces.add("Alfa", str(home))
    b = workspaces.add("Beta", str(home))
    canvas.add_terminal(_spec(a, "alfa-1", "id-ta"))
    canvas.add_terminal(_spec(b, "beta-1", "id-tb"))
    qapp.processEvents()

    assert {"id-ta", "id-tb"} <= set(canvas.windows), \
        "canvas.windows deixou de listar terminais de workspaces nao exibidos"
    assert {"id-ta", "id-tb"} <= set(canvas._nodes_by_id()), \
        "_nodes_by_id deixou de listar nos de workspaces nao exibidos"


# ==========================================================================
# 2. Pan/zoom: o enquadramento é de cada workspace (decisão do usuário)
# ==========================================================================
def test_pan_e_zoom_ficam_memorizados_por_workspace(qapp, home, canvas):
    """Cada workspace lembra o próprio enquadramento.

    Necessário porque cenas com o mesmo sceneRect PRESERVAM o transform do view
    na troca (medido): sem isto o pan/zoom de Alfa vazaria para Beta.
    """
    from PyQt6.QtCore import QPointF
    from tayama import workspaces

    a = workspaces.add("Alfa", str(home))
    b = workspaces.add("Beta", str(home))

    canvas.set_current_ws(a); qapp.processEvents()
    canvas.scale(2.0, 2.0); canvas.centerOn(QPointF(1000, -500))
    antes = canvas._read_view()
    centro_antes = antes[1]

    canvas.set_current_ws(b); qapp.processEvents()
    canvas.scale(0.5, 0.5); canvas.centerOn(QPointF(-2000, 700))

    canvas.set_current_ws(a); qapp.processEvents()
    centro_de_volta, escala = canvas._read_view()[1], canvas.transform().m11()
    assert abs(escala - antes[0].m11()) < 1e-6, \
        f"o zoom de Alfa nao voltou: esperava {antes[0].m11()}, veio {escala}"
    # Tolerância em unidades de tela: mapToScene cai na grade de pixels, então
    # no zoom 2.0 o centro volta meio pixel de cena (0.5) fora — é arredondamento
    # do view, não perda de estado. 2 px de tela cobrem esse arredondamento.
    tol = 2.0 / escala
    assert (centro_de_volta - centro_antes).manhattanLength() <= tol, \
        f"o centro de Alfa nao voltou: esperava {centro_antes}, veio {centro_de_volta}"


# ==========================================================================
# 3. O bug do removeItem na cena errada (silencioso — o que os testes pegam)
# ==========================================================================
def test_remove_window_de_workspace_nao_exibido_solta_o_proxy(qapp, home, canvas, capsys):
    """Fechar um nó de um workspace que NÃO está na tela precisa soltá-lo.

    Com uma cena por workspace, remover de self.gscene seria no-op silencioso:
    o proxy ficaria preso à cena do próprio workspace, e um BrowserWindow preso
    é o que derruba o processo no teardown (v. os docstrings de _fechar).
    """
    from tayama import workspaces

    a = workspaces.add("Alfa", str(home))
    b = workspaces.add("Beta", str(home))
    canvas.add_terminal(_spec(a, "alfa-1", "id-ta"))
    oculto = canvas.add_terminal(_spec(b, "beta-1", "id-tb"))
    canvas.add_panel("about:blank", name="painel-b", workspace=b)
    qapp.processEvents()

    canvas.set_current_ws(a); qapp.processEvents()      # B agora está OCULTO
    assert oculto.proxy.scene() is canvas._scene_of(b)

    canvas.remove_window(oculto)
    qapp.processEvents()

    assert oculto.proxy.scene() is None, \
        "o proxy ficou preso na cena do workspace oculto apos remove_window"
    assert "id-tb" not in canvas.windows, "remove_window nao tirou a janela do registro"
    # o painel do mesmo workspace oculto tem de continuar solto e funcional
    assert "id-tb" not in canvas.panels


def test_remove_edge_de_workspace_nao_exibido_solta_a_seta(qapp, home, canvas):
    """Mesma regra para as setas: uma Edge sai da cena dela."""
    from tayama import workspaces

    a = workspaces.add("Alfa", str(home))
    b = workspaces.add("Beta", str(home))
    canvas.set_current_ws(b)
    t1 = canvas.add_terminal(_spec(b, "um", "id-1"))
    t2 = canvas.add_terminal(_spec(b, "dois", "id-2"))
    canvas.start_link(t1); canvas.start_link(t2)
    qapp.processEvents()
    assert len(canvas.edges) == 1, "premissa: a seta nao foi criada"

    canvas.set_current_ws(a); qapp.processEvents()      # B oculto
    e = canvas.edges[0]
    canvas.remove_edge(e)
    qapp.processEvents()
    assert e.scene() is None, "a seta ficou presa na cena do workspace oculto"


# ==========================================================================
# 4. Link entre workspaces: proibido, com aviso (decisão do usuário)
# ==========================================================================
def test_link_cross_workspace_e_recusado_e_avisa(qapp, home, canvas):
    """Workspaces são ambientes separados: a conexão é recusada E o usuário é avisado.

    Antes desta mudança não havia nenhuma checagem e a seta cross-workspace era
    criada — ela não teria onde ser desenhada, com as pontas em cenas diferentes.
    """
    from tayama import workspaces

    a = workspaces.add("Alfa", str(home))
    b = workspaces.add("Beta", str(home))
    ta = canvas.add_terminal(_spec(a, "alfa-1", "id-ta"))
    tb = canvas.add_terminal(_spec(b, "beta-1", "id-tb"))

    avisos = []
    canvas.notice.connect(avisos.append)

    canvas.start_link(ta)      # origem em Alfa
    canvas.start_link(tb)      # destino em Beta -> recusado
    qapp.processEvents()

    assert canvas.edges == [], f"a seta cross-workspace foi criada: {canvas.edges}"
    assert len(avisos) == 1, f"o usuario nao foi avisado (avisos={avisos})"
    assert "workspaces diferentes" in avisos[0], f"aviso pouco claro: {avisos[0]!r}"
    # e o modo de origem foi desarmado: o segundo clique não fica pendurado
    assert canvas._link_src is None


def test_link_dentro_do_workspace_continua_valendo(qapp, home, canvas):
    """Anti-regressão: a regra nova não pode quebrar a conexão normal."""
    from tayama import workspaces

    a = workspaces.add("Alfa", str(home))
    canvas.set_current_ws(a)
    t1 = canvas.add_terminal(_spec(a, "um", "id-1"))
    t2 = canvas.add_terminal(_spec(a, "dois", "id-2"))
    canvas.start_link(t1); canvas.start_link(t2)
    qapp.processEvents()

    assert len(canvas.edges) == 1, "a conexao dentro do mesmo workspace falhou"
    e = canvas.edges[0]
    assert e.scene() is canvas._scene_of(a), "a seta nao foi para a cena do workspace"
    assert e.src is t1 and e.dst is t2


def test_link_antigo_cross_workspace_no_disco_e_descartado_no_restore(qapp, home, canvas):
    """Migração: links/<ws>.json pode ter pares cross-workspace de antes.

    _persist_links gravava no arquivo da ORIGEM sem checar o destino, então
    esses pares existem em disco. No restore precisam ser descartados em vez de
    virarem setas sem sentido.
    """
    from tayama import agents, layout, workspaces

    a = workspaces.add("Alfa", str(home))
    b = workspaces.add("Beta", str(home))
    canvas.set_current_ws(a)
    t1 = canvas.add_terminal(_spec(a, "um", "id-1"))
    canvas.add_terminal(_spec(b, "dois", "id-2"))
    qapp.processEvents()
    canvas._persist_window(t1)

    # par cross-workspace escrito a mao, como o codigo antigo faria
    layout.save_links(a["id"], [("id-1", "id-2")])
    canvas.edges.clear()

    canvas.restore_panels()
    qapp.processEvents()

    assert canvas.edges == [], \
        f"o restore recriou uma conexao cross-workspace de dado antigo: {canvas.edges}"
    # e o nó de Alfa sobrevive intacto (não foi removido por causa do par)
    assert [n["id"] for n in agents.load_one(a["id"])] == ["id-1"]


# ==========================================================================
# 5. Nós sem workspace / cena vazia
# ==========================================================================
def test_current_ws_none_mostra_uma_cena_vazia(qapp, home, canvas):
    """Sem nenhum workspace selecionado o app ainda mostra um canvas (com grid).

    current_ws is None é um estado real (test_restore_flow e
    test_workspace_clone_ui usam), e a cena sentinel '' existe para isso — não
    para o app ficar sem cena nenhuma.
    """
    canvas.set_current_ws(None)
    qapp.processEvents()
    assert canvas.scene() is not None, "sem workspace o canvas ficou sem cena"
    assert canvas.scene() is canvas._scene_of(None)
    assert canvas.scene().sceneRect().width() == 100000, "a cena sem ws perdeu o grid"


def test_painel_sem_workspace_nao_persiste_e_cai_na_cena_sentinel(qapp, home, canvas):
    """Painel sem workspace: funcional, visível, e sem arquivo no disco.

    Cobre o sentinela de add_panel(workspace=...): passar None explicitamente
    tem de ser diferente de "não passei nada" (que usa o corrente).
    """
    canvas.set_current_ws(None)
    p = canvas.add_panel("about:blank", name="sem-ws", workspace=None)
    qapp.processEvents()

    assert p.workspace is None
    assert p.proxy.scene() is canvas._scene_of(None), "o painel sem ws nao foi para a cena sentinel"
    assert not list((home / "panels").glob("*.json")), "painel sem ws gravou arquivo"


def test_no_nascendo_em_cena_nao_exibida_cai_perto_do_conteudo_dela(qapp, home, canvas):
    """mapToScene() é relativo à cena EXIBIDA — não pode ser usado.

    Medido: com a cena de Alfa visível e um proxy em Beta na posição (1100,1100),
    mapToScene devolvia (318,238) — uma posição sem sentido em Beta. Um terminal
    nascendo em cena oculta precisa cair sobre o conteúdo DELE.
    """
    from PyQt6.QtCore import QPointF
    from tayama import workspaces

    a = workspaces.add("Alfa", str(home))
    b = workspaces.add("Beta", str(home))
    canvas.set_current_ws(a); qapp.processEvents()

    primeiro = canvas.add_terminal(_spec(b, "beta-1", "id-tb"))
    primeiro.proxy.setPos(QPointF(1100, 1100)); qapp.processEvents()

    segundo = canvas.add_terminal(_spec(b, "beta-2", "id-tb2"))
    qapp.processEvents()

    p = segundo.proxy.pos()
    assert abs(p.x() - 1100) < 600 and abs(p.y() - 1100) < 600, \
        f"o terminal nao nasceu perto do conteudo de Beta: saiu em ({p.x():.0f},{p.y():.0f}) " \
        f"para um conteudo em (1100,1100) — sinal de que usou mapToScene da cena errada"


# ==========================================================================
# 6. Restore não mexe no workspace corrente (invariante explícita)
# ==========================================================================
def test_restore_panels_nao_altera_o_workspace_corrente(qapp, home, canvas):
    """Antes isto era efeito colateral do hack previous_ws; hoje é invariante.

    add_panel recebe o workspace explicitamente, então materializar um clone não
    pode mais trocar o foco de quem está usando o app.
    """
    from tayama import workspaces

    original = workspaces.add("Original", str(home))
    canvas.set_current_ws(original)
    canvas.add_terminal(_spec(original, "alfa", "id-ta"))
    canvas.add_panel("about:blank", name="painel-original")
    canvas._persist_links(canvas.windows["id-ta"])
    qapp.processEvents()

    outro = workspaces.add("Outro", str(home))
    canvas.set_current_ws(outro)
    antes = canvas.current_ws["id"]

    clone = workspaces.duplicate(original)
    canvas.restore_agents(clone["id"])
    canvas.restore_panels(clone["id"])
    qapp.processEvents()

    assert canvas.current_ws is not None, "materializar o clone zerou o workspace corrente"
    assert canvas.current_ws["id"] == antes, \
        f"o ws corrente mudou: 'Outro' -> {canvas.current_ws['name']!r}"

    # e um painel novo ainda nasce onde o usuário está
    novo = canvas.add_panel("about:blank", name="depois-do-clone")
    qapp.processEvents()
    assert (novo.workspace or {}).get("id") == antes, \
        "o painel seguinte ao clone nasceu no workspace errado"


def test_workspace_removido_descarta_a_cena_e_a_enquadragem(qapp, home, canvas):
    """Cena e enquadramento de um workspace removido não podem ficar vazando.

    Sem forget_workspace(), canvas.scenes cresceria a cada remoção, com cenas
    vazias segurando memória e o transform de um workspace que não existe mais.
    """
    from tayama import workspaces

    a = workspaces.add("Alfa", str(home))
    canvas.set_current_ws(a); qapp.processEvents()
    canvas.scale(1.7, 1.7)

    assert a["id"] in canvas.scenes, "premissa: a cena de Alfa nao foi criada"

    canvas.forget_workspace(a["id"])

    assert a["id"] not in canvas.scenes, "a cena de Alfa ficou em canvas.scenes"
    assert a["id"] not in canvas._views, "o enquadramento de Alfa ficou em canvas._views"
    # e o canvas continua com uma cena válida (caiu na sentinel)
    assert canvas.scene() is canvas._scene_of(None)