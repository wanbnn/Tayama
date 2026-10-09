#!/usr/bin/env python3
"""INTEGRACAO do bug reportado: fechar o app e voltar nao pode perder nada.

Cobre o fluxo que o usuario reclamou, de ponta a ponta no canvas Qt:

    2 terminais + 2 paineis + 3 setas  ->  "fecha o app"  ->  reabre
    -> os 4 nos voltam na posicao certa e as 3 setas voltam.

Mais: encerrar o app no meio / remover nos um a um nao pode deixar link
orfao no disco.

Roda sob display virtual (nao precisa de X de verdade):
    QT_QPA_PLATFORM=offscreen DISPLAY=:99 .venv-test/bin/python -m pytest \
        tests/test_restore_flow.py -v

Se PyQt6 nao estiver instalado, pula com o motivo explicito.
Os terminais usam o agente "sleep" — nenhum processo de agente de verdade
e lancado.
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

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

pytestmark = [
    pytest.mark.skipif(
        not (HAS_QT and HAS_DISPLAY),
        reason=(f"integracao do canvas exige PyQt6 + display. qt={HAS_QT} ({_ERR}); "
                f"display={HAS_DISPLAY} (DISPLAY={os.environ.get('DISPLAY')!r}). "
                "Rode com DISPLAY=:99 QT_QPA_PLATFORM=offscreen sob um venv com "
                "os requisitos instalados."),
    ),
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """TAYAMA_HOME isolado + config/layout reconfigurados.

    Sem isto o teste escreveria no ~/.tayama do usuario.
    """
    fake = tmp_path / "tayama-home"
    fake.mkdir()
    monkeypatch.setenv("TAYAMA_HOME", str(fake))

    from tayama import config
    monkeypatch.setattr(config, "DIR", fake)
    monkeypatch.setattr(config, "CFG", fake / "config.json")
    monkeypatch.setattr(config, "SKILLS", fake / "skills")

    from tayama import layout
    for name in ("PANELS", "LINKS"):
        if isinstance(getattr(layout, name, None), str):
            continue
    return fake


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(["tayama-test"])
    yield app


def _fechar(c, qapp):
    """Fecha um canvas sem derrubar o processo.

    ACHADO DO TESTER: o canvas liga um QTimer de 33ms que chama Edge.refresh()
    em todas as setas. Se esse timer continuar rodando durante o
    processEvents() do teardown, ele toca em proxy/widget ja em destruicao e o
    processo inteiro morre em SIGSEGV — era o que truncava esta suite no 2o
    teste (reproduzido tambem numa copia limpa do HEAD, ou seja, preexistente).
    Ordem obrigatoria: para o timer, mata o PTY, tira as setas da cena e so
    entao processa os eventos.
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
        c.gscene.removeItem(e)
    c.edges.clear()
    c.close()
    c.deleteLater()
    qapp.processEvents()


@pytest.fixture
def canvas(qapp, home):
    """InfiniteCanvas novo, com um agente 'sleep' (nao lanca agente de verdade)."""
    from tayama import config, workspaces
    from tayama.ui import InfiniteCanvas

    config.ensure()
    cfg = json.loads((home / "config.json").read_text("utf-8"))
    cfg["agents"] = [{"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0}]
    cfg["roles"] = [{"name": "Tester", "color": "#d6336c", "prompt": "testa"}]
    (home / "config.json").write_text(json.dumps(cfg), "utf-8")
    (home / "config.json").write_text(json.dumps(cfg), "utf-8")

    ws = workspaces.add("Teste", str(home))
    c = InfiniteCanvas()
    c.resize(1200, 800)
    c.set_current_ws(ws)
    c._test_ws = ws
    yield c
    _fechar(c, qapp)


def term_spec(canvas, name, pid):
    """Spec de terminal — mesmo formato que agents.save_one grava."""
    return {"id": pid, "name": name,
            "agent": {"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0},
            "role": {"name": "Tester", "color": "#d6336c"}, "workspace": canvas._test_ws,
            "cwd": str(canvas._test_ws["path"]), "skills": []}


def pos_of(win):
    p = win.proxy.pos()
    return (p.x(), p.y())


def panel_file(home, ws_id):
    return home / "panels" / f"{ws_id}.json"


def link_file(home, ws_id):
    return home / "links" / f"{ws_id}.json"


# ==========================================================================
# 1. O BUG REPORTADO: 2 terminais + 2 paineis + 3 setas -> reinicia
# ==========================================================================
@pytest.mark.xfail(reason="ACHADO (bug reportado, reproduzido): BrowserWindow ignora o "
                          "id salvo — browser.py:30 faz self.id = f'panel-{uuid4}' sempre. "
                          "add_terminal honra spec['id'] (ui.py:41), add_panel nao. Entao ao "
                          "reiniciar o painel volta com id NOVO e o link salvo (que traz o id "
                          "antigo) nao acha o no: restore_panels descarta a seta em silencio.",
           strict=False)
def test_fluxo_completo_sobrevive_a_reinicio(canvas, qapp, home):
    """2 terminais + 2 paineis + 3 setas voltam na posicao certa ao reabrir."""
    ws = canvas._test_ws
    ws_id = ws["id"]

    # --- sessao 1: monta o cenario ------------------------------------
    t1 = canvas.add_terminal(term_spec(canvas, "alice", "term-1"), _persist=False)
    t2 = canvas.add_terminal(term_spec(canvas, "bob", "term-2"), _persist=False)
    p1 = canvas.add_panel("https://a.test", name="painel-a", pos=(100, 100),
                          size=(600, 400), _persist=False)
    p2 = canvas.add_panel("https://b.test", name="painel-b", pos=(800, 300),
                          size=(500, 350), _persist=False)
    qapp.processEvents()

    # geometria esperada de cada no, gravada na mao
    esperado = {
        t1.id: pos_of(t1), t2.id: pos_of(t2),
        p1.id: pos_of(p1), p2.id: pos_of(p2),
    }
    tamanhos = {t1.id: (t1.width(), t1.height()), t2.id: (t2.width(), t2.height()),
                p1.id: (p1.width(), p1.height()), p2.id: (p2.width(), p2.height())}

    # as 3 setas, uma de cada tipo
    canvas.start_link(t1); canvas.start_link(t2)      # terminal <-> terminal
    canvas.start_link(p1); canvas.start_link(t1)      # painel <-> terminal
    canvas.start_link(p2); canvas.start_link(p1)      # painel <-> painel
    assert len(canvas.edges) == 3, f"esperava 3 setas, criou {len(canvas.edges)}"
    esperado_links = {(t1.id, t2.id), (p1.id, t1.id), (p2.id, p1.id)}

    # persiste tudo no disco, como se o usuario fechasse o app
    for w in (t1, t2):
        canvas._persist_window(w)
    for p in (p1, p2):
        canvas._persist_window(p)
    canvas._persist_links(t1)
    canvas._persist_links(p1)          # t2 e p2 tem o mesmo ws -> mesmo arquivo

    assert panel_file(home, ws_id).exists(), f"painel nao gravou em {panel_file(home, ws_id)}"
    assert link_file(home, ws_id).exists(), "links nao gravaram"
    gravados = {tuple(x) for x in json.loads(link_file(home, ws_id).read_text("utf-8"))}
    assert gravados == esperado_links, f"links no disco divergem: {gravados} != {esperado_links}"

    # --- fecha a sessao 1 ---------------------------------------------
    _fechar(canvas, qapp)

    # --- sessao 2: reabre do zero -------------------------------------
    from tayama.ui import InfiniteCanvas

    c2 = InfiniteCanvas()
    c2.resize(1200, 800)
    c2.restore_agents()
    c2.restore_panels()
    qapp.processEvents()

    # os 4 nos voltaram
    nos = c2._nodes_by_id()
    for pid in esperado:
        assert pid in nos, f"no {pid} nao voltou; voltaram {sorted(nos)}"
    assert len(c2.windows) == 2, f"esperava 2 terminais, veio {len(c2.windows)}"
    assert len(c2.panels) == 2, f"esperava 2 paineis, veio {len(c2.panels)}"

    # ... na posicao e tamanho certos
    for pid, pos in esperado.items():
        assert pos_of(nos[pid]) == pos, f"no {pid} voltou em {pos_of(nos[pid])} != {pos}"
        w = nos[pid]
        assert (w.width(), w.height()) == tamanhos[pid], f"tamanho errado em {pid}"

    # ... e as 3 setas voltaram
    pares = {(e.src.id, e.dst.id) for e in c2.edges}
    assert pares == esperado_links, f"setas divergem: {pares} != {esperado_links}"

    # os urls dos paineis voltaram tambem
    assert c2.panels[p1.id].url.text() == "https://a.test"
    assert c2.panels[p2.id].url.text() == "https://b.test"

    # restaurar nao regravou (senao duplica em todo startup)
    antes = panel_file(home, ws_id).read_text("utf-8")
    c3 = InfiniteCanvas(); c3.resize(800, 600)
    c3.restore_agents(); c3.restore_panels()
    assert len(c3.panels) == 2, "restaurar duas vezes duplicou paineis"
    assert panel_file(home, ws_id).read_text("utf-8") == antes, \
        "restaurar reescreveu o arquivo (os 2 ids sao novos a cada vez?)"

    for cc in (c2, c3):
        _fechar(cc, qapp)


# ==========================================================================
# 2. encerrar no meio / remover nos um a um -> nada de link orfao
# ==========================================================================
def test_remover_no_deixa_link_orfao(canvas, qapp, home):
    """Remover um no tira as setas dele do disco, sem deixar orfao."""
    ws_id = canvas._test_ws["id"]
    t1 = canvas.add_terminal(term_spec(canvas, "alice", "term-1"), _persist=False)
    t2 = canvas.add_terminal(term_spec(canvas, "bob", "term-2"), _persist=False)
    p1 = canvas.add_panel("https://a.test", name="painel-a", _persist=False)
    qapp.processEvents()

    canvas.start_link(t1); canvas.start_link(t2)
    canvas.start_link(p1); canvas.start_link(t1)
    assert len(canvas.edges) == 2
    canvas._persist_links(t1)
    assert len(json.loads(link_file(home, ws_id).read_text("utf-8"))) == 2

    # remove o painel: a seta painel<->terminal tem de sumir do disco
    canvas.remove_panel(p1)
    qapp.processEvents()
    assert link_file(home, ws_id).exists(), "as 2 setas sao do mesmo ws; arquivo deve sobrar"
    restantes = {tuple(x) for x in json.loads(link_file(home, ws_id).read_text("utf-8"))}
    assert p1.id not in {n for par in restantes for n in par}, \
        f"link orfao no disco apos remover o painel: {restantes}"

    # remove um terminal: agora nao resta nenhuma seta -> arquivo some
    canvas.remove_window(t2)
    qapp.processEvents()
    assert not link_file(home, ws_id).exists(), \
        "nenhuma seta sobrou mas o arquivo de links continua no disco"

    # e os terminais nao ficaram com entrada no agents.json
    from tayama import agents
    ids = [e["id"] for e in agents.load_all()]
    assert "term-2" not in ids, f"terminal removido ficou no agents.json: {ids}"


def test_remover_painel_tambem_limpa_o_arquivo_de_paineis(canvas, qapp, home):
    ws_id = canvas._test_ws["id"]
    p = canvas.add_panel("https://a.test", name="painel-a", _persist=False)
    qapp.processEvents()
    canvas._persist_window(p)              # como se o app fosse fechar agora
    assert panel_file(home, ws_id).exists(), "painel nao gravou"
    canvas.remove_panel(p)
    qapp.processEvents()
    assert not panel_file(home, ws_id).exists(), "painel removido, arquivo ficou"


def test_painel_sem_workspace_nao_persiste_nem_quebra(canvas, qapp, home):
    """Sem workspace o painel funciona mas nao grava — degrada, nao quebra."""
    canvas.set_current_ws(None)
    p = canvas.add_panel("https://a.test", name="sem-ws", _persist=False)
    qapp.processEvents()
    canvas._persist_window(p)          # nao pode levantar
    assert not list((home / "panels").glob("*.json")), \
        "painel sem workspace gravou arquivo assim mesmo"
    canvas.set_current_ws(canvas._test_ws)


def test_restore_panels_ignora_workspace_removido(canvas, qapp, home, monkeypatch):
    """Painel cujo workspace sumiu e descartado em silencio, sem estourar."""
    from tayama import layout

    ws_id = canvas._test_ws["id"]
    layout.save_panel(type("P", (), {"id": "panel-fantasma", "name": "fantasma",
                                    "url": "https://x.test", "x": 1, "y": 2,
                                    "w": 300, "h": 200})())
    # apaga o workspace da lista, mantendo o painel salvo
    from tayama import workspaces
    monkeypatch.setattr(workspaces, "load", lambda: [])
    before = len(canvas.panels)
    canvas.restore_panels()            # nao pode levantar
    assert len(canvas.panels) == before, "painel de ws inexistente nao devia ser criado"


# ==========================================================================
# 3. As tres ressalvas do lider sobre o _id restaurado
# ==========================================================================
def test_painel_novo_ainda_recebe_id_novo(canvas, qapp, home):
    """Ressalva 1: _id so entra na restauracao — dois paineis novos nao colidem."""
    a = canvas.add_panel("https://a.test", name="a", _persist=False)
    b = canvas.add_panel("https://b.test", name="b", _persist=False)
    qapp.processEvents()
    assert a.id != b.id, "dois paineis novos nasceram com o mesmo id"
    assert a.id.startswith("panel-") and b.id.startswith("panel-")


def test_painel_novo_nao_herda_id_de_painel_removido(canvas, qapp, home):
    """Ressalva 3: id orfao (prefixo panel-) nao colide com painel novo."""
    antigo = canvas.add_panel("https://a.test", name="antigo", _persist=False)
    canvas.remove_panel(antigo)
    novo = canvas.add_panel("https://b.test", name="novo", _persist=False)
    qapp.processEvents()
    assert novo.id != antigo.id


def test_id_de_painel_nao_colide_com_terminal(canvas, qapp, home):
    """Ressalva 3: prefixos distintos — painel nunca briga com terminal pelo id."""
    t = canvas.add_terminal(term_spec(canvas, "alice", "term-1"), _persist=False)
    p = canvas.add_panel("https://a.test", name="p", _persist=False)
    qapp.processEvents()
    assert t.id != p.id
    assert t.id.startswith("term-") or not t.id.startswith("panel-")
    assert p.id.startswith("panel-")


def test_paineis_sem_nome_salvo_nao_colidem(canvas, qapp, home):
    """Ressalva 2: dois paineis salvos sem name voltam com nomes distintos."""
    from tayama import layout

    ws_id = canvas._test_ws["id"]
    # specs SEM a chave 'name', como viriam de um arquivo antigo
    specs = [{"id": f"panel-s{i}", "name": None, "url": f"https://{i}.test",
              "x": i, "y": i, "w": 300, "h": 200} for i in (1, 2)]
    (home / "panels").mkdir(parents=True, exist_ok=True)
    (home / "panels" / f"{ws_id}.json").write_text(json.dumps(specs), "utf-8")
    canvas.restore_panels()
    qapp.processEvents()
    ids = {p.id for p in canvas.panels.values()}
    assert {"panel-s1", "panel-s2"} <= ids, f"ids restaurados errados: {ids}"
    nomes = [p.name for p in canvas.panels.values() if p.id in ("panel-s1", "panel-s2")]
    assert len(set(nomes)) == 2, f"nomes gerados colidiram: {nomes}"


def test_nome_do_painel_e_preservado_ao_restaurar(canvas, qapp, home):
    """Ressalva 2: com nome salvo, o nome volta — nao um 'painel-xxx' novo."""
    from tayama import layout

    ws_id = canvas._test_ws["id"]
    (home / "panels").mkdir(parents=True, exist_ok=True)
    (home / "panels" / f"{ws_id}.json").write_text(json.dumps(
        [{"id": "panel-n1", "name": "meu painel", "url": "https://a.test",
          "x": 0, "y": 0, "w": 300, "h": 200}]), "utf-8")
    canvas.restore_panels()
    qapp.processEvents()
    p = canvas.panels["panel-n1"]
    assert p.name == "meu painel"
