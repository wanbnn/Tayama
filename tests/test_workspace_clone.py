#!/usr/bin/env python3
"""Duplicar e editar workspace (tayama/workspaces.py).

Sem Qt, sem GUI: usa um TAYAMA_HOME temporário e objetos falsos com os mesmos
atributos que FloatingWindow/Panel, só para semear o estado inicial.

Contrato exercitado:
    workspaces.update(wid, name, path) -> None   mantém o id, reescreve name/path
    workspaces.duplicate(ws)          -> dict    id novo, copia agents/panels/links
                                                   com ids novos e links remapeados

Rodar:
    python3 -m pytest tests/test_workspace_clone.py -v
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile

import pytest


@pytest.fixture
def home(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="tayama-test-") as d:
        monkeypatch.setenv("TAYAMA_HOME", d)
        import tayama.config
        tayama.config.DIR = pathlib.Path(d)
        tayama.config.CFG = tayama.config.DIR / "config.json"
        tayama.config.SKILLS = tayama.config.DIR / "skills"
        yield d


@pytest.fixture
def ws(home):
    from tayama import workspaces
    return workspaces


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


class FakeSize:
    def __init__(self, w, h): self._w, self._h = w, h
    def width(self): return self._w
    def height(self): return self._h


class FakePanel:
    """Tem os atributos que layout._spec() lê."""

    def __init__(self, pid, ws_id, url="https://a.test", x=0, y=0, w=760, h=520):
        self.id, self.name, self.url = pid, f"painel-{pid}", FakeLineEdit(url)
        self.workspace = {"id": ws_id}
        self.proxy = FakeProxy(x, y)
        self.w, self.h = w, h
    def width(self): return self.w
    def height(self): return self.h


class FakeAgent:
    """Tem os atributos que agents.save_one() lê (terminal ou janela de agente)."""

    def __init__(self, aid, ws_id, agent="Shell", role=None, skills=(), x=0, y=0):
        self.id, self.name = aid, f"janela-{aid}"
        self.agent = {"name": agent}
        self.role = {"name": role} if role else None
        self.skills = [{"name": s} for s in skills]
        self.workspace = {"id": ws_id}
        self.proxy = FakeProxy(x, y)
        self.w, self.h = 640, 480
    def size(self): return FakeSize(self.w, self.h)


def semear(ws_id):
    """Workspace de referência: 2 agentes, 2 painéis e uma ligação em cada par.

    Os ids são propositalmente longos e distintos para que uma troca parcial
    (metade remapeada, metade não) apareça nos asserts.
    """
    from tayama import agents, layout, workspaces
    workspaces.save([{"id": ws_id, "name": "Original", "path": "/tmp/orig"}])
    agents.save_one(FakeAgent("term-alfa", ws_id, agent="Shell", skills=["git"], x=10, y=20))
    agents.save_one(FakeAgent("agente-beta", ws_id, agent="Claude Code", role="Tester"))
    layout.save_panel(FakePanel("painel-alfa", ws_id, url="https://alfa.test", x=5, y=6, w=700, h=400))
    layout.save_panel(FakePanel("painel-beta", ws_id, url="https://beta.test", x=50, y=60, w=800, h=450))
    # ligação ordenada agente->painel e painel->painel
    layout.save_links(ws_id, [("term-alfa", "painel-alfa"), ("painel-alfa", "painel-beta")])


def ler_agentes(ws_id):
    from tayama import agents
    return agents._read(ws_id)


def ler_links(ws_id):
    from tayama import layout
    return [tuple(p) for p in layout.load_links(ws_id)]


# --- 1. update -------------------------------------------------------------
def test_update_muda_nome_e_path_mantendo_id(ws):
    ws.save([{"id": "w1", "name": "Antigo", "path": "/tmp/velho"}])
    ws.update("w1", "Novo", "/tmp/novo")
    itens = ws.load()
    assert len(itens) == 1, "update nao pode criar entrada nova"
    assert itens[0]["id"] == "w1"
    assert itens[0]["name"] == "Novo"
    assert itens[0]["path"] == "/tmp/novo"


def test_update_nome_com_espacos_e_acento(ws):
    ws.save([{"id": "w1", "name": "Antigo", "path": "/tmp/a"}])
    ws.update("w1", "Projeto 安 동 ✅", "/caminho/novo")
    assert ws.load()[0]["name"] == "Projeto 安 동 ✅"


def test_update_nao_toca_outros_workspaces(ws):
    ws.save([
        {"id": "w1", "name": "Um", "path": "/1"},
        {"id": "w2", "name": "Dois", "path": "/2"},
    ])
    ws.update("w1", "Um editado", "/1b")
    por_id = {w["id"]: w for w in ws.load()}
    assert por_id["w2"] == {"id": "w2", "name": "Dois", "path": "/2"}


def test_update_inexistente_nao_cria_fantasma(ws):
    ws.save([{"id": "w1", "name": "Um", "path": "/1"}])
    ws.update("nao-existe", "Fantasma", "/tmp/fantasma")
    assert [w["id"] for w in ws.load()] == ["w1"]


def test_update_arquivo_inexistente_nao_quebra(ws):
    ws.update("w1", "Qualquer", "/tmp/x")     # sem workspaces.json no disco
    assert ws.load() == []


# --- 2. duplicate: identidade e unicidade -----------------------------------
def test_duplicate_devolve_id_diferente_e_dict(ws):
    original = ws.add("Original", "/tmp/orig")
    copia = ws.duplicate(original)
    assert isinstance(copia, dict)
    assert copia["id"] != original["id"]
    assert copia["name"] != original["name"], "a copia precisa de nome proprio"
    assert len(copia["id"]) >= 1
    assert copia["path"]


def test_duplicate_cria_entrada_no_lista_e_mantem_original(ws):
    original = ws.add("Original", "/tmp/orig")
    copia = ws.duplicate(original)
    ids = [w["id"] for w in ws.load()]
    assert len(ids) == 2
    assert original["id"] in ids and copia["id"] in ids


def test_duplicate_duas_vezes_nao_colide(ws):
    original = ws.add("Original", "/tmp/orig")
    c1 = ws.duplicate(original)
    c2 = ws.duplicate(original)
    assert c1["id"] != c2["id"] != original["id"]
    nomes = [w["name"] for w in ws.load()]
    assert len(set(nomes)) == 3, f"nomes colidem: {nomes}"


def test_duplicate_preserva_path_do_original(ws):
    original = ws.add("Original", "/tmp/orig")
    assert ws.duplicate(original)["path"] == "/tmp/orig"


# --- 3. agentes clonados ---------------------------------------------------
def test_agentes_clonados_com_ids_novos(ws):
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    orig, clone = ler_agentes("wsorig"), ler_agentes(copia["id"])
    assert len(clone) == len(orig) == 2
    ids_orig = {a["id"] for a in orig}
    ids_clone = {a["id"] for a in clone}
    assert not (ids_orig & ids_clone), f"id original sobreviveu: {ids_orig & ids_clone}"


def test_agentes_clonados_apontam_para_o_novo_workspace(ws):
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    for a in ler_agentes(copia["id"]):
        assert a["workspace_id"] == copia["id"]


def test_agentes_clonados_preservam_metadados(ws):
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    orig = sorted(ler_agentes("wsorig"), key=lambda a: a["name"])
    clone = sorted(ler_agentes(copia["id"]), key=lambda a: a["name"])
    for o, c in zip(orig, clone):
        for campo in ("name", "agent_name", "role_name", "skills", "x", "y", "w", "h"):
            assert c[campo] == o[campo], f"{campo}: {o[campo]!r} != {c[campo]!r}"
        assert c["id"] != o["id"]


def test_agentes_clonados_viram_arquivo_novo(ws):
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    d = pathlib.Path(home_of("agents"))
    assert (d / "wsorig.json").exists()
    assert (d / f"{copia['id']}.json").exists()


def test_workspace_sem_agentes_duplica_sem_quebrar(ws):
    ws.save([{"id": "w1", "name": "Vazio", "path": "/tmp/vazio"}])
    copia = ws.duplicate({"id": "w1", "name": "Vazio", "path": "/tmp/vazio"})
    assert copia["id"] != "w1"
    assert ler_agentes(copia["id"]) == []


# --- 4. painéis clonados ---------------------------------------------------
def test_paineis_clonados_com_ids_novos_e_geometria_intacta(ws):
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    from tayama import layout
    orig = sorted(layout.load_panels("wsorig"), key=lambda p: p["name"])
    clone = sorted(layout.load_panels(copia["id"]), key=lambda p: p["name"])
    assert len(clone) == len(orig) == 2
    for o, c in zip(orig, clone):
        assert c["id"] != o["id"]
        for campo in ("name", "url", "x", "y", "w", "h"):
            assert c[campo] == o[campo], f"{campo}: {o[campo]!r} != {c[campo]!r}"


def test_paineis_clonados_urls_especificas(ws):
    from tayama import layout
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    urls = {p["url"] for p in layout.load_panels(copia["id"])}
    assert urls == {"https://alfa.test", "https://beta.test"}


# --- 5. links remapeados ---------------------------------------------------
def test_links_clonados_apontam_para_ids_novos(ws):
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    ids_agentes = {a["id"] for a in ler_agentes(copia["id"])}
    from tayama import layout
    ids_paineis = {p["id"] for p in layout.load_panels(copia["id"])}
    conhecidos = ids_agentes | ids_paineis
    links = ler_links(copia["id"])
    assert len(links) == 2, f"links perdidos: {links}"
    for src, dst in links:
        assert src in conhecidos, f"origem {src} nao foi remapeada"
        assert dst in conhecidos, f"destino {dst} nao foi remapeado"


def test_links_clonados_nao_sobram_ids_do_original(ws):
    """Contrato item 5: nenhum id do original sobrevive no arquivo clonado."""
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    from tayama import layout
    ids_do_original = {a["id"] for a in ler_agentes("wsorig")}
    ids_do_original |= {p["id"] for p in layout.load_panels("wsorig")}
    ids_do_original |= {x for link in ler_links("wsorig") for x in link}
    sobreviventes = ids_do_original & {x for link in ler_links(copia["id"]) for x in link}
    assert not sobreviventes, f"ids do original sobreviveram no clone: {sobreviventes}"


def test_links_clonados_preservam_o_par_ordenado(ws):
    """O par (term-alfa -> painel-alfa) continua sendo (agente -> painel)."""
    from tayama import layout
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})

    def indexar(ws_id):
        ag = {a["id"]: a for a in ler_agentes(ws_id)}
        pa = {p["id"]: p for p in layout.load_panels(ws_id)}
        return {**{k: "agente" for k in ag}, **{k: "painel" for k in pa}}

    antes, depois = indexar("wsorig"), indexar(copia["id"])
    ordem_antes = [(antes[a], antes[b]) for a, b in ler_links("wsorig")]
    ordem_depois = [(depois[a], depois[b]) for a, b in ler_links(copia["id"])]
    assert sorted(ordem_depois) == sorted(ordem_antes)
    assert ("agente", "painel") in ordem_depois
    assert ("painel", "painel") in ordem_depois


def test_links_do_original_continuam_valendo(ws):
    """O clone não pode 'gastar' os ids do original — ele tem os seus."""
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    assert ler_links("wsorig") == [("term-alfa", "painel-alfa"), ("painel-alfa", "painel-beta")]
    assert ler_links(copia["id"]) != ler_links("wsorig")


def test_workspace_sem_links_duplica_sem_quebrar(ws):
    ws.save([{"id": "w1", "name": "Vazio", "path": "/tmp/vazio"}])
    copia = ws.duplicate({"id": "w1", "name": "Vazio", "path": "/tmp/vazio"})
    assert ler_links(copia["id"]) == []


# --- 6. original intacto ----------------------------------------------------
def test_original_intacto_apos_duplicate(ws):
    """O original não é movido nem alterado — a cópia é uma ENTRADA A MAIS."""
    semear("wsorig")
    antes = {
        "ws": [w for w in ws.load() if w["id"] == "wsorig"],
        "agentes": ler_agentes("wsorig"),
        "links": ler_links("wsorig"),
    }
    from tayama import layout
    antes["paineis"] = layout.load_panels("wsorig")

    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})

    # a entrada do original continua byte-a-byte igual (id, nome e path)
    assert [w for w in ws.load() if w["id"] == "wsorig"] == antes["ws"]
    assert ler_agentes("wsorig") == antes["agentes"], "os agentes do original mudaram"
    assert layout.load_panels("wsorig") == antes["paineis"], "os paineis do original mudaram"
    assert ler_links("wsorig") == antes["links"], "os links do original mudaram"
    # e a cópia é uma entrada nova, não uma troca
    assert len(ws.load()) == len(antes["ws"]) + 1
    assert copia["id"] != "wsorig"


def test_duplicate_nao_apaga_outros_workspaces(ws):
    semear("wsorig")
    ws.add("Outro", "/tmp/outro")
    ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    ids = {w["id"] for w in ws.load()}
    assert "wsorig" in ids and len(ids) == 3


def test_remover_o_clone_nao_leva_o_original(ws):
    """O clone é um workspace independente: apagar ele não toca no original."""
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    ws.remove(copia["id"])
    assert ws.load() == [{"id": "wsorig", "name": "Original", "path": "/tmp/orig"}]
    assert len(ler_agentes("wsorig")) == 2
    assert ler_links("wsorig")


# --- 7. degradação ----------------------------------------------------------
def test_duplicate_de_workspace_inexistente_nao_quebra(ws):
    ws.save([{"id": "w1", "name": "Um", "path": "/1"}])
    try:
        copia = ws.duplicate({"id": "nao-existe", "name": "Fantasma", "path": "/tmp/f"})
    except Exception as e:                       # noqa: BLE001 - teste é sobre degradação
        pytest.fail(f"duplicate de workspace inexistente levantou {type(e).__name__}: {e}")
    if copia is not None:
        assert copia.get("id") != "nao-existe"


def test_duplicate_com_dict_vazio_nao_quebra(ws):
    try:
        ws.duplicate({})
    except Exception as e:                       # noqa: BLE001
        pytest.fail(f"duplicate com dict vazio levantou {type(e).__name__}: {e}")


def test_duplicate_com_workspace_nao_registrado_nao_cria_arquivo_lixo(ws):
    ws.save([{"id": "w1", "name": "Um", "path": "/1"}])
    ws.duplicate({"id": "fantasma", "name": "F", "path": "/f"})
    for d in ("agents", "panels", "links"):
        p = pathlib.Path(home_of(d)) / "fantasma.json"
        assert not p.exists(), f"criou {p} para workspace inexistente"


def test_copia_com_agentes_corrompidos_nao_quebra(ws):
    """JSON quebrado no original degrada para lista vazia, não derruba a cópia."""
    from tayama import layout
    semear("wsorig")
    d = pathlib.Path(home_of("agents"))
    (d / "wsorig.json").write_text("{{{ nao é json", "utf-8")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    assert copia["id"] != "wsorig"
    assert ler_agentes(copia["id"]) == []
    assert len(layout.load_panels(copia["id"])) == 2


def home_of(kind):
    from tayama import config
    config.ensure()
    d = config.DIR / kind; d.mkdir(exist_ok=True); return str(d)


# --- 8. arquivo cru: o clone é mesmo um arquivo separado -------------------
def test_arquivo_clonado_tem_ids_novos_no_disco(ws):
    """Lê o arquivo do clone direto, sem passar pelos loaders."""
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    caminho = pathlib.Path(home_of("panels")) / f"{copia['id']}.json"
    assert caminho.exists(), "o clone nao gravou panels/<id>.json"
    dados = json.loads(caminho.read_text("utf-8"))
    assert {d["id"] for d in dados}.isdisjoint({"painel-alfa", "painel-beta"})


def test_permissao_600_no_arquivo_clonado(ws):
    """Só o que o layout.py grava — ele usa config._write_private (0600).

    O agents/<id>.json fica em 0644 porque agents._write() usa write_text()
    direto; isso é pré-existente ao duplicate e está fora deste contrato
    (ver teste análogo em test_layout.py, que também só cobre panels).
    """
    import stat
    semear("wsorig")
    copia = ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    for kind in ("panels", "links"):
        p = pathlib.Path(home_of(kind)) / f"{copia['id']}.json"
        if p.exists():
            modo = stat.S_IMODE(os.stat(p).st_mode)
            assert modo == 0o600, f"{kind}: {oct(modo)}"


def test_duplicate_respeita_home_temporario(ws, home):
    """Nada pode ser gravado fora do TAYAMA_HOME isolado."""
    semear("wsorig")
    ws.duplicate({"id": "wsorig", "name": "Original", "path": "/tmp/orig"})
    for d in os.listdir(home):
        assert d in ("agents", "panels", "links", "skills", "config.json", "workspaces.json"), d