"""Ponta a ponta: `tayama panel ...` de verdade, contra Chromium de verdade.

Este é o arquivo que fecha o ciclo. Os outros provam as peças isoladas
(`test_panelapi_offline.py` prova o envelope e os gadgets; `test_bridge_panel.py`
prova o roteamento com dublês); aqui nada é dublê:

  * InfiniteCanvas real, BrowserWindow real, Chromium real;
  * Bridge real no QLocalServer real, em `/tmp/tayama-<uid>.sock`;
  * cliente UNIX de verdade — o mesmo caminho de `bin/tayama`.

Por isso é também a prova de que a resposta ASSÍNCRONA sai pelo mesmo socket:
se `handle()` devolvesse `None` sem responder nada, o cliente ficaria pendurado
até o timeout do socket, e os testes falhariam por timeout, não por asserção.

Sem internet: sobe um ThreadingHTTPServer em 127.0.0.1.

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_panel_e2e.py -v

Rodar ISOLADO — junto de outros arquivos que criam BrowserWindow, o teardown do
QtWebEngine faz core dump (o mesmo mecanismo e motivo de test_restore_flow.py:82-94).
"""
import os, sys, json, time, socket, threading, subprocess, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer

try:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    HAS_QT = True
    _ERR = ""
except Exception as exc:  # pragma: no cover - depende do ambiente
    HAS_QT = False
    _ERR = f"{type(exc).__name__}: {exc}"

pytestmark = [
    pytest.mark.skipif(not HAS_QT, reason=f"exige PyQt6 + QtWebEngine: {_ERR}"),
]

HTML = b"""<html><head><title>Alvo Local</title></head><body>
<h1 id=t>Ola Tayama</h1>
<input id=q name=q>
<form id=f action="/busca"><input id=g name=q></form>
<button id=b onclick="document.getElementById('r').textContent='clicou'">Enviar</button>
<div id=r></div>
<a id=a href="/outro">siga</a>
<script>window.__n=0;
document.getElementById('q').addEventListener('input',function(){window.__n++;});</script>
</body></html>"""

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def app():
    q = QApplication.instance() or QApplication(["tayama-e2e"])
    _APP.append(q)
    return q


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


_APP = []            # o QApplication do módulo, para os helpers


def _drain(qapp, n=5):
    """Drena os deleteLater. Sem isso o processo morre em SIGSEGV DEPOIS do
    pytest imprimir o resumo — resultado truncado, sem veredicto."""
    for _ in range(n):
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def tela(app, site, tmp_path, monkeypatch):
    """Canvas + Bridge + socket reais, com um painel apontando para o site local.

    Devolve (canvas, bridge, sock_path, client).
    """
    from tayama import config, workspaces
    from tayama.ui import InfiniteCanvas
    from tayama.bridge import Bridge

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config, "DIR", home)
    monkeypatch.setattr(config, "CFG", home / "config.json")
    monkeypatch.setattr(config, "SKILLS", home / "skills")
    monkeypatch.setenv("TAYAMA_HOME", str(home))
    config.ensure()

    # Socket deste teste, para não colidir com um Tayama em execução na máquina.
    sock = str(tmp_path / "tayama-e2e.sock")
    monkeypatch.setattr(config, "SOCK", sock)

    ws = workspaces.add("E2E", str(home))
    c = InfiniteCanvas()
    c.resize(1200, 800)
    c.set_current_ws(ws)
    # O canvas liga um QTimer de 33ms que chama Edge.refresh() em TODAS as
    # setas. A _Seta daqui não é um QGraphicsPathItem, então refresh() levanta
    # AttributeError dentro do slot — e uma exceção de Python escapando de um
    # slot é qFatal no PyQt: ABORT, sem traceback, sem veredicto do pytest.
    # Este teste não desenha seta nenhuma, então o timer é puro risco.
    for _tmr in c.findChildren(QTimer):
        _tmr.stop()

    # Terminal fictício: só o que o Bridge lê dele (id, name, workspace,
    # role_name, agent, receive). Um FloatingWindow real abriria um PTY com um
    # agente de verdade — e `__new__` sem `__init__` quebra o QObject do Qt
    # ("super-class __init__() was never called"). O terminal é a ponta que NÃO
    # estamos testando: o que importa aqui é o painel, o Chromium e o socket.
    c.windows["t1"] = _Term(ws)
    c._test_ws = ws          # para os testes que montam um segundo terminal

    b = Bridge(c)
    _drain(app)
    yield c, b, sock, _Cliente(sock)
    _fechar(c, app)


class _Cliente:
    """Cliente UNIX idêntico ao caminho de `bin/tayama`.

    O I/O vai numa THREAD, e isso é obrigatório, não conveniência: o `page`/
    `click`/`eval` respondem de um callback do Chromium, que só roda se o event
    loop do Qt estiver girando. Um `recv()` bloqueante na thread principal
    congelaria o loop e o request em voo nunca responderia — deadlock. Na vida
    real não existe esse conflito porque o agente é outro processo; aqui a
    thread reproduz essa separação.
    """

    def __init__(self, path):
        self.path = path

    def pedir(self, req, timeout=25):
        caixa = {}

        def _io():
            try:
                s = socket.socket(socket.AF_UNIX)
                s.settimeout(timeout)
                s.connect(self.path)
                s.sendall((json.dumps(req) + "\n").encode())
                buf = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                s.close()
                caixa["r"] = buf
            except Exception as e:            # noqa: BLE001 — repassado como falha
                caixa["e"] = e

        t = threading.Thread(target=_io, daemon=True)
        t.start()
        # A thread espera; a principal bombeia o Qt. Por isso o `while`.
        while t.is_alive():
            _APP[-1].processEvents()
            t.join(0.01)
        if "e" in caixa:
            raise caixa["e"]
        buf = caixa.get("r") or b""
        assert buf, "resposta vazia — o Bridge não respondeu (request em voo perdido?)"
        return json.loads(buf)


class _Term:
    """Terminal fictício. Duck-type do que a Bridge lê — ver a nota na fixture."""

    def __init__(self, ws):
        self.id, self.name, self.workspace = "t1", "tester", ws
        self.role_name, self.agent = "Tester", {"name": "Claude Code"}
        self.recebeu = []

    def receive(self, src, text):
        self.recebeu.append(text)


class _Seta:
    """Seta de ligação. A Bridge só lê `src`/`dst` (peers_of, ui.py:380); o
    Edge real é um QGraphicsItem e exigiria um FloatingWindow de verdade."""

    def __init__(self, src, dst):
        self.src, self.dst = src, dst


def _painel(c, site, nome="alvo"):
    """Cria o painel e espera a página ficar USÁVEL.

    Esperar `state == "ok"` não basta, e foi medido: `_load_finished`
    (browser.py:139) marca OK mesmo em load que falhou, e o `ok` pode chegar
    antes do documento existir — o comando seguinte recebia
    "seletor nao encontrado: #q" de uma página ainda sem `#q`. Por isso a
    espera real é pelo título, que só existe depois do parse.
    """
    p = c.add_panel(site, name=nome)
    _espera(lambda: p.title.text() == "Alvo Local"
            and p.view.url().toString().startswith(site))
    assert p.title.text() == "Alvo Local", (
        f"pagina {site} nao carregou: state={p.state} "
        f"url={p.view.url().toString()} title={p.title.text()!r}")
    return p


def _espera(pred, ms=15000):
    """Espera a condição pred() enquanto o event loop corre. Sem isto o Chromium
    nunca carrega: nada pumpa os eventos."""
    fim = time.monotonic() + ms / 1000
    while time.monotonic() < fim:
        _APP[-1].processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


def _tirar_da_cena(item):
    if item is None:
        return
    # _Seta e _Term não são QGraphicsItem — saem daqui sem tocar na cena.
    if not hasattr(item, "scene"):
        return
    sc = item.scene()
    if sc is not None:
        sc.removeItem(item)


def _fechar(c, qapp):
    """Fecha o canvas sem derrubar o processo.

    Mesma ordem obrigatória de test_restore_flow.py:123-160, e pelo mesmo motivo
    medido — o timer de refresh das setas durante o processEvents() toca em
    widget em destruição, e o BrowserWindow destruido dentro do processEvents()
    desce QQuickWidget e mata o processo em SIGSEGV depois do resumo.
    """
    for tmr in c.findChildren(QTimer):
        tmr.stop()
    for e in list(c.edges):
        _tirar_da_cena(e)          # _Seta não é QGraphicsItem; o guard abaixo trata
    c.edges.clear()
    for pan in list(c.panels.values()):
        try:
            _tirar_da_cena(pan.proxy)
            pan.proxy = None
            pan.deleteLater()
        except RuntimeError:
            pass
    c.panels.clear()
    for win in list(c.windows.values()):
        try:
            _tirar_da_cena(getattr(win, "proxy", None))
        except RuntimeError:
            pass
    c.windows.clear()
    _drain(qapp)
    c.close()
    c.deleteLater()
    _drain(qapp)


def _liga(c, t, p):
    """Conecta terminal e painel pela seta — o que autoriza o acesso."""
    e = _Seta(t, p)
    c.edges.append(e)
    return e


# ==========================================================================
# 1. O ciclo completo pelo socket
# ==========================================================================
def test_peers_pelo_socket_real(tela, site):
    c, b, sock, cli = tela
    _painel(c, site, "docs")
    r = cli.pedir({"from": "t1", "cmd": "peers"})
    assert r["ok"] is True


def test_send_pelo_socket_real_continua_funcionando(tela):
    """A regressão que mais importa: `send`/`broadcast` não podem ter quebrado
    por causa do request-em-voo. Aqui a mensagem TEM que chegar no terminal."""
    c, b, sock, cli = tela
    outro = _Term(c._test_ws); outro.id, outro.name = "t2", "dev"
    c.windows["t2"] = outro
    _liga(c, c.windows["t1"], outro)
    r = cli.pedir({"from": "t1", "cmd": "send", "target": "dev", "text": "oi"})
    assert r["ok"] is True, r
    assert r["sent_to"] == ["dev"], r
    assert outro.recebeu == ["oi"], outro.recebeu


def test_panel_list_pelo_socket(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site, "docs")
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "list"})
    assert r["ok"] is True
    linha = [x for x in r["panels"] if x["id"] == p.id][0]
    assert linha["name"] == "docs"
    assert linha["state"] == "ok"


def test_peers_expoe_o_painel_com_id_e_kind(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site, "docs")
    _liga(c, c.windows["t1"], p)
    r = cli.pedir({"from": "t1", "cmd": "peers"})
    info = [x for x in r["peers"] if x["id"] == p.id][0]
    assert info["kind"] == "painel"
    assert info["id"] == p.id


# ==========================================================================
# 2. Ler a página — a resposta ASSÍNCRONA pelo mesmo socket
# ==========================================================================
def test_page_devolve_o_texto_real(tela, site):
    """Este é o teste que prova que o `None` do handle() não engoliu a resposta:
    se a resposta não saísse do callback, o cliente leria vazio."""
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "page", "panel": p.id})
    assert r["ok"] is True, r
    assert "Ola Tayama" in r["text"], r
    assert r["title"] == "Alvo Local"
    assert r["url"].startswith(site)


def test_page_full_traz_html(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "page", "panel": p.id, "full": True})
    assert "<h1" in r["html"], r


def test_eval_devolve_valor(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "eval",
                   "panel": p.id, "js": "1+1"})
    assert r["ok"] is True and r["value"] == 2, r


def test_eval_statement_tambem(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "eval",
                   "panel": p.id, "js": "var x=1; x+41"})
    assert r["ok"] is True and r["value"] == 42, r


def test_eval_js_invalido_vira_erro_com_mensagem(tela, site):
    """A regressão do envelope, agora ponta a ponta: `return (src)` devolveria
    `{"ok": true, "value": null}` e o agente acharia que rodou."""
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "eval",
                   "panel": p.id, "js": "sintaxe !!! ("})
    assert r["ok"] is False, r
    assert "SyntaxError" in r["error"], r


def test_eval_throw_vira_erro(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "eval",
                   "panel": p.id, "js": "throw new Error('boom')"})
    assert r["ok"] is False and "boom" in r["error"], r


# ==========================================================================
# 3. Mouse e teclado no DOM real
# ==========================================================================
def test_click_dispara_o_handler(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "click", "panel": p.id, "selector": "#b"})
    assert r["ok"] is True and r["found"] is True, r
    r2 = cli.pedir({"from": "t1", "cmd": "panel", "op": "eval",
                    "panel": p.id, "js": "document.getElementById('r').textContent"})
    assert r2["value"] == "clicou", r2


def test_click_em_seletor_ausente_da_erro(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "click",
                   "panel": p.id, "selector": "#naoexiste"})
    assert r["ok"] is False and "#naoexiste" in r["error"], r


def test_type_deixa_o_valor_exato(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "type",
                   "panel": p.id, "selector": "#q", "text": "abc"})
    assert r["ok"] is True and r["value"] == "abc", r
    r2 = cli.pedir({"from": "t1", "cmd": "panel", "op": "eval",
                    "panel": p.id, "js": "document.querySelector('#q').value"})
    assert r2["value"] == "abc", r2


def test_type_append_concatena(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    cli.pedir({"from": "t1", "cmd": "panel", "op": "type",
               "panel": p.id, "selector": "#q", "text": "ab"})
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "type", "panel": p.id,
                   "selector": "#q", "text": "cd", "append": True})
    assert r["value"] == "abcd", r
    r2 = cli.pedir({"from": "t1", "cmd": "panel", "op": "type",
                    "panel": p.id, "selector": "#q", "text": "zz"})
    assert r2["value"] == "zz", r2


def test_type_dispara_evento_input(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    cli.pedir({"from": "t1", "cmd": "panel", "op": "eval", "panel": p.id, "js": "window.__n=0"})
    cli.pedir({"from": "t1", "cmd": "panel", "op": "type",
               "panel": p.id, "selector": "#q", "text": "x"})
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "eval", "panel": p.id, "js": "window.__n"})
    assert r["value"] >= 1, r


def test_press_enter_submta_o_form(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    cli.pedir({"from": "t1", "cmd": "panel", "op": "eval", "panel": p.id,
               "js": "window.__sub=false;document.getElementById('f')"
                     ".addEventListener('submit',function(e){window.__sub=true;e.preventDefault();})"})
    cli.pedir({"from": "t1", "cmd": "panel", "op": "type",
               "panel": p.id, "selector": "#g", "text": "termo"})
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "press",
                   "panel": p.id, "key": "Enter", "selector": "#g"})
    assert r["ok"] is True and r["found"] is True, r
    r2 = cli.pedir({"from": "t1", "cmd": "panel", "op": "eval",
                    "panel": p.id, "js": "window.__sub"})
    assert r2["value"] is True, r2


def test_links_preserva_href_relativo(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "links", "panel": p.id})
    assert r["ok"] is True and isinstance(r["value"], list), r
    link = r["value"][0]
    assert link["raw"] == "/outro"
    assert link["href"].startswith(site)


# ==========================================================================
# 4. A URL que o agente pode mudar
# ==========================================================================
def test_navigate_muda_a_url_de_verdade(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    outro = site + "pagina2"
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "navigate", "panel": p.id, "url": outro})
    assert r["ok"] is True and r["url"] == outro, r
    assert _espera(lambda: p.view.url().toString().startswith(outro)), \
        "o Chromium não navegou de fato"


def test_navigate_recusa_file(tela, site):
    """A fronteira que segura o disco do usuário: `navigate file://` + `page`
    leria qualquer arquivo."""
    c, b, sock, cli = tela
    p = _painel(c, site, "docs")
    antes = p.url.text()
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "navigate",
                   "panel": p.id, "url": "file:///etc/passwd"})
    assert r["ok"] is False, r
    assert p.url.text() == antes


def test_navigate_recusa_data_url(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site, "docs")
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "navigate", "panel": p.id,
                   "url": "data:text/html,<script>alert(1)</script>"})
    assert r["ok"] is False, r


def test_back_e_reload(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    cli.pedir({"from": "t1", "cmd": "panel", "op": "navigate", "panel": p.id, "url": site + "x"})
    _espera(lambda: p.view.url().toString().startswith(site + "x"))
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "reload", "panel": p.id})
    assert r["ok"] is True, r
    r2 = cli.pedir({"from": "t1", "cmd": "panel", "op": "back", "panel": p.id})
    assert r2["ok"] is True, r2


def test_open_cria_painel_e_devolve_id(tela, site):
    c, b, sock, cli = tela
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "open", "url": site, "name": "novo"})
    assert r["ok"] is True and r["id"] in c.panels, r
    _espera(lambda: c.panels[r["id"]].state == "ok")


def test_open_recusa_file(tela):
    c, b, sock, cli = tela
    antes = len(c.panels)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "open", "url": "file:///etc/passwd"})
    assert r["ok"] is False and len(c.panels) == antes, r


def test_close_some_o_painel(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "close", "panel": p.id})
    assert r["ok"] is True and p.id not in c.panels, r


# ==========================================================================
# 5. Autorização: a seta é quem autoriza
# ==========================================================================
def test_sem_seta_e_sem_panel_da_erro_que_diz_o_que_fazer(tela, site):
    c, b, sock, cli = tela
    _painel(c, site)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "page"})
    assert r["ok"] is False
    assert "seta" in r["error"], r


def test_com_seta_usa_o_painel_ligado(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    _liga(c, c.windows["t1"], p)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "page"})
    assert r["ok"] is True and "Ola Tayama" in r["text"], r


def test_painel_de_outro_workspace_recusado(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    p.workspace = {"id": "OUTRO", "name": "Outro"}
    _liga(c, c.windows["t1"], p)
    r = cli.pedir({"from": "t1", "cmd": "panel", "op": "page", "panel": p.id})
    assert r["ok"] is False and "workspace" in r["error"], r


def test_origem_desconhecida_recusada(tela, site):
    c, b, sock, cli = tela
    r = cli.pedir({"from": "p1", "cmd": "panel", "op": "page"})
    assert r["ok"] is False and "origem desconhecido" in r["error"], r


# ==========================================================================
# 6. O CLI de verdade — `tayama panel ...` num subprocesso
# ==========================================================================
def _cli(sock, *args, tid="t1", timeout=40):
    """Roda o bin/tayama de verdade, como o agente faria.

    Não dá para usar subprocess.run: ele bloqueia a thread, e a thread é quem
    bombeia o event loop do Qt — o callback do Chromium nunca chegaria e o
    comando expIRARIA timeout. Então o subprocesso é esperado com poll, com o
    loop girando no meio. É a separação real: o agente é outro processo.
    """
    env = dict(os.environ, TAYAMA_SOCK=sock, TAYAMA_ID=tid)
    p = subprocess.Popen([sys.executable, os.path.join(BIN, "tayama"), *args],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, env=env)
    fim = time.monotonic() + timeout
    while p.poll() is None and time.monotonic() < fim:
        _APP[-1].processEvents()
        time.sleep(0.01)
    if p.poll() is None:
        p.kill()
        raise AssertionError(f"tayama {' '.join(args)} nao respondeu em {timeout}s")
    out, err = p.communicate()
    return subprocess.CompletedProcess(p.args, p.returncode, out, err)


def test_cli_page_imprime_o_texto(tela, site):
    """O agente não fala JSON: fala com o binário. Se o formatador não conhece a
    resposta nova, cai no KeyError de antes."""
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = _cli(sock, "panel", "page", "--panel", p.id)
    assert r.returncode == 0, r.stderr
    assert "Ola Tayama" in r.stdout, r.stdout
    assert "Traceback" not in r.stderr


def test_cli_eval_imprime_o_valor(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = _cli(sock, "panel", "eval", "--panel", p.id, "1+1")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "2", r.stdout


def test_cli_navega(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = _cli(sock, "panel", "navigate", site + "y", "--panel", p.id)
    assert r.returncode == 0, r.stderr


def test_cli_recusa_file_sem_traceback(tela, site):
    c, b, sock, cli = tela
    p = _painel(c, site)
    r = _cli(sock, "panel", "navigate", "file:///etc/passwd", "--panel", p.id)
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr
    assert "erro:" in r.stdout.lower() or "erro:" in r.stderr.lower(), (r.stdout, r.stderr)


def test_cli_op_desconhecida_sem_traceback(tela):
    _, _, sock, _ = tela
    r = _cli(sock, "panel", "inexistente")
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr


def test_cli_peers_continua(tela, site):
    """`peers` mostra o painel com o marcador [painel] — é assim que o agente
    descobre que existe um navegador ligado a ele."""
    c, b, sock, cli = tela
    p = _painel(c, site, "docs")
    _liga(c, c.windows["t1"], p)
    r = _cli(sock, "peers")
    assert r.returncode == 0, r.stderr
    assert "docs" in r.stdout, r.stdout
    assert "[painel]" in r.stdout, r.stdout