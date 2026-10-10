"""Prova real do envelope e dos gadgets de DOM contra um Chromium de verdade.

Nao usa internet: sobe um ThreadingHTTPServer em 127.0.0.1 servindo HTML
estatico. O alvo e a regressão do envelope — se alguem trocar `(0, eval)` por
`return (src)`, o teste de JS invalido falha, porque esse wrapper devolve None
em silencio em vez de erro.

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_panelapi_offline.py -v
"""
import os, sys, time, threading, json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, QUrl

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PyQt6.QtWidgets import QApplication
    HAS_QT = True
    _ERR = ""
except Exception as exc:  # pragma: no cover - depende do ambiente
    HAS_QT = False
    _ERR = f"{type(exc).__name__}: {exc}"

from tayama import panelapi

pytestmark = [
    pytest.mark.skipif(not HAS_QT, reason=f"exige PyQt6 + QtWebEngine: {_ERR}"),
]

HTML = b"""<html><head><title>Alvo Local</title></head><body>
<h1 id=t>Ola Tayama</h1>
<input id=i name=q>
<form id=f action="/busca"><input id=g name=q></form>
<button id=b onclick="document.getElementById('r').textContent='clicou'">Enviar</button>
<div id=r></div>
<a id=a href="/outro">siga</a>
<script>window.__n=0; document.getElementById('i').addEventListener('input', function(){window.__n++;});</script>
</body></html>"""


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
    return QApplication.instance() or QApplication(["tayama-panelapi"])


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module")
def view(app, site):
    """Uma view real, numa cena solta, com a página carregada."""
    v = QWebEngineView()
    v.load(QUrl(site))
    _pump(app, lambda: v.page().runJavaScript("document.readyState") is not None
          and _sync(v, "document.readyState") == "complete")
    yield v
    # O teardown do QtWebEngine precisa de drains explicitos. Sem o
    # sendPostedEvents(DeferredDelete), o widget marcado com deleteLater()
    # continua vivo e o processo morre em SIGSEGV DEPOIS do pytest imprimir o
    # resumo — resultado truncado, sem veredicto (o mesmo mecanismo e motivo
    # de tests/test_restore_flow.py:82-94 e :123-160).
    v.setPage(None)
    v.deleteLater()
    for _ in range(5):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del v


def _sync(v, src, timeout=8000):
    """Roda JS e espera o callback, so para uso no teste (a API nao faz isso).

    Dois modos, e a distincao importa:
      * codigo do agente (`1+1`, `var x=1; x+41`) passa pelo envelope, que
        envolve em try/catch e devolve SEMPRE a string JSON;
      * os gadgets de panelapi ja devolvem um dict pronto com `ok`, entao vao
        crus. Envelopar um gadget faz o `eval` devolver undefined e o teste
        falha com KeyError — foi o que aconteceu na primeira rodada.
    """
    box, loop = {}, QEventLoop()

    def cb(val):
        box["v"] = val
        loop.quit()

    # Gadget = IIFE que ja retorna dict. Codigo do agente = expressao solta.
    cru = src.lstrip().startswith("(function")
    v.page().runJavaScript(src if cru else panelapi.envelope(src), cb)
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    raw = box.get("v")
    if raw is None:
        return None
    return raw if cru else json.loads(raw)


def _pump(qapp, pred, ms=15000):
    fim = time.monotonic() + ms / 1000
    while time.monotonic() < fim:
        qapp.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


# ==========================================================================
# 1. O envelope — a regressão que este arquivo existe para travar
# ==========================================================================
def test_envelope_devolve_valor_de_expressao(view):
    assert _sync(view, "1+1") == {"ok": True, "value": 2}


def test_envelope_devolve_valor_de_statement(view):
    """`var x=1; x+41` — quebra com new Function('return (...)'), so passa com eval."""
    assert _sync(view, "var x=1; x+41") == {"ok": True, "value": 42}


def test_envelope_erro_de_sintaxe_vira_erro_com_mensagem(view):
    """A regressão: `return (src)` devolveria None silencioso aqui."""
    r = _sync(view, "sintaxe !!! (")
    assert r is not None, "envelope devolveu None — o wrapper quebrou em silencio"
    assert r["ok"] is False
    assert "SyntaxError" in r["error"]


def test_envelope_excecao_js_vira_erro(view):
    r = _sync(view, "throw new Error('boom')")
    assert r["ok"] is False and "boom" in r["error"]


def test_envelope_preserva_tipos(view):
    assert _sync(view, "document.title")["value"] == "Alvo Local"
    assert _sync(view, "({a:1,b:[1,2]})")["value"] == {"a": 1, "b": [1, 2]}
    assert _sync(view, "[1,2,3]")["value"] == [1, 2, 3]
    assert _sync(view, "void 0")["value"] is None


def test_envelope_valor_circular_vira_erro_e_nao_explode(view):
    r = _sync(view, "var o={}; o.o=o; o")
    assert r["ok"] is False


def test_envelope_nao_quebra_com_aspas_e_nova_linha(view):
    r = _sync(view, 'var s = "aspas \'e\' nova\\nlinha"; s.length')
    assert r["ok"] is True and r["value"] == len("aspas 'e' nova\nlinha")


# ==========================================================================
# 2. Gadgets de DOM
# ==========================================================================
def test_click_dispara_handler(view):
    r = _sync(view, panelapi.js_click("#b"))
    assert r["found"] is True, r
    assert _sync(view, "document.getElementById('r').textContent")["value"] == "clicou"


def test_click_em_seletor_ausente_da_erro_com_nome(view):
    r = _sync(view, panelapi.js_click("#naoexiste"))
    assert r["ok"] is False
    assert "#naoexiste" in r["error"]


def test_type_deixa_valor_exato_e_dispara_input(view):
    _sync(view, "document.getElementById('i').value=''; window.__n=0;")
    r = _sync(view, panelapi.js_type("#i", "abc"))
    assert r["found"] is True and r["value"] == "abc", r
    assert _sync(view, "document.getElementById('i').value")["value"] == "abc"
    assert _sync(view, "window.__n")["value"] >= 1, "evento 'input' nao disparou"


def test_type_append_concatena_e_nao_duplica(view):
    _sync(view, "document.getElementById('i').value='ab';")
    r = _sync(view, panelapi.js_type("#i", "cd", append=True))
    assert r["value"] == "abcd"
    # Sem append, substitui em vez de concatenar.
    r2 = _sync(view, panelapi.js_type("#i", "zz"))
    assert r2["value"] == "zz"


def test_press_enter_submeta_o_form(view):
    _sync(view, "window.__sub=false; document.getElementById('f').addEventListener('submit',"
                "function(e){window.__sub=true; e.preventDefault();});")
    js, err = panelapi.js_press("Enter", selector="#g")
    assert err is None
    assert _sync(view, js)["found"] is True
    assert _sync(view, "window.__sub")["value"] is True


def test_press_rejeita_tecla_invalida_em_python(view):
    """A tecla é filtrada antes de chegar ao JS — nada de injeção aqui."""
    for chave in ("Enter'); alert(1)//", "a b", "", "x" * 40, "<script>"):
        assert panelapi.js_press(chave)[1] is not None, chave


def test_press_aceita_nomes_de_tecla_comuns(view):
    for chave in ("Enter", "Tab", "Escape", "a", "F1", "ArrowDown"):
        assert panelapi.js_press(chave)[1] is None, chave


def test_page_devolve_texto_e_url(view):
    r = _sync(view, panelapi.js_page())
    assert r["title"] == "Alvo Local", r
    assert "Ola Tayama" in r["text"]
    assert r["url"].startswith("http://127.0.0.1:")
    assert "truncated" in r


def test_page_full_traz_html(view):
    r = _sync(view, panelapi.js_page(with_html=True))
    assert "<h1" in r["html"], r


def test_links_preserva_href_relativo(view):
    r = _sync(view, panelapi.js_links())
    assert isinstance(r, list) and r
    link = r[0]
    assert link["raw"] == "/outro", "href relativo deve preservar o atributo cru"
    assert link["href"].startswith("http://127.0.0.1:"), "href resolvido para navegar"
    assert link["text"] == "siga"


# ==========================================================================
# 3. Allowlist de URL — o que segura o disco do usuario
# ==========================================================================
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file:///home/qualquer/.ssh/id_rsa",
    "FILE:///etc/passwd",
    "data:text/html,<script>alert(1)</script>",
    "javascript:alert(1)",
    "qrc:/etc/passwd",
    "ftp://exemplo/segredo",
    "",
    "   ",
])
def test_check_url_recusa_esquema_perigoso(url):
    assert panelapi.check_url(url) == (None, panelapi.check_url(url)[1])
    assert panelapi.check_url(url)[0] is None


@pytest.mark.parametrize("url", [
    "https://exemplo.com",
    "http://exemplo.com",
    "HTTP://EXEMPLO.COM",
    "  https://exemplo.com  ",
    "about:blank",
])
def test_check_url_aceita_http_e_https(url):
    assert panelapi.check_url(url)[0] is not None, url


def test_check_url_recusa_caractere_de_controle():
    """`java<TAB>script:` normaliza para javascript: no QUrl — precisa cair na string."""
    assert panelapi.check_url("java\tscript:alert(1)")[0] is None
    assert panelapi.check_url("http://x\ny")[0] is None


def test_check_url_recusa_nao_string():
    assert panelapi.check_url(None)[0] is None
    assert panelapi.check_url(123)[0] is None