#!/usr/bin/env python3
"""Persistência de painéis e conexões (tayama/layout.py).

Sem Qt, sem GUI: usa um TAYAMA_HOME temporário e objetos falsos com os mesmos
atributos que BrowserWindow/Edge.

Rodar:
    python3 -m pytest tests/test_layout.py -v
"""
from __future__ import annotations

import json
import os
import stat
import tempfile

import pytest


@pytest.fixture
def home(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="tayama-test-") as d:
        monkeypatch.setenv("TAYAMA_HOME", d)
        import tayama.config
        tayama.config.DIR = __import__("pathlib").Path(d)
        tayama.config.CFG = tayama.config.DIR / "config.json"
        tayama.config.SKILLS = tayama.config.DIR / "skills"
        yield d


@pytest.fixture
def layout(home):
    from tayama import layout as mod
    return mod


class FakeProxy:
    def __init__(self, x=0, y=0): self._p = (x, y)
    def pos(self):
        class P:
            def __init__(self, v): self._v = v
            def x(self): return self._v[0]
            def y(self): return self._v[1]
        return P(self._p)


class FakeLineEdit:
    def __init__(self, text=""): self._t = text
    def text(self): return self._t


class FakePanel:
    def __init__(self, pid, ws_id=None, url="https://a.test", x=0, y=0):
        self.id, self.name, self.url = pid, f"nome-{pid}", FakeLineEdit(url)
        self.workspace = {"id": ws_id} if ws_id else None
        self.proxy = FakeProxy(x, y)
        self.w, self.h = 760, 520
    def width(self): return self.w
    def height(self): return self.h


# --- painéis ---------------------------------------------------------------
def test_save_e_load(layout):
    layout.save_panel(FakePanel("p1", "ws1"))
    spec = layout.load_panels("ws1")[0]
    assert spec["id"] == "p1"
    assert spec["url"] == "https://a.test"
    assert spec["w"] == 760 and spec["h"] == 520
    assert set(spec) == {"id", "name", "url", "x", "y", "w", "h"}


def test_save_atualiza_nao_duplica(layout):
    p = FakePanel("p1", "ws1", url="https://a.test")
    layout.save_panel(p)
    p.url = FakeLineEdit("https://b.test"); p.proxy = FakeProxy(50, 60)
    layout.save_panel(p)
    entries = layout.load_panels("ws1")
    assert len(entries) == 1
    assert entries[0]["url"] == "https://b.test"
    assert (entries[0]["x"], entries[0]["y"]) == (50, 60)


def test_remove_panel(layout):
    layout.save_panel(FakePanel("p1", "ws1"))
    layout.save_panel(FakePanel("p2", "ws1"))
    layout.remove_panel("p1", "ws1")
    assert [e["id"] for e in layout.load_panels("ws1")] == ["p2"]


def test_painel_sem_workspace_nao_persiste_e_nao_quebra(layout):
    layout.save_panel(FakePanel("p1", None))
    assert layout.load_all_panels() == []
    layout.remove_panel("p1", None)          # não deve estourar


def test_workspaces_isolados(layout):
    layout.save_panel(FakePanel("p1", "ws1"))
    layout.save_panel(FakePanel("p2", "ws2"))
    assert [e["id"] for e in layout.load_panels("ws1")] == ["p1"]
    assert [e["id"] for e in layout.load_panels("ws2")] == ["p2"]
    assert dict(layout.load_all_panels())["ws2"]["id"] == "p2"


def test_arquivo_vazio_some_do_disco(layout):
    layout.save_panel(FakePanel("p1", "ws1"))
    f = os.path.join(home_path(layout, "panels"), "ws1.json")
    assert os.path.exists(f)
    layout.remove_panel("p1", "ws1")
    assert not os.path.exists(f)


def home_path(layout, kind):
    from tayama import config
    return str(config.DIR / kind)


def test_permissao_600(layout):
    layout.save_panel(FakePanel("p1", "ws1"))
    mode = stat.S_IMODE(os.stat(os.path.join(home_path(layout, "panels"), "ws1.json")).st_mode)
    assert mode == 0o600, oct(mode)


def test_json_corrompido_nao_quebra(layout):
    d = os.path.join(home_path(layout, "panels")); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "ws1.json"), "w") as fh: fh.write("{{{ nao é json")
    assert layout.load_panels("ws1") == []


def test_url_vazio_vira_about_blank(layout):
    layout.save_panel(FakePanel("p1", "ws1", url=""))
    assert layout.load_panels("ws1")[0]["url"] == "about:blank"


# --- conexões --------------------------------------------------------------
def test_links_roundtrip(layout):
    layout.save_links("ws1", [("a", "b"), ("c", "d")])
    assert layout.load_links("ws1") == [("a", "b"), ("c", "d")]
    raw = json.load(open(os.path.join(home_path(layout, "links"), "ws1.json")))
    assert raw == [["a", "b"], ["c", "d"]]      # lista de pares, como no contrato


def test_links_vazias_removem_arquivo(layout):
    layout.save_links("ws1", [("a", "b")])
    layout.save_links("ws1", [])
    assert not os.path.exists(os.path.join(home_path(layout, "links"), "ws1.json"))
    assert layout.load_links("ws1") == []


def test_links_entre_paineis_e_terminais(layout):
    layout.save_links("ws1", [("term1", "panel-1"), ("panel-1", "panel-2")])
    assert ("panel-1", "panel-2") in layout.load_links("ws1")


def test_links_corrompidos_descartados(layout):
    d = os.path.join(home_path(layout, "links")); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "ws1.json"), "w") as fh: fh.write("nao é json")
    assert layout.load_links("ws1") == []


def test_par_invalido_descartado(layout):
    d = os.path.join(home_path(layout, "links")); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "ws1.json"), "w") as fh:
        json.dump([["a"], ["b", "c", "d"], "lixo", ["e", "f"]], fh)
    assert layout.load_links("ws1") == [("e", "f")]


# --- remoção de workspace --------------------------------------------------
def test_remove_workspace_apaga_paineis_e_links(layout):
    layout.save_panel(FakePanel("p1", "ws1")); layout.save_links("ws1", [("p1", "p2")])
    layout.save_panel(FakePanel("p9", "ws9"))
    layout.remove_workspace("ws1")
    assert layout.load_panels("ws1") == [] and layout.load_links("ws1") == []
    assert [e["id"] for e in layout.load_panels("ws9")] == ["p9"]   # outro workspace intacto


def test_remove_workspace_inexistente_nao_quebra(layout):
    layout.remove_workspace("nao-existe")


def test_workspaces_remove_chama_layout(home, monkeypatch):
    from tayama import layout as lay, workspaces
    lay.save_panel(FakePanel("p1", "wstest"))
    workspaces.save([{"id": "wstest", "name": "w", "path": "/tmp"}])
    workspaces.remove("wstest")
    assert lay.load_panels("wstest") == []
    assert workspaces.load() == []


# --- addendum do tester: JSON válido do tipo errado -------------------------
@pytest.mark.parametrize("conteudo", ["null", "42", '"texto"', "true"])
@pytest.mark.parametrize("kind,leitor", [("panels", "load_panels"), ("links", "load_links")])
def test_json_de_outro_tipo_vira_lista_vazia(layout, conteudo, kind, leitor):
    d = os.path.join(home_path(layout, kind)); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "ws1.json"), "w") as fh: fh.write(conteudo)
    assert getattr(layout, leitor)("ws1") == []


def test_json_com_lista_de_nao_listas_nao_quebra(layout):
    """Um item do tipo errado dentro da lista é ignorado, não derruba a carga."""
    d = os.path.join(home_path(layout, "links")); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "ws1.json"), "w") as fh:
        json.dump([None, 5, "ab", ["a", "b"]], fh)
    assert layout.load_links("ws1") == [("a", "b")]


def test_agents_json_de_outro_tipo_vira_lista_vazia(layout):
    """Mesma classe de bug corrigida em agents.py."""
    from tayama import agents
    with open(os.path.join(home_path(agents, "agents"), "ws1.json"), "w") as fh:
        fh.write("null")
    assert agents._read("ws1") == []
    with open(os.path.join(home_path(agents, "agents"), "ws1.json"), "w") as fh:
        fh.write("42")
    assert agents._read("ws1") == []
    assert [a for _, a in agents.load_all() if isinstance(a, dict)] == []


def home_path(mod, kind):
    """_dir() é privado em agents/layout; usamos o mesmo caminho que eles usam."""
    from tayama import config
    config.ensure()
    d = config.DIR / kind; d.mkdir(exist_ok=True); return str(d)


# --- addendum do tester: ws_id nao sanitizado -------------------------------
# Contrato: id degenerado DEGRADA (nao persiste, load devolve []) em vez de
# levantar erro — o painel ainda precisa funcionar na sessao.
@pytest.mark.parametrize("ws_id", [
    "../../pwned", "../pwned", "sub/dir", "/abs", "", "a b", "x;rm -rf", "id\n",
    ".", "..", "ws ok", "a.json",
])
def test_ws_id_hostil_degrada_sem_estourar(layout, ws_id):
    layout.save_panel(FakePanel("p1", ws_id))
    layout.save_links(ws_id, [("a", "b")])
    assert layout.load_panels(ws_id) == []
    assert layout.load_links(ws_id) == []
    layout.remove_panel("p1", ws_id)
    layout.remove_workspace(ws_id)


def test_ws_id_hostil_nao_escapa_do_home(layout, home):
    """Nada pode ser gravado fora de TAYAMA_HOME, nem arquivo solto dentro dele."""
    for ws_id in ("../../pwned", "../pwned", "sub/dir", "/tmp/abs", ".."):
        layout.save_panel(FakePanel("p1", ws_id))
        layout.save_links(ws_id, [("a", "b")])
    assert not os.path.exists("/tmp/pwned.json")
    assert not os.path.exists(os.path.join(str(home), "..", "pwned.json"))
    for d in os.listdir(home):
        assert d in ("panels", "links", "skills", "config.json", "agents"), d


def test_painel_sem_workspace_nao_persiste(layout):
    """Contrato item 3: sem workspace o painel não persiste e não estoura."""
    layout.save_panel(FakePanel("p1", None))
    assert layout.load_all_panels() == []


def test_ws_id_valido_continua_funcionando(layout):
    """A validação não pode quebrar ids reais (uuid hex de 8)."""
    for ws in ("a1b2c3d4", "WS-1", "ws_2", "A"):
        layout.save_panel(FakePanel(f"p-{ws}", ws))
        layout.save_links(ws, [(f"p-{ws}", "t-1")])
        assert [e["id"] for e in layout.load_panels(ws)] == [f"p-{ws}"]
        assert layout.load_links(ws) == [(f"p-{ws}", "t-1")]


def test_ws_id_invalido_nao_quebra_remove_workspace(layout):
    layout.remove_workspace("../../pwned")     # não deve escrever fora