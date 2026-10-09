#!/usr/bin/env python3
"""O DEFEITO da duplicacao na tela: o clone nascia so no disco.

Relato do usuario: ao duplicar um workspace pela sidebar, os agentes, o painel
web e as setas do clone NAO apareciam — so apareciam depois de reiniciar o app.

Por que os testes de workspaces.py (tests/test_workspace_clone.py) nao pegaram
-------------------------------------------------------------------------------
duplicate() grava tudo certo em disco: ids novos, links remapeados. O defeito
esta um nivel acima, no canvas: workspaces.duplicate() escreve
agents/<novo>.json, panels/<novo>.json e links/<novo>.json, mas o objeto vivo so
nasce quando alguem materializa esse conteudo — e a Sidebar.refresh() monta a
arvore a partir de canvas.windows / canvas.panels, nao do disco. Sem
materializar, o clone aparece VAZIO ate reiniciar.

Estes testes exercitam o contrato no nivel do canvas (restore_agents(ws_id) +
restore_panels(ws_id)), que e o caminho que main.py duplicate_workspace usa:

    workspaces.duplicate(ws)  ->  canvas.restore_agents(novo_id)
                                 canvas.restore_panels(novo_id)

O cenario mais importante e o 2: restore_panels() chama set_current_ws() DENTRO
do laco (o painel nasce no workspace dele) e, sem cuidado, materializar o clone
roubava o foco de quem esta usando o app — o proximo painel novo nasceria no
workspace errado.

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_workspace_clone_ui.py -v

Se PyQt6 nao estiver instalado, pula com o motivo explicito.
Os terminais usam o agente "sleep" — nenhum agente de verdade e lancado.
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

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
        reason=(f"materializacao do clone no canvas exige PyQt6 + display. "
                f"qt={HAS_QT} ({_ERR}); display={HAS_DISPLAY}. Rode com "
                "QT_QPA_PLATFORM=offscreen sob um venv com os requisitos."),
    ),
]


# ==========================================================================
# fixtures
# ==========================================================================
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
def config_com_agente(home):
    """Config com o agente 'sleep' e o cargo 'Dev' — restaura sem estourar."""
    cfg = json.loads((home / "config.json").read_text("utf-8"))
    cfg["agents"] = [{"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0}]
    cfg["roles"] = [{"name": "Dev", "color": "#58a6ff", "prompt": "desenvolve"}]
    (home / "config.json").write_text(json.dumps(cfg), "utf-8")
    return cfg


def _drain(qapp, n=5):
    """Processa a fila de eventos E a de deleteLater.

    processEvents() sozinho nao processa DeferredDelete: os widgets marcados
    com deleteLater() continuam vivos ate o proximo nivel do event loop, e e
    ai que o QtWebEngine destrói QQuickWidget dentro de sendPostedEvents.
    """
    from PyQt6.QtCore import QCoreApplication, QEvent
    for _ in range(n):
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _tirar_da_cena(item):
    """Remove o item da cena DELE — nao da cena exibida.

    QGraphicsScene.removeItem() numa cena errada e NO-OP SILENCIOSO (medido no
    Qt 6.11: so um qWarning, o item continua preso). Como o canvas agora tem UMA
    CENA POR WORKSPACE, usar c.gscene deixaria os nos dos workspaces nao
    exibidos grudados na cena — e um BrowserWindow preso e o que derruba o
    processo (v. o docstring de _fechar).
    """
    sc = item.scene() if item is not None else None
    if sc is not None:
        sc.removeItem(item)


def _fechar(c, qapp):
    """Fecha um canvas sem derrubar o processo.

    ACHADO DO TESTER (suite nao chegava a um veredicto): o crash nao era so o
    QTimer de Edge.refresh() — faltava o BrowserWindow. Este cenario traz
    painel web, e um painel e um QWebEngineView: destruido dentro do
    processEvents() do teardown, ele desce QQuickWidget/~QQuickWindow ->
    QQuickRenderControlPrivate::windowDestroyed e o processo morre em SIGSEGV,
    DEPOIS do pytest imprimir o resumo. Era resultado truncado: '5 passed'
    com o core dump logo em seguida.

    Ordem obrigatoria (medida, nao deduzida):
      1. para o timer de 33ms das setas;
      2. mata o PTY dos terminais;
      3. tira as setas da cena;
      4. tira cada BrowserWindow da cena + deleteLater;
      5. DRAINA os deleteLater e so entao fecha o canvas.

    Os passos 3 e 4 tiram cada item da SUA cena (_tirar_da_cena): o canvas tem
    uma cena por workspace, entao remover de c.gscene seria no-op silencioso
    para tudo que nao estivesse no workspace exibido.

    Nao matar a QWebEnginePage na mao (page.deleteLater()): o QWebEngineView
    continua emitindo loadFinished de um load em voo e aborta em qt_assert, e
    sobra QSocketNotifier orfao do Chromium. Deixar o BrowserWindow levar a
    page junto resolve, sem o aviso 'WebEnginePage still not deleted'.
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
        _tirar_da_cena(e)
    c.edges.clear()
    for pan in list(c.panels.values()):
        try:
            _tirar_da_cena(pan.proxy)
            pan.proxy = None
            pan.deleteLater()
        except RuntimeError:
            pass          # painel ja destruido: nada a fazer
    for win in list(c.windows.values()):
        _tirar_da_cena(win.proxy)
        win.proxy = None
    c.panels.clear()
    _drain(qapp)
    c.close()
    c.deleteLater()
    _drain(qapp)


@pytest.fixture
def canvas(qapp, home, config_com_agente):
    """InfiniteCanvas de verdade (QApplication offscreen + QtWebEngine)."""
    from tayama.ui import InfiniteCanvas
    c = InfiniteCanvas()
    c.resize(1200, 800)
    yield c
    _fechar(c, qapp)


def _spec_terminal(ws, name="alfa", pid="term-orig"):
    return {"id": pid, "name": name,
            "agent": {"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0},
            "role": {"name": "Dev", "color": "#58a6ff"},
            "workspace": ws, "cwd": ws["path"], "skills": []}


def _cenario_no_canvas(canvas, ws):
    """O que o usuario tem na tela: 1 terminal, 1 painel web e 1 seta entre eles,
    tudo PERSISTIDO no workspace (como se o app fosse fechar agora).

    Monta pelo canvas de proposito: o defeito e sobre objetos vivos, entao o
    cenario tem de nascer de janela viva, nao de JSON escrito a mao.
    """
    canvas.set_current_ws(ws)
    term = canvas.add_terminal(_spec_terminal(ws))          # _persist=True
    painel = canvas.add_panel("about:blank", name="painel")  # _persist=True
    canvas._persist_window(term)
    canvas._persist_window(painel)
    canvas.start_link(painel)          # painel -> terminal
    canvas.start_link(term)
    canvas._persist_links(painel)
    return term, painel


def _materializar(canvas, ws_id):
    """O caminho que main.py duplicate_workspace usa para trazer o clone a tela."""
    canvas.restore_agents(ws_id)
    canvas.restore_panels(ws_id)


def _ids_dos_nos_do_ws(canvas, ws_id):
    """(ids de terminais, ids de paineis) vivos no canvas que pertencem a ws_id."""
    terms = {w.id for w in canvas.windows.values() if (w.workspace or {}).get("id") == ws_id}
    pans = {p.id for p in canvas.panels.values() if (getattr(p, "workspace", None) or {}).get("id") == ws_id}
    return terms, pans


def _pares(canvas):
    return {(e.src.id, e.dst.id) for e in canvas.edges}


@pytest.fixture
def uuid_deterministico(monkeypatch):
    """Fixa os ids que workspaces.duplicate() sorteia.

    Sem isso o teste do cenario 2 e FLAKY: um restore_panels() sem filtro
    percorre panels/<ws_id>.json em ordem alfabetica e chama set_current_ws()
    por painel, entao o ULTIMO processado define o workspace corrente. Com ids
    aleatorios, o clone viraria o corrente so ~50% das vezes — bug intermitente
    passa como 'de vez em quando some'. Aqui o original ('aaaaorig1') sempre
    ordena antes do clone ('zzclone01'), entao o bug aparece sempre.
    """
    from tayama import workspaces
    hexes = iter(["zzclone01", "itemaaa1", "itemaaa2", "itemaaa3",
                  "itemaaa4", "itemaaa5", "itemaaa6", "itemaaa7"])

    class _Uuid:
        @staticmethod
        def uuid4():
            try:
                h = next(hexes)
            except StopIteration:
                h = "f" + uuid.uuid4().hex[:7]
            return SimpleNamespace(hex=h)

    monkeypatch.setattr(workspaces, "uuid", _Uuid)


# ==========================================================================
# 1. duplicar + materializar: os nos do clone e a seta precisam existir
# ==========================================================================
def test_clone_materializado_traz_terminal_painel_e_seta(qapp, home, canvas):
    """O DEFEITO: logo apos duplicar, o clone tem de estar no canvas como objeto
    vivo — terminal, painel e a seta entre eles, com ids novos."""
    from tayama import workspaces

    original = workspaces.add("Original", str(home))
    term, painel = _cenario_no_canvas(canvas, original)
    assert _pares(canvas) == {(painel.id, term.id)}, "premissa: a seta do original nao foi criada"

    clone = workspaces.duplicate(original)
    _materializar(canvas, clone["id"])
    qapp.processEvents()

    # --- o clone virou objeto vivo, no workspace novo ---
    terms, pans = _ids_dos_nos_do_ws(canvas, clone["id"])
    assert len(terms) == 1, f"terminal do clone nao materializou; canvas tem {sorted(canvas.windows)}"
    assert len(pans) == 1, f"painel do clone nao materializou; canvas tem {sorted(canvas.panels)}"

    # --- ids do clone NAO podem ser os do original (senao as setas cruzariam) ---
    id_term_clone, id_painel_clone = next(iter(terms)), next(iter(pans))
    assert id_term_clone != term.id, "o terminal do clone reusou o id do original"
    assert id_painel_clone != painel.id, "o painel do clone reusou o id do original"

    # --- a seta do clone tambem voltou, ligando os nos DO CLONE ---
    assert (id_painel_clone, id_term_clone) in _pares(canvas), \
        f"a seta do clone nao foi recriada; edges={sorted(_pares(canvas))}"

    # --- e o Original continua intacto na tela ---
    assert term.id in canvas.windows and painel.id in canvas.panels, \
        "materializar o clone desmonto o original"
    assert (painel.id, term.id) in _pares(canvas), "a seta do original sumiu"


def test_materializar_o_clone_nao_altera_o_workspace_corrente(qapp, home, canvas,
                                                               uuid_deterministico):
    """O DEFEITO FINO: restore_panels() chama set_current_ws() dentro do logo
    (o painel nasce no workspace dele). Materializar o clone nao pode ROUBAR o
    foco de quem esta usando o app — o proximo painel novo nasceria no clone.

    Cenario real: o usuario esta trabalhando em 'Outro', nao no Original.
    """
    from tayama import workspaces

    # id fixo ('aaaa...') para ordenar ANTES do clone no disco — ver o fixture
    # uuid_deterministico. workspaces.add() sorteia o id e nao serviria.
    original = {"id": "aaaaorig1", "name": "Original", "path": str(home)}
    workspaces.save([original])
    _cenario_no_canvas(canvas, original)
    outro = workspaces.add("Outro", str(home))
    canvas.set_current_ws(outro)
    antes = canvas.current_ws["id"]

    clone = workspaces.duplicate(original)
    _materializar(canvas, clone["id"])
    qapp.processEvents()

    assert canvas.current_ws is not None, "materializar o clone zerou o workspace corrente"
    assert canvas.current_ws["id"] == antes, \
        f"o ws corrente mudou: 'Outro' -> {canvas.current_ws['name']!r}"
    assert canvas.current_ws["id"] != clone["id"], "o ws corrente virou o clone"

    # e o clone ficou utilizavel: um painel novo ainda nasce em 'Outro'
    novo = canvas.add_panel("about:blank", name="depois-do-clone", _persist=True)
    qapp.processEvents()
    assert (novo.workspace or {}).get("id") == antes, \
        "o painel seguinte ao clone nasceu no workspace errado"


# ==========================================================================
# 2. duplicar duas vezes: os clones coexistem, ids distintos entre si
# ==========================================================================
def test_duas_duplicacoes_coexistem_sem_embolar_os_ids(qapp, home, canvas):
    """Clonar o mesmo workspace duas vezes: cada clone com os SEUS nos, ids
    diferentes entre si e do original, e o original intacto (na tela e no disco)."""
    from tayama import agents, layout, workspaces

    original = workspaces.add("Original", str(home))
    term, painel = _cenario_no_canvas(canvas, original)
    disco_antes = {
        "agentes": agents.load_one(original["id"]),
        "paineis": layout.load_panels(original["id"]),
        "links": sorted(layout.load_links(original["id"])),
    }

    clone1 = workspaces.duplicate(original)
    _materializar(canvas, clone1["id"])
    clone2 = workspaces.duplicate(original)
    _materializar(canvas, clone2["id"])
    qapp.processEvents()

    # --- cada clone tem exatamente 1 terminal, 1 painel e a seta entre eles ---
    vistos = {}
    for nome, clone in (("clone1", clone1), ("clone2", clone2)):
        terms, pans = _ids_dos_nos_do_ws(canvas, clone["id"])
        assert len(terms) == 1, f"{nome}: esperava 1 terminal, veio {len(terms)} ({sorted(terms)})"
        assert len(pans) == 1, f"{nome}: esperava 1 painel, veio {len(pans)} ({sorted(pans)})"
        par = (next(iter(pans)), next(iter(terms)))
        assert par in _pares(canvas), \
            f"{nome}: a seta nao aponta para os nos do clone; edges={sorted(_pares(canvas))}"
        vistos[nome] = (next(iter(terms)), next(iter(pans)))

    # --- ids distintos entre os dois clones e contra o original ---
    t1, p1 = vistos["clone1"]
    t2, p2 = vistos["clone2"]
    assert t1 != t2, f"os dois clones usaram o mesmo id de terminal ({t1})"
    assert p1 != p2, f"os dois clones usaram o mesmo id de painel ({p1})"
    for nome, tid, pid in (("clone1", t1, p1), ("clone2", t2, p2)):
        assert tid != term.id, f"{nome} reusou o id de terminal do original"
        assert pid != painel.id, f"{nome} reusou o id de painel do original"

    # --- o original sobreviveu, na tela e no disco ---
    assert term.id in canvas.windows and painel.id in canvas.panels, \
        "duplicar duas vezes desmonto o original na tela"
    assert (painel.id, term.id) in _pares(canvas), "a seta do original sumiu"
    assert agents.load_one(original["id"]) == disco_antes["agentes"], \
        "o agents.json do original foi reescrito"
    assert layout.load_panels(original["id"]) == disco_antes["paineis"], \
        "o panels.json do original foi reescrito"
    assert sorted(layout.load_links(original["id"])) == disco_antes["links"], \
        "o links.json do original foi reescrito"


# ==========================================================================
# 3. workspace sem conteudo: nao quebra e nao cria lixo
# ==========================================================================
def test_clonar_workspace_vazio_nao_quebra_e_nao_cria_sujeira(qapp, home, canvas):
    """Um workspace sem agente/painel/seta persistidos pode ser clonado: o clone
    entra na lista, nao cria arquivo vazio e materializa sem estourar nem
    inventar no no canvas."""
    from tayama import workspaces

    original = workspaces.add("Vazio", str(home))
    nodes_antes = set(canvas._nodes_by_id())

    clone = workspaces.duplicate(original)     # nao pode levantar
    assert clone["id"] != original["id"]
    assert clone["name"] == "Vazio (copia)"
    assert [w["id"] for w in workspaces.load()] == [original["id"], clone["id"]], \
        "o clone do workspace vazio nao entrou na lista (ou o original sumiu)"

    _materializar(canvas, clone["id"])         # nao pode levantar
    qapp.processEvents()

    # clone vazio => nenhum no novo e nenhum arquivo criado
    assert set(canvas._nodes_by_id()) == nodes_antes, \
        f"materializar um clone vazio inventou nos: {sorted(set(canvas._nodes_by_id()) - nodes_antes)}"
    assert _ids_dos_nos_do_ws(canvas, clone["id"]) == (set(), set())
    for sub in ("agents", "panels", "links"):
        f = home / sub / f"{clone['id']}.json"
        assert not f.exists(), f"clone vazio criou arquivo lixo: {f}"


def test_clonar_workspace_vazio_nao_troca_o_ws_corrente(qapp, home, canvas):
    """O mesmo cenario 2, sem workspace corrente nenhum: materializar um clone
    vazio nao pode inventar um 'corrente' do nada."""
    from tayama import workspaces

    original = workspaces.add("Vazio", str(home))
    canvas.set_current_ws(None)
    clone = workspaces.duplicate(original)
    _materializar(canvas, clone["id"])
    qapp.processEvents()
    assert canvas.current_ws is None, \
        f"materializar um clone vazio inventou ws corrente: {canvas.current_ws}"


# ==========================================================================
# 4. robustez do caminho novo
# ==========================================================================
def test_materializar_o_mesmo_clone_duas_vezes_nao_duplica_seta(qapp, home, canvas):
    """O caminho novo (restore_agents(ws_id)/restore_panels(ws_id)) tem de ser
    idempotente: materializar o mesmo clone duas vezes nao pode criar uma segunda
    seta nem um segundo no com o mesmo id.

    BUG ACHADO E CORRIGIDO AQUI: a guarda de restore_panels comparava OBJETO
    (any(e.src is src and e.dst is dst)) em vez de id. Ao materializar 2x, o
    painel era recriado (add_panel faz um BrowserWindow novo e sobrescreve
    canvas.panels[id]), entao a seta antiga apontava para um objeto que ja tinha
    saido do dicionario e a guarda nao a via: nascia uma 2a seta identica e o
    widget antigo ficava orfao na cena. Hoje restore_panels/restore_agents
    pulam o no ja materializado e o dedup das setas e por par de IDS.
    """
    from tayama import workspaces

    original = workspaces.add("Original", str(home))
    _cenario_no_canvas(canvas, original)
    clone = workspaces.duplicate(original)
    _materializar(canvas, clone["id"])
    qapp.processEvents()

    terms, pans = _ids_dos_nos_do_ws(canvas, clone["id"])
    par = (next(iter(pans)), next(iter(terms)))
    edges_1 = len(canvas.edges)

    _materializar(canvas, clone["id"])          # segunda vez, mesmo clone
    qapp.processEvents()

    assert len(canvas.edges) == edges_1, \
        f"materializar 2x criou seta duplicada: {sorted(_pares(canvas))}"
    assert len([e for e in canvas.edges
                if (e.src.id, e.dst.id) == par]) == 1, \
        f"a seta {par} do clone aparece mais de uma vez"
    assert len([w for w in canvas.windows.values()
                if (w.workspace or {}).get("id") == clone["id"]]) == 1, \
        "materializar 2x deixou dois terminais do mesmo clone"
    assert len([p for p in canvas.panels.values()
                if (getattr(p, "workspace", None) or {}).get("id") == clone["id"]]) == 1, \
        "materializar 2x deixou dois paineis do mesmo clone"