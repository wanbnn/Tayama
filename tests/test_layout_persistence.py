#!/usr/bin/env python3
"""Persistencia de paineis web e conexoes (setas) — testes de I/O puro.

NAO importa Qt, NAO abre display, NAO sobe servidor. Cobre apenas o modulo
tayama/layout.py (o mesmo estilo de agents.py: JSON em ~/.tayama, degrada em
vez de estourar quando o arquivo falta ou esta corrompido).

Isolamento: todo teste roda com TAYAMA_HOME apontando para um tmp_path, de
modo que nada toca no ~/.tayama real. Ver fixture `home`.

Rodar:
    python3 -m pytest tests/test_layout_persistence.py -v

Contrato coberto (tayama/layout.py):
    save_panel(win)                 persiste um painel (win tipo BrowserWindow)
    remove_panel(panel_id, ws_id)   remove um painel pelo id
    load_panels(ws_id)              -> [{id, name, url, x, y, w, h}]
    load_all_panels()               -> todos os paineis de todos os workspaces
    save_links(ws_id, pairs)        persiste conexoes; pairs = [(src, dst), ...]
    load_links(ws_id)               -> [(src, dst), ...]
    Arquivos: <TAYAMA_HOME>/panels/<ws_id>.json e /links/<ws_id>.json
    Workspace removido limpa os dois.
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

WS = "ws-teste"


# --------------------------------------------------------------------------
# isolamento: TAYAMA_HOME -> tmp_path
# --------------------------------------------------------------------------
@pytest.fixture
def home(tmp_path, monkeypatch):
    """TAYAMA_HOME apontando para tmp_path + reconfig do config ja importado.

    A env var e setada ANTES do primeiro import de tayama.config e, como o
    config pode ja ter sido importado por outro teste da mesma sessao, os
    atributos de Path ja materializados sao remapeados tambem. O patch de
    layout cobre o caso de o modulo guardar `PANELS = config.DIR / "panels"`
    no import em vez de resolver em tempo de chamada.
    """
    fake = tmp_path / "tayama-home"
    fake.mkdir()
    monkeypatch.setenv("TAYAMA_HOME", str(fake))

    from tayama import config

    old = {
        "DIR": config.DIR,
        "CFG": config.CFG,
        "SKILLS": config.SKILLS,
    }
    monkeypatch.setattr(config, "DIR", fake)
    monkeypatch.setattr(config, "CFG", fake / "config.json")
    monkeypatch.setattr(config, "SKILLS", fake / "skills")

    from tayama import layout

    # remapeia qualquer Path do modulo layout que pointed para o DIR antigo
    for name in dir(layout):
        if name.startswith("__"):
            continue
        val = getattr(layout, name)
        if isinstance(val, Path):
            try:
                rel = val.relative_to(old["DIR"])
            except ValueError:
                continue
            monkeypatch.setattr(layout, name, fake / rel)

    yield fake


@pytest.fixture
def layout(home):
    from tayama import layout as mod
    return mod


def panel_file(home: Path, ws_id: str = WS) -> Path:
    return home / "panels" / f"{ws_id}.json"


def link_file(home: Path, ws_id: str = WS) -> Path:
    return home / "links" / f"{ws_id}.json"


# --------------------------------------------------------------------------
# duble de painel: imita a superficie de tayama/browser.py BrowserWindow que o
# layout precisa ler (id, name, url, geometria) + win.workspace como a
# FloatingWindow. Sem Qt: pos() e size() devolvem objetos com .x()/.y() e
# .width()/.height(), igual QPoint/QSize.
# --------------------------------------------------------------------------
class _Point:
    """QPoint — .x()/.y()."""
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _Proxy:
    """QGraphicsProxyWidget — layout._spec usa win.proxy.pos()."""
    def __init__(self, x, y):
        self._pos = _Point(x, y)

    def pos(self):
        return self._pos


class _LineEdit:
    """QLineEdit — layout._spec usa win.url.text()."""
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


class FakePanel:
    """Duble de tayama/browser.py BrowserWindow, sem Qt.

    Reproduz a superficie que layout._spec le: id, name, url (QLineEdit),
    proxy (QGraphicsProxyWidget, attribute), width()/height() (QWidget) e
    workspace (dict com "id", como em ui.FloatingWindow).
    """

    def __init__(self, pid="panel-abc123", name="painel-web",
                 url="https://exemplo.test", x=10, y=20, w=760, h=520,
                 ws_id=WS, with_proxy=True):
        self.id = pid
        self.name = name
        self.url = _LineEdit(url)
        self.workspace = {"id": ws_id, "name": "teste"}
        self.proxy = _Proxy(x, y) if with_proxy else None
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h

    def size(self):
        return _Point(self._w, self._h)


# ==========================================================================
# 1. round-trip de painel
# ==========================================================================
def test_roundtrip_painel(layout, home):
    p = FakePanel()
    layout.save_panel(p)

    got = layout.load_panels(WS)
    assert len(got) == 1, f"esperava 1 painel, veio {got!r}"
    e = got[0]
    assert e["id"] == p.id
    assert e["name"] == p.name
    assert e["url"] == "https://exemplo.test"
    assert (e["x"], e["y"]) == (10, 20)
    assert (e["w"], e["h"]) == (760, 520)


def test_painel_arquivo_no_lugar_certo(layout, home):
    layout.save_panel(FakePanel())
    assert panel_file(home).exists(), "esperava panels/<ws_id>.json"
    dados = json.loads(panel_file(home).read_text("utf-8"))
    assert isinstance(dados, list) and len(dados) == 1


def test_save_panel_atualiza_nao_duplica(layout, home):
    layout.save_panel(FakePanel(x=10, y=20))
    layout.save_panel(FakePanel(x=99, y=77))          # mesmo id
    got = layout.load_panels(WS)
    assert len(got) == 1, f"save_panel deve atualizar, nao duplicar: {got!r}"
    assert (got[0]["x"], got[0]["y"]) == (99, 77)


def test_paineis_independentes_por_workspace(layout, home):
    layout.save_panel(FakePanel(pid="p1", ws_id="ws-a"))
    layout.save_panel(FakePanel(pid="p2", ws_id="ws-b"))
    a = [e["id"] for e in layout.load_panels("ws-a")]
    b = [e["id"] for e in layout.load_panels("ws-b")]
    assert a == ["p1"]
    assert b == ["p2"]


def test_load_all_panels_agrega_todos(layout, home):
    """API real: load_all_panels() -> [(ws_id, spec), ...]."""
    layout.save_panel(FakePanel(pid="p1", ws_id="ws-a"))
    layout.save_panel(FakePanel(pid="p2", ws_id="ws-b"))
    got = layout.load_all_panels()
    assert sorted((ws, e["id"]) for ws, e in got) == [("ws-a", "p1"), ("ws-b", "p2")]


def test_load_all_panels_pula_json_corrompido(layout, home):
    layout.save_panel(FakePanel(pid="p1", ws_id="ws-a"))
    (home / "panels" / "ws-quebrado.json").write_text("{nao eh json", "utf-8")
    got = layout.load_all_panels()
    assert sorted(e["id"] for _, e in got) == ["p1"], \
        f"arquivo corrompido nao pode derrubar o resto: {got!r}"


# ==========================================================================
# 2. remocao de painel
# ==========================================================================
def test_remove_panel(layout, home):
    layout.save_panel(FakePanel(pid="p1"))
    layout.save_panel(FakePanel(pid="p2"))
    layout.remove_panel("p1", WS)
    assert [e["id"] for e in layout.load_panels(WS)] == ["p2"]


def test_remove_panel_inexistente_nao_quebra(layout, home):
    layout.save_panel(FakePanel(pid="p1"))
    layout.remove_panel("nao-existe", WS)      # agente repete comando; idem
    assert [e["id"] for e in layout.load_panels(WS)] == ["p1"]


def test_remove_panel_sem_arquivo_nao_quebra(layout, home):
    layout.remove_panel("p1", "ws-que-nunca-existiu")   # idem


# ==========================================================================
# 3. round-trip de links (os 3 tipos)
# ==========================================================================
def test_link_terminal_terminal(layout, home):
    layout.save_links(WS, [("t-1", "t-2")])
    assert layout.load_links(WS) == [("t-1", "t-2")]


def test_link_terminal_painel(layout, home):
    layout.save_links(WS, [("t-1", "panel-abc123")])
    assert layout.load_links(WS) == [("t-1", "panel-abc123")]


def test_link_painel_terminal(layout, home):
    layout.save_links(WS, [("panel-abc123", "t-1")])
    assert layout.load_links(WS) == [("panel-abc123", "t-1")]


def test_link_painel_painel(layout, home):
    layout.save_links(WS, [("panel-aaa", "panel-bbb")])
    assert layout.load_links(WS) == [("panel-aaa", "panel-bbb")]


def test_links_mistos_e_ordem(layout, home):
    pares = [("t-1", "t-2"), ("t-2", "panel-aaa"), ("panel-bbb", "t-1")]
    layout.save_links(WS, pares)
    assert layout.load_links(WS) == pares


def test_links_por_workspace_nao_vazam(layout, home):
    layout.save_links("ws-a", [("t-1", "t-2")])
    layout.save_links("ws-b", [("t-3", "t-4")])
    assert layout.load_links("ws-a") == [("t-1", "t-2")]
    assert layout.load_links("ws-b") == [("t-3", "t-4")]
    assert layout.load_links("ws-c") == []


def test_save_links_substitui_nao_acumula(layout, home):
    layout.save_links(WS, [("t-1", "t-2")])
    layout.save_links(WS, [("t-3", "t-4")])
    assert layout.load_links(WS) == [("t-3", "t-4")]


# ==========================================================================
# 4. lista vazia -> arquivo some
# ==========================================================================
def test_links_vazios_removem_arquivo(layout, home):
    layout.save_links(WS, [("t-1", "t-2")])
    assert link_file(home).exists()
    layout.save_links(WS, [])
    assert not link_file(home).exists(), "arquivo de lista vazia deve sumir"
    assert layout.load_links(WS) == []


def test_paineis_vazios_removem_arquivo(layout, home):
    layout.save_panel(FakePanel(pid="p1"))
    assert panel_file(home).exists()
    layout.remove_panel("p1", WS)
    assert not panel_file(home).exists(), "arquivo de lista vazia deve sumir"
    assert layout.load_panels(WS) == []


def test_save_links_vazio_sem_arquivo_nao_quebra(layout, home):
    layout.save_links(WS, [])       # nunca houve arquivo


# ==========================================================================
# 5. JSON corrompido / ausente nao estoura (mesmo spirit de agents.py)
# ==========================================================================
def test_painel_ausente_retorna_vazio(layout, home):
    assert layout.load_panels(WS) == []
    assert layout.load_panels("ws-que-nunca-existiu") == []


def test_links_ausente_retorna_vazio(layout, home):
    assert layout.load_links(WS) == []


def test_painel_corrompido_retorna_vazio(layout, home):
    d = home / "panels"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{WS}.json").write_text("{isso nao eh json", "utf-8")
    assert layout.load_panels(WS) == [], "JSON corrompido deve degradar para []"


def test_links_corrompido_retorna_vazio(layout, home):
    d = home / "links"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{WS}.json").write_text("\x00\x00 nao utf8", "utf-8", errors="ignore")
    assert layout.load_links(WS) == [], "JSON corrompido deve degradar para []"


def test_painel_json_com_forma_inesperada_nao_quebra(layout, home):
    """Guarda contra 'a lista veio como objeto'/'null'/string'."""
    d = home / "panels"
    d.mkdir(parents=True, exist_ok=True)
    for conteudo in ('{"um": "objeto"}', "null", '"uma string"', "42", "[]"):
        (d / f"{WS}.json").write_text(conteudo, "utf-8")
        layout.load_panels(WS)          # nao pode levantar


@pytest.mark.xfail(reason="ACHADO: _read() devolve o JSON cru; 'null'/'42' "
                          "retornam None/int e load_links levanta "
                          "TypeError ao iterar. Falta isinstance(lst, list).",
           strict=False)
def test_links_json_com_forma_inesperada_nao_quebra(layout, home):
    d = home / "links"
    d.mkdir(parents=True, exist_ok=True)
    for conteudo in ('{"um": "objeto"}', "null", '"uma string"', "42", "[]"):
        (d / f"{WS}.json").write_text(conteudo, "utf-8")
        layout.load_links(WS)          # nao pode levantar


# --- load_all_*: o startup depende deles (restore_agents / restore_panels) ---
@pytest.mark.parametrize("conteudo", ["null", "42", '"uma string"', "true",
                                      '{"um": "objeto"}'])
def test_load_all_panels_json_inesperado_nao_quebra(layout, home, conteudo):
    """load_all_panels() nao pode levantar com JSON de outro tipo.

    REGRAESSAO: restore_panels() chama isto na abertura do main.py — um
    panels/<ws>.json corrompido derrubava o app inteiro.
    """
    d = home / "panels"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{WS}.json").write_text(conteudo, "utf-8")
    assert layout.load_all_panels() == []


@pytest.mark.parametrize("conteudo", ["null", "42", '"uma string"', "true",
                                      '{"um": "objeto"}'])
def test_load_all_links_json_inesperado_nao_quebra(layout, home, conteudo):
    d = home / "links"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{WS}.json").write_text(conteudo, "utf-8")
    assert layout.load_all_links() == []


def test_load_all_panels_item_nao_dict_descartado(layout, home):
    """A lista pode vir certa mas com itens soltos: [1, null, "x"]."""
    import json as _json
    d = home / "panels"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{WS}.json").write_text(_json.dumps([1, None, "x", [1, 2]]), "utf-8")
    assert layout.load_all_panels() == []   # restore_panels faria spec.get() neles


def test_load_all_links_item_nao_par_descartado(layout, home):
    import json as _json
    d = home / "links"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{WS}.json").write_text(_json.dumps([1, None, "ab", ["a"], ["a", "b", "c"]]), "utf-8")
    assert layout.load_all_links() == []


def test_load_all_um_arquivo_ruim_nao_derruba_os_outros(layout, home):
    """Um workspace corrompido nao pode apagar o painel dos outros workspaces."""
    layout.save_panel(FakePanel(pid="p1", ws_id="ws-a"))
    layout.save_links("ws-a", [("t-1", "p1")])
    d = home / "panels"
    d.mkdir(parents=True, exist_ok=True)
    (d / "ws-quebrado.json").write_text("42", "utf-8")
    (d / "ws-quebrado2.json").write_text("{nem json", "utf-8")
    dl = home / "links"
    dl.mkdir(parents=True, exist_ok=True)
    (dl / "ws-quebrado.json").write_text("null", "utf-8")

    assert [s["id"] for _, s in layout.load_all_panels()] == ["p1"]
    assert [p for _, p in layout.load_all_links()] == [("t-1", "p1")]


def test_load_all_agentes_json_inesperado_nao_quebra(home, layout):
    """Mesma classe em agents.load_all(), que roda em restore_agents()."""
    import json as _json
    from tayama import agents
    d = home / "agents"
    d.mkdir(parents=True, exist_ok=True)
    for conteudo in ("null", "42", '"texto"'):
        (d / f"{WS}.json").write_text(conteudo, "utf-8")
        assert agents.load_all() == []
    (d / f"{WS}.json").write_text(_json.dumps([1, None, "x"]), "utf-8")
    assert agents.load_all() == []      # itens nao-dict derrubariam o startup


# ==========================================================================
# 6. workspace removido limpa panels/ e links/
# ==========================================================================
def test_remove_workspace_apaga_arquivos(layout, home):
    """O layout sozinho sabe limpar os dois arquivos do workspace."""
    layout.save_panel(FakePanel(pid="p1", ws_id="ws-a"))
    layout.save_links("ws-a", [("t-1", "panel-p1")])
    layout.remove_workspace("ws-a")
    assert not (home / "panels" / "ws-a.json").exists()
    assert not (home / "links" / "ws-a.json").exists()


def test_remove_workspace_limpa_paineis_e_links(layout, home):
    """Regra do contrato: remover o workspace limpa panels/ e links/ dele."""
    from tayama import workspaces

    layout.save_panel(FakePanel(pid="p1", ws_id="ws-a"))
    layout.save_links("ws-a", [("t-1", "panel-p1")])
    layout.save_panel(FakePanel(pid="p2", ws_id="ws-b"))
    layout.save_links("ws-b", [("t-2", "panel-p2")])

    workspaces.add("A", "/tmp/a")
    wid = workspaces.load()[-1]["id"]
    layout.save_panel(FakePanel(pid="p3", ws_id=wid))
    layout.save_links(wid, [("t-3", "p3")])

    workspaces.remove(wid)

    assert not (home / "panels" / f"{wid}.json").exists(), "painel do ws removido ficou"
    assert not (home / "links" / f"{wid}.json").exists(), "link do ws removido ficou"
    # os outros workspaces sobrevivem
    assert [e["id"] for e in layout.load_panels("ws-a")] == ["p1"]
    assert layout.load_links("ws-b") == [("t-2", "panel-p2")]


# ==========================================================================
# 7. sem workspace: degrada, nao quebra
# ==========================================================================
def test_save_panel_sem_workspace_nao_quebra(layout, home):
    p = FakePanel(ws_id="ws-ok")
    p.workspace = None                       # painel orfao
    layout.save_panel(p)                     # nao pode levantar
    assert layout.load_panels("ws-ok") == []


def test_save_panel_workspace_sem_id_nao_quebra(layout, home):
    p = FakePanel()
    p.workspace = {}                         # workspace sem id
    layout.save_panel(p)
    assert layout.load_panels(WS) == []


def test_save_panel_workspace_desconhecido_nao_quebra(layout, home):
    p = FakePanel(ws_id="ws-nunca-criado")
    layout.save_panel(p)                     # ws nao existe em workspaces.json
    # pode gravar ou ignorar; o contrato e so nao quebrar
    layout.load_panels("ws-nunca-criado")


def test_save_links_ws_vazio_nao_quebra(layout, home):
    layout.save_links("", [("t-1", "t-2")])   # ws_id degenerado
    layout.load_links("")


# ==========================================================================
# 8. content-addressable: o layout nao deve colidir workspaces por nome
#    (o id vem de uuid; "__" ou "/" num id nao pode escapar do diretorio)
# ==========================================================================
@pytest.mark.parametrize("ws_id", ["..", "ws ok", "a.json"])
def test_ws_id_hostil_nao_escapa_do_diretorio(layout, home, ws_id):
    """Um ws_id degenerado nao escreve fora de panels/ e links/.

    Em producao o id vem de uuid4().hex[:8] (workspaces.add), entao isto e
    defesa em profundidade — o contrato e apenas "nao estoura e nao vaza".
    """
    layout.save_panel(FakePanel(pid="p1", ws_id=ws_id))
    layout.save_links(ws_id, [("t-1", "t-2")])
    layout.load_panels(ws_id)
    layout.load_links(ws_id)
    # config.ensure() cria DIR/skills + config.json legitimamente; qualquer
    # outro arquivo solto em TAYAMA_HOME e escape.
    for d in home.iterdir():
        if d.name in ("panels", "links", "skills", "config.json"):
            continue
        assert not d.is_file(), f"arquivo solto em TAYAMA_HOME: {d.name}"


@pytest.mark.xfail(reason="ACHADO: ws_id com '/' nao escapa do diretorio — "
                          "_file() faz config.DIR/kind/f'{ws}.json' sem sanitizar",
           strict=False)
def test_ws_id_com_barra_deveria_degradar(layout, home):
    """'sub/dir' estoura FileNotFoundError; idealmente cai para [] sem gravar."""
    layout.save_links("sub/dir", [("t-1", "t-2")])


@pytest.mark.xfail(reason="ACHADO: ws_id='../../x' escapa de TAYAMA_HOME "
                          "(path traversal); mitigavel com sanitizar o id",
           strict=False)
def test_ws_id_traversal_deveria_ser_bloqueado(layout, home):
    """'../../pwned' escreve fora de TAYAMA_HOME — hoje grava de fato."""
    layout.save_links("../../pwned", [("t-1", "t-2")])
    fora = home.parent / "pwned.json"
    assert not fora.exists(), "ws_id com '..' escreveu fora de TAYAMA_HOME"
    if fora.exists():
        fora.unlink()
