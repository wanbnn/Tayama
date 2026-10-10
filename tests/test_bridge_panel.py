"""Bridge: roteamento de painel e resposta em voo. Sem Qt, sem display.

`tayama/bridge.py` nunca teve teste — esta é a dobradiça nova (peers/send/
broadcast viraram reply assíncrono, e cmd="panel" não responde na hora).

    .venv/bin/python -m pytest tests/test_bridge_panel.py -v
"""
import os, sys, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tayama import panelapi
from tayama.bridge import Bridge, _Conn


# ==========================================================================
# Dublês: duck-typing, sem QApplication
# ==========================================================================
class FakeProxy:
    def __init__(self, ws=None):
        self.ws = ws


class FakeUrl:
    def __init__(self, v=""):
        self.v = v

    def text(self):
        return self.v

    def setText(self, v):
        self.v = v


class FakeTitle:
    def __init__(self, v=""):
        self.v = v

    def text(self):
        return self.v


class FakePanel:
    """Duck-tipo do BrowserWindow: so o que o Bridge toca."""
    def __init__(self, pid, name=None, ws=None, url="http://exemplo/", state="ok"):
        self.id = pid
        self.name = name or pid
        self.workspace = ws
        # mesmos nomes de browser.py:37-39 — o Bridge lista peers por role_name/agent
        self.role_name = "painel web"
        self.agent = {"name": "Chromium"}
        self.url, self.title, self.state = FakeUrl(url), FakeTitle("T"), state
        self.vai_sumir = False      # quando True, simula painel fechado no meio
        self.recebeu = []           # js que chegou

    def run_js(self, js, on_value):
        self.recebeu.append(js)
        # Um painel que some antes de responder: o Bridge tem de recusar.
        if self.vai_sumir:
            return
        on_value({"found": True, "value": "resposta-fake"})

    def go_back(self):
        self.url.setText("http://exemplo/antes")

    def go_forward(self):
        self.url.setText("http://exemplo/depois")

    def go_reload(self):
        self.url.setText(self.url.text() + "#reload")

    def navigate_api(self, url):
        ok, err = panelapi.check_url(url)
        if not ok:
            return {"ok": False, "error": err}
        self.url.setText(ok)
        return {"ok": True, "url": ok}

    def receive(self, src, text):
        self.recebeu.append(("msg", text))


class FakeTerm:
    def __init__(self, tid, name, ws=None):
        self.id, self.name, self.workspace = tid, name, ws
        self.role_name, self.agent = "Tester", {"name": "Claude Code"}
        self.recebeu = []

    def receive(self, src, text):
        self.recebeu.append(text)


class FakeEdge:
    def __init__(self, src, dst):
        self.src, self.dst = src, dst


class FakeCanvas:
    def __init__(self, ws=None):
        self.windows, self.panels, self.edges = {}, {}, []
        self.ws = ws
        self.adicionados = []

    def peers_of(self, win):
        return [e.dst for e in self.edges if e.src is win]

    def add_panel(self, url="about:blank", name=None):
        p = FakePanel("panel-novo-%d" % len(self.adicionados), name, self.ws, url)
        self.panels[p.id] = p
        self.adicionados.append(p)
        return p

    def remove_panel(self, win):
        self.panels.pop(win.id, None)


class Coletor:
    """Substitui `reply`: guarda as respostas em vez de escrever no socket."""
    def __init__(self):
        self.respostas = []

    def __call__(self, resp):
        self.respostas.append(resp)

    @property
    def ultima(self):
        return self.respostas[-1] if self.respostas else None


@pytest.fixture
def cena():
    c = FakeCanvas({"id": "ws1"})
    t = FakeTerm("t1", "tester", {"id": "ws1"})
    c.windows["t1"] = t
    return c, t


def bridge_de(cena):
    """Bridge sem QLocalServer: só o handle, com o canvas da cena."""
    b = Bridge.__new__(Bridge)
    b.canvas, b._live = cena, set()
    return b


# ==========================================================================
# 1. Regressão: peers/send/broadcast continuam respondendo na hora
# ==========================================================================
def test_peers_responde_sincrono(cena):
    """`peers` volta pelo return, não pelo reply — quem responde é `_new`."""
    c, t = cena
    out = Coletor()
    r = bridge_de(c).handle({"from": "t1", "cmd": "peers"}, reply=out)
    assert r is not None and r["ok"] and r["peers"] == []
    assert out.respostas == [], "peers nao devia passar pelo reply assíncrono"


def test_peers_expoe_id_e_kind_do_painel(cena):
    """O agente precisa do id do painel para passar --panel depois."""
    c, t = cena
    p = FakePanel("panel-aaaa", "docs", {"id": "ws1"})
    c.panels[p.id] = p
    c.edges.append(FakeEdge(t, p))
    out = Coletor()
    r = bridge_de(c).handle({"from": "t1", "cmd": "peers"}, reply=out)
    info = r["peers"][0]
    assert info["id"] == "panel-aaaa"
    assert info["kind"] == "painel"
    assert info["name"] == "docs"


def test_send_para_terminal_continua_iguais(cena):
    c, t = cena
    outro = FakeTerm("t2", "dev")
    c.windows["t2"] = outro
    c.edges.append(FakeEdge(t, outro))
    r = bridge_de(c).handle({"from": "t1", "cmd": "send", "target": "dev", "text": "oi"}, reply=Coletor())
    assert r["ok"] and r["sent_to"] == ["dev"]
    assert outro.recebeu == ["oi"]


def test_broadcast_continua_iguais(cena):
    c, t = cena
    outro = FakeTerm("t2", "dev")
    c.windows["t2"] = outro
    c.edges.append(FakeEdge(t, outro))
    r = bridge_de(c).handle({"from": "t1", "cmd": "broadcast", "text": "oi"}, reply=Coletor())
    assert r["ok"] and r["sent_to"] == ["dev"]   # sent_to usa nome, como sempre


def test_cmd_desconhecido_continua_dando_erro_util(cena):
    c, t = cena
    r = bridge_de(c).handle({"from": "t1", "cmd": "inexistente"}, reply=Coletor())
    assert r["ok"] is False and "inexistente" in r["error"]


def test_origem_desconhecida_recusada_antes_do_dispatch(cena):
    """Painéis vivem em canvas.panels e não têm TAYAMA_ID: continuam inválidos
    como origem, e a checagem vem ANTES do cmd (senão um painel forja `from`)."""
    c, t = cena
    c.panels["p1"] = FakePanel("p1")
    r = bridge_de(c).handle({"from": "p1", "cmd": "panel", "op": "list"}, reply=Coletor())
    assert r["ok"] is False
    assert "de origem desconhecido" in r["error"]
    assert "rode dentro do Tayama" in r["error"], "a mensagem de hoje não pode mudar"


# ==========================================================================
# 2. Painel: resposta em voo (devolve None)
# ==========================================================================
def test_op_de_painel_devolve_none(cena):
    c, t = cena
    p = FakePanel("panel-aaaa", ws={"id": "ws1"})
    c.panels[p.id] = p
    c.edges.append(FakeEdge(t, p))
    out = Coletor()
    assert bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "click", "selector": "#b"}, reply=out) is None
    assert out.ultima["ok"] is True


def test_list_responde_na_hora(cena):
    c, t = cena
    p = FakePanel("panel-aaaa", "docs", {"id": "ws1"}, url="http://exemplo/x")
    c.panels[p.id] = p
    c.edges.append(FakeEdge(t, p))
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": "list"}, reply=out)
    linha = out.ultima["panels"][0]
    assert linha["id"] == "panel-aaaa"
    assert linha["linked"] is True
    assert linha["url"] == "http://exemplo/x"


def test_list_marca_painel_de_outro_workspace_como_inacessivel(cena):
    c, t = cena
    c.panels["p1"] = FakePanel("p1", ws={"id": "OUTRO"})
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": "list"}, reply=out)
    assert out.ultima["panels"][0]["accessible"] is False


def test_painel_sem_seta_nao_e_alcancavel(cena):
    c, t = cena
    c.panels["p1"] = FakePanel("p1", ws={"id": "ws1"})
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": "page"}, reply=out)
    assert out.ultima["ok"] is False
    assert "nenhum painel conectado" in out.ultima["error"]


def test_dois_paineis_sem_panel_exige_desambiguacao(cena):
    """Adivinhar entre dois painéis é pior que falhar."""
    c, t = cena
    for pid in ("p1", "p2"):
        c.panels[pid] = FakePanel(pid, ws={"id": "ws1"})
        c.edges.append(FakeEdge(t, c.panels[pid]))
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": "page"}, reply=out)
    assert out.ultima["ok"] is False
    assert "p1" in out.ultima["error"] and "p2" in out.ultima["error"]


def test_panel_por_id_escolhe_o_certo(cena):
    c, t = cena
    for pid in ("p1", "p2"):
        c.panels[pid] = FakePanel(pid, ws={"id": "ws1"})
        c.edges.append(FakeEdge(t, c.panels[pid]))
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": "page", "panel": "p2"}, reply=out)
    assert out.respostas[0]["ok"] is True
    assert len(c.panels["p2"].recebeu) == 1 and c.panels["p1"].recebeu == []


def test_panel_inexistente_da_erro(cena):
    c, t = cena
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": "page", "panel": "nao-existe"}, reply=out)
    assert out.ultima["ok"] is False and "inexistente" in out.ultima["error"]


def test_painel_de_outro_workspace_recusado(cena):
    c, t = cena
    c.panels["p1"] = FakePanel("p1", ws={"id": "OUTRO"})
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": "page", "panel": "p1"}, reply=out)
    assert out.ultima["ok"] is False
    assert "outro workspace" in out.ultima["error"]


# ==========================================================================
# 3. Navegação — a URL que o agente pode mudar (a preocupação do usuário)
# ==========================================================================
def test_navigate_muda_a_url_do_painel(cena):
    c, t = cena
    p = FakePanel("p1", ws={"id": "ws1"})
    c.panels[p.id] = p
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "navigate", "url": "https://exemplo.com/novo",
         "panel": "p1"}, reply=out)
    assert out.ultima["ok"] is True
    assert p.url.text() == "https://exemplo.com/novo"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file:///home/qualquer/.ssh/id_rsa",
    "FILE:///etc/passwd",
    "data:text/html,<script>alert(1)</script>",
    "javascript:alert(1)",
    "java\tscript:alert(1)",
    "qrc:/etc/passwd",
])
def test_navigate_recusa_url_perigosa(cena, url):
    """A URL que o agente escreve é a fronteira de segurança: `navigate file://`
    seguido de `page` leria o disco do usuário."""
    c, t = cena
    p = FakePanel("p1", ws={"id": "ws1"}, url="http://antes/")
    c.panels[p.id] = p
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "navigate", "url": url, "panel": "p1"}, reply=out)
    assert out.ultima["ok"] is False, url
    assert p.url.text() == "http://antes/", f"{url} nao pode ter navegado"


def test_navigate_aceita_http_com_caixa_e_espaco(cena):
    c, t = cena
    p = FakePanel("p1", ws={"id": "ws1"})
    c.panels[p.id] = p
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "navigate", "url": "  HTTPS://exemplo.com/  ",
         "panel": "p1"}, reply=out)
    assert out.ultima["ok"] is True
    assert p.url.text() == "HTTPS://exemplo.com/"


def test_open_recusa_url_perigosa(cena):
    c, t = cena
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "open", "url": "file:///etc/passwd"}, reply=out)
    assert out.ultima["ok"] is False
    assert c.adicionados == []


def test_open_cria_painel(cena):
    c, t = cena
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "open", "url": "https://exemplo.com",
         "name": "docs"}, reply=out)
    assert out.ultima["ok"] is True and out.ultima["id"]
    assert c.adicionados[0].name == "docs"


@pytest.mark.parametrize("op,esperado", [
    ("back", "http://exemplo/antes"),
    ("forward", "http://exemplo/depois"),
    ("reload", "http://a/#reload"),
])
def test_back_forward_reload(cena, op, esperado):
    """Cada op parte de um painel novo: back/forward mudam a url e `reload` relê
    a url atual, então um painel compartilhado daria falso negativo."""
    c, t = cena
    p = FakePanel("p1", ws={"id": "ws1"}, url="http://a/")
    c.panels[p.id] = p
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": op, "panel": "p1"}, reply=out)
    assert out.ultima["ok"] is True, op
    assert out.ultima["url"] == esperado, op
    assert p.url.text() == esperado, op


def test_close_remove_o_painel(cena):
    c, t = cena
    p = FakePanel("p1", ws={"id": "ws1"})
    c.panels[p.id] = p
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": "close", "panel": "p1"}, reply=out)
    assert out.ultima["ok"] is True and "p1" not in c.panels


# ==========================================================================
# 4. JS: eval e gadgets
# ==========================================================================
def _panel_ligado(cena, pid="p1"):
    c, t = cena
    p = FakePanel(pid, ws={"id": "ws1"})
    c.panels[pid] = p
    c.edges.append(FakeEdge(t, p))
    return c, t, p


def test_eval_manda_o_codigo_do_agente_no_envelope(cena):
    c, t, p = _panel_ligado(cena)
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "eval", "js": "document.title"}, reply=out)
    assert "(0,eval)" in p.recebeu[0]
    assert out.ultima["ok"] is True


def test_eval_via_envelope_para_o_resultado_do_gadget(cena):
    """O eval do agente devolve {ok,value}; o Bridge achata para a resposta."""
    c, t, p = _panel_ligado(cena)
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "eval", "js": "1+1"}, reply=out)
    assert out.ultima["ok"] is True and out.ultima["value"] == "resposta-fake"


def test_eval_js_grande_recusado(cena):
    c, t, p = _panel_ligado(cena)
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "eval", "js": "a" * (panelapi.MAX_JS + 1)}, reply=out)
    assert out.ultima["ok"] is False and p.recebeu == []


def test_eval_vazio_recusado(cena):
    c, t, p = _panel_ligado(cena)
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": "eval", "js": "   "}, reply=out)
    assert out.ultima["ok"] is False


def test_gadgets_vai_cru_sem_envelope(cena):
    """Gadget já devolve dict: passar pelo envelope faz o eval devolver
    undefined e o resultado se perder."""
    c, t, p = _panel_ligado(cena)
    b = bridge_de(c)
    for op, req in (("click", {"selector": "#b"}),
                    ("type", {"selector": "#q", "text": "oi"}),
                    ("press", {"key": "Enter"}),
                    ("page", {}), ("links", {})):
        p.recebeu.clear()
        b.handle({"from": "t1", "cmd": "panel", "op": op, "panel": "p1", **req}, reply=Coletor())
        assert p.recebeu, op
        assert "(0,eval)" not in p.recebeu[0], f"{op} nao devia ir pelo envelope"


def test_type_texto_grande_recusado(cena):
    c, t, p = _panel_ligado(cena)
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "type", "selector": "#q",
         "text": "a" * (panelapi.MAX_TEXT + 1), "panel": "p1"}, reply=out)
    assert out.ultima["ok"] is False and p.recebeu == []


def test_press_tecla_invalida_recusada_em_python(cena):
    c, t, p = _panel_ligado(cena)
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "press", "key": "'); alert(1)//",
         "panel": "p1"}, reply=out)
    assert out.ultima["ok"] is False and p.recebeu == []


def test_gadget_que_falha_devolve_ok_false(cena):
    """O painel responde {ok:false,error} — o Bridge não pode deixar virar ok:true."""
    class Falha(FakePanel):
        def run_js(self, js, on_value):
            on_value({"ok": False, "error": "seletor nao encontrado: #x"})
    c, t = cena
    p = Falha("p1", ws={"id": "ws1"})
    c.panels["p1"] = p
    c.edges.append(FakeEdge(t, p))
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "click", "selector": "#x"}, reply=out)
    assert out.ultima["ok"] is False
    assert "seletor nao encontrado" in out.ultima["error"]


def test_links_devolve_lista_sem_derrubar_o_bridge(cena):
    """`js_links` devolve um ARRAY puro. `{**env}` num list levanta TypeError —
    e exceção de Python dentro de callback do Qt é qFatal no PyQt: ABORT, sem
    traceback, app inteiro morre. Este teste existiu por causa disso."""
    class Lista(FakePanel):
        def run_js(self, js, on_value):
            on_value([{"text": "siga", "href": "http://x/outro", "raw": "/outro"}])
    c, t = cena
    p = Lista("p1", ws={"id": "ws1"})
    c.panels["p1"] = p
    c.edges.append(FakeEdge(t, p))
    out = Coletor()
    bridge_de(c).handle({"from": "t1", "cmd": "panel", "op": "links", "panel": "p1"}, reply=out)
    assert out.ultima["ok"] is True, out.ultima
    assert out.ultima["value"][0]["raw"] == "/outro"


def test_painel_que_some_antes_de_responder(cena):
    c, t = cena
    p = FakePanel("p1", ws={"id": "ws1"})
    p.vai_sumir = True
    c.panels["p1"] = p
    c.edges.append(FakeEdge(t, p))
    out = Coletor()
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "click", "selector": "#b"}, reply=out)
    assert out.respostas == []   # o fake não responde mesmo; sem callback pendurado


# ==========================================================================
# 5. _Conn: a guarda que impede resposta dupla
# ==========================================================================
class FakeSock:
    def __init__(self):
        self.escrito, self.fechado, self.deletado = b"", False, False

    def write(self, b):
        self.escrito += b

    def flush(self):
        pass

    def disconnectFromServer(self):
        self.fechado = True

    def deleteLater(self):
        self.deletado = True


def test_reply_escreve_uma_vez_so():
    c = _Conn(FakeSock())
    c.reply({"ok": True, "a": 1})
    c.reply({"ok": True, "b": 2})     # watchdog chegando depois
    assert json.loads(c.sock.escrito)["a"] == 1
    assert c.sock.escrito.count(b"\n") == 1, "duas respostas no mesmo socket"
    assert c.sock.deletado is True


def test_callback_repetido_nao_escreve_duas_vezes():
    """O risco nº 1 do plano: callback do Chromium e watchdog sao dois slots da
    main thread e podem disparar em qualquer ordem — o mesmo socket nao pode
    receber duas respostas."""
    class DuasVezes(FakePanel):
        def run_js(self, js, on_value):
            on_value({"found": True})
            on_value({"found": False})     # watchdog chegando logo depois
    c = FakeCanvas({"id": "ws1"})
    t = FakeTerm("t1", "tester", {"id": "ws1"})
    c.windows["t1"] = t
    p = DuasVezes("p1", ws={"id": "ws1"})
    c.panels["p1"] = p
    c.edges.append(FakeEdge(t, p))

    conn = _Conn(FakeSock())
    bridge_de(c).handle(
        {"from": "t1", "cmd": "panel", "op": "click", "selector": "#b"}, reply=conn.reply)
    assert conn.sock.escrito.count(b"\n") == 1, conn.sock.escrito


def test_reply_tolera_socket_morto():
    class Morto(FakeSock):
        def write(self, b):
            raise RuntimeError("socket destruido")
    c = _Conn(Morto())
    c.reply({"ok": True})   # não pode levantar
    assert c.sock.deletado is True