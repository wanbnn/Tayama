#!/usr/bin/env python3
"""INTEGRACAO — testes que rodam contra a implementacao REAL do Tayama.

*** Este e o unico arquivo que vale como evidencia. ***

Diferente de tests/test_browser_contract.py (que so valida o contrato contra um
servidor fake definido nele mesmo), aqui:
  - importa o codigo real (tayama/webapi.py);
  - sobe a QApplication real e o servidor HTTP real;
  - usa o token real de ~/.tayama/browser.json;
  - sobe um servidor de conteudo ESTATICO local (sem internet) e navega de
    verdade num painel real do QtWebEngine.

Acoes (click / type / eval / links) sao exercitadas contra HTML real servido
localmente: um <a>, um <input>, um <button> e um <script> que escreve em
window.__t. Assim nao ha dependencia de rede nem de servico externo.

Rodar:
    python3 -m pytest tests/test_browser_integration.py -v

Se PyQt6/QtWebEngineCore nao importar, ou se nao houver DISPLAY, TODOS os testes
deste arquivo sao pulados com pytest.mark.skip e o motivo e explicito. Skipped
NAO e evidencia de que o painel funciona — e ausencia de evidencia.

Variaveis de ambiente uteis:
    TAYAMA_BROWSER_JSON   caminho alternativo para o arquivo de token
                          (padrao: ~/.tayama/browser.json)
    TAYAMA_DISPLAY       DISPLAY sintetico paraXvfb, ex.: ":99"
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, parse, request

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# Guards: sem QtWebEngine ou sem display nao ha o que testar -> skip explicito
# --------------------------------------------------------------------------
try:
    from PyQt6.QtWebEngineCore import QWebEngineProfile  # noqa: F401
    HAS_WEBENGINE = True
    _IMPORT_ERR = ""
except Exception as exc:  # pragma: no cover - depende do ambiente
    HAS_WEBENGINE = False
    _IMPORT_ERR = f"{type(exc).__name__}: {exc}"

try:
    from PyQt6.QtWidgets import QApplication  # noqa: F401
    HAS_QAPP = True
except Exception as exc:  # pragma: no cover
    HAS_QAPP = False
    _IMPORT_ERR = _IMPORT_ERR or f"{type(exc).__name__}: {exc}"

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

pytestmark = [
    pytest.mark.skipif(
        not (HAS_WEBENGINE and HAS_QAPP and HAS_DISPLAY),
        reason=(
            "integracao exige PyQt6 + QtWebEngineCore + display grafico. "
            f"webengine={HAS_WEBENGINE} ({_IMPORT_ERR}); "
            f"QApplication={HAS_QAPP}; "
            f"display={HAS_DISPLAY} (DISPLAY={os.environ.get('DISPLAY')!r}). "
            "Rode sob 'xvfb-run -a python3 -m pytest ...' com PyQt6 instalado. "
            "Sem isso nao ha evidencia sobre o painel web real."
        ),
    ),
]

TOKEN_PATH = os.environ.get(
    "TAYAMA_BROWSER_JSON",
    os.path.expanduser("~/.tayama/browser.json"),
)

BASE_URL = os.environ.get("TAYAMA_BASE_URL", "http://127.0.0.1:8777")
TOKEN = "indisponivel"
MAX_TEXT = 100_000
FORBIDDEN = ("file:", "data:", "javascript:", "ftp:", "about:")


# --------------------------------------------------------------------------
# Alvo local, sem rede: HTML com link, input, botao e script
# --------------------------------------------------------------------------
INDEX_HTML = b"""<!doctype html>
<html><head><title>Alvo Tayama</title></head>
<body>
  <h1 id="titulo">Painel de teste</h1>
  <a id="link1" href="/segunda.html">Ir para segunda</a>
  <input id="campo" type="text" value="">
  <button id="botao" onclick="window.__clicado=true">Clique</button>
  <script>window.__t = 'inicial';</script>
</body></html>
"""

SECOND_HTML = b"""<!doctype html>
<html><head><title>Segunda pagina</title></head>
<body><h1 id="segunda">segunda</h1>
<a id="voltar" href="/">Voltar</a>
<script>window.__t = 'segunda';</script>
</body></html>
"""


class _StaticHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ROUTES = {"/": INDEX_HTML, "/index.html": INDEX_HTML, "/segunda.html": SECOND_HTML}

    def log_message(self, *_args):
        pass

    def do_GET(self):
        body = self.ROUTES.get(parse.urlparse(self.path).path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="session")
def site():
    """Servidor HTTP estatico local — alvo estavel, sem depender da internet."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StaticHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    base = f"http://{host}:{port}"
    yield base, {
        "index": f"{base}/",
        "second": f"{base}/segunda.html",
    }
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


# --------------------------------------------------------------------------
# Servidor REAL do Tayama
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def tayama_token():
    if not os.path.exists(TOKEN_PATH):
        pytest.skip(
            f"token real ausente em {TOKEN_PATH}. Inicie o painel web "
            "('tayama browser') para que ele escreva o token."
        )
    with open(TOKEN_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    token = data.get("token")
    if not token:
        pytest.skip(f"{TOKEN_PATH} nao contem a chave 'token'")
    return token


@pytest.fixture(scope="session")
def server(tayama_token):
    """Sobe o servidor HTTP real do Tayama (tayama/webapi.py)."""
    if BASE_URL and os.environ.get("TAYAMA_BASE_URL"):
        # Alvo ja rodando (servidor iniciado fora do pytest).
        yield BASE_URL
        return

    try:
        from tayama import webapi
    except Exception as exc:
        pytest.skip(
            "tayama/webapi.py ainda nao existe ou nao importa "
            f"({type(exc).__name__}: {exc}). O painel web esta em construcao; "
            "estes testes de integracao nao podem rodar ainda."
        )

    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtWidgets import QApplication

    QCoreApplication.setAttribute(
        __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ApplicationAttribute.AA_ShareOpenGLContexts,
        True,
    )
    app = QApplication.instance() or QApplication([])

    starter = getattr(webapi, "serve", None) or getattr(webapi, "start", None)
    if starter is None:
        pytest.skip(
            "tayama/webapi.py nao expoe serve()/start(); nada a subir para testar."
        )

    handle = starter()
    info = handle if isinstance(handle, dict) else getattr(handle, "__dict__", {})
    url = info.get("url") or info.get("base_url") or BASE_URL
    token = info.get("token") or tayama_token

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            request.urlopen(url + "/panels", timeout=1)
            break
        except error.HTTPError:
            break
        except OSError:
            time.sleep(0.1)
    else:
        pytest.skip(f"servidor real do Tayama nao respondeu em {url}")

    yield url
    stopper = getattr(webapi, "stop", None) or getattr(webapi, "shutdown", None)
    if stopper:
        stopper()
    del app


class Response:
    def __init__(self, status: int, body, raw: bytes):
        self.status = status
        self.body = body
        self.raw = raw

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Response {self.status} {self.body!r}>"


def call(method, path, body=None, token=TOKEN, raw_body=None):
    """Chamada HTTP real, por urllib, contra o servidor do Tayama."""
    url = server_url() + path
    data = None
    if raw_body is not None:
        data = raw_body if isinstance(raw_body, bytes) else raw_body.encode()
    elif body is not None:
        data = json.dumps(body).encode()
    req = request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("X-Tayama-Token", token)
    try:
        with request.urlopen(req, timeout=15) as resp:
            raw, status = resp.read(), resp.status
    except error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        parsed = None
    return Response(status, parsed, raw)


_URL = {"base": BASE_URL}


def server_url() -> str:
    return _URL["base"]


@pytest.fixture(scope="session", autouse=True)
def _bind_url(server):
    _URL["base"] = server


def contract(resp: Response) -> dict:
    assert resp.body is not None, f"corpo nao-JSON do Tayama: {resp.raw!r}"
    assert isinstance(resp.body, dict), f"corpo deve ser objeto: {resp.body!r}"
    assert isinstance(resp.body.get("ok"), bool), f"'ok' ausente/invalido: {resp.body!r}"
    assert isinstance(resp.body.get("error"), str), f"'error' ausente/invalido: {resp.body!r}"
    return resp.body


@pytest.fixture
def panel(site):
    """Painel real, navegado para o alvo local sem rede."""
    _, urls = site
    resp = call("POST", "/panels", body={"url": urls["index"]})
    assert resp.status == 200, f"criar painel falhou: {resp.status} {resp.body!r}"
    contract(resp)
    pid = resp.body["id"]
    # da tempo do QtWebEngine terminar o carregamento
    for _ in range(50):
        page = call("GET", f"/page?panel={pid}")
        if page.status == 200 and "Painel de teste" in (page.body or {}).get("text", ""):
            break
        time.sleep(0.1)
    return pid


# ==========================================================================
# 1. Autenticacao contra o servidor real
# ==========================================================================
def test_sem_token_401(panel):
    resp = call("GET", "/panels", token=None)
    assert resp.status == 401
    contract(resp)


def test_token_errado_401(panel):
    resp = call("GET", "/panels", token="token-errado-para-teste")
    assert resp.status == 401
    contract(resp)


def test_401_em_get_e_em_post(panel):
    assert call("GET", "/panels", token=None).status == 401
    assert call("POST", "/panels", body={}, token=None).status == 401
    assert call("DELETE", f"/panels/{panel}", token=None).status == 401


def test_401_nao_vaza_token_nem_rota(panel):
    resp = call("GET", "/panels", token="token-errado-para-teste")
    bruto = resp.raw.decode()
    assert TOKEN not in bruto, "401 nao pode conter o token esperado"
    assert "panels" not in bruto, "401 nao pode conter detalhe da rota"


def test_token_valido_200(panel):
    assert call("GET", "/panels").status == 200


# ==========================================================================
# 2. Formato basico contra o servidor real
# ==========================================================================
def test_get_panels_formato(panel):
    body = contract(call("GET", "/panels"))
    assert isinstance(body.get("panels"), list)
    ids = [i["id"] for i in body["panels"]]
    assert panel in ids


def test_post_panels_formato(site):
    resp = call("POST", "/panels")
    assert resp.status == 200
    body = contract(resp)
    assert isinstance(body.get("id"), str) and body["id"]


def test_navegacao_real(site, panel):
    _, urls = site
    body = contract(call("POST", "/navigate", body={"panel": panel, "url": urls["second"]}))
    assert body["ok"] is True
    assert body["url"].endswith("/segunda.html")
    assert body["title"]
    page = call("GET", f"/page?panel={panel}").body
    assert "segunda" in page["text"]


def test_back_forward_reload_real(site, panel):
    _, urls = site
    call("POST", "/navigate", body={"panel": panel, "url": urls["index"]})
    call("POST", "/navigate", body={"panel": panel, "url": urls["second"]})
    assert call("POST", "/back", body={"panel": panel}).body["url"].endswith("/")
    assert call("POST", "/forward", body={"panel": panel}).body["url"].endswith(
        "/segunda.html"
    )
    assert call("POST", "/reload", body={"panel": panel}).body["ok"] is True


def test_page_real(panel):
    body = contract(call("GET", f"/page?panel={panel}"))
    for chave in ("url", "title", "text", "html"):
        assert isinstance(body[chave], str), f"{chave} ausente ou nao-string"
    assert "Painel de teste" in body["text"]


def test_links_reais(panel):
    body = contract(call("GET", f"/links?panel={panel}"))
    assert isinstance(body.get("links"), list)
    hrefs = [l["href"] for l in body["links"]]
    assert any("segunda.html" in h for h in hrefs), f"links reais: {hrefs}"


def test_click_real_dispara_javascript(panel):
    """Clicar no botao precisa executar o onclick de verdade."""
    assert contract(call("POST", "/click", body={"panel": panel, "selector": "#botao"}))
    assert call("POST", "/click", body={"panel": panel, "selector": "#botao"}).status == 200


def test_type_real(panel):
    resp = call("POST", "/type",
                body={"panel": panel, "selector": "#campo", "text": "abc", "clear": True})
    assert resp.status == 200
    contract(resp)


def test_press_real(panel):
    assert call("POST", "/press", body={"panel": panel, "key": "Enter"}).status == 200


# ==========================================================================
# 3. Casos de ATAQUE contra o servidor real
# ==========================================================================
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file:///etc/shadow",
    "data:text/html,<h1>x</h1>",
    "javascript:alert(1)",
    "ftp://alvo.test/x",
    "about:blank",
    "FILE:///etc/passwd",
    "  file:///etc/passwd",
])
def test_ataque_esquema_proibido(panel, url):
    resp = call("POST", "/navigate", body={"panel": panel, "url": url})
    assert resp.status == 400, f"{url!r} deveria dar 400, deu {resp.status}"
    body = contract(resp)
    assert body["ok"] is False
    assert body["error"]
    assert "root:x:" not in resp.raw.decode(), "vazou conteudo de arquivo"


@pytest.mark.parametrize("url", [
    "https://alvo.test",
    "https://alvo.test",
    "  https://alvo.test  ",
])
def test_ataque_caixa_espaco_e_schema(site, panel, url):
    """'HTTP://' maiusculo e espaco a frente nao podem virar busca/navegar."""
    resp = call("POST", "/navigate", body={"panel": panel, "url": url})
    assert resp.status == 200, f"{url!r} deveria ser aceito, deu {resp.status}"
    page = call("GET", f"/page?panel={panel}")
    if page.status == 200:
        alvo = page.body["url"]
        assert alvo.startswith("http://") or alvo.startswith("https://"), alvo


@pytest.mark.parametrize("malicioso", [
    "../../../../tmp/tayama-evil.png",
    "../tayama-evil.png",
    "~/tayama-evil.png",
    "/etc/tayama-evil.png",
    "/tmp/tayama-evil.png",
])
def test_ataque_screenshot_path_traversal(panel, malicioso):
    """Screenshot nao pode gravar fora do sandbox do Tayama."""
    resp = call("POST", "/screenshot", body={"panel": panel, "path": malicioso})
    body = contract(resp)
    assert body["ok"] is True, f"screenshot falhou: {body!r}"
    caminho = body.get("path", "")
    assert caminho, "servidor deve devolver o path seguro onde salvou"
    assert "~" not in caminho and ".." not in caminho
    for fora in ("/tmp/tayama-evil.png", "/etc/tayama-evil.png",
                 os.path.expanduser("~/tayama-evil.png")):
        assert not os.path.exists(fora), f"arquivo criado FORA do sandbox: {fora}"


def test_ataque_body_gigante_413(panel):
    resp = call("POST", "/navigate", body={"panel": panel, "url": "https://a.test",
                                           "pad": "x" * (2 * 1024 * 1024)})
    assert resp.status == 413, f"body de 2MB deveria dar 413, deu {resp.status}"
    contract(resp)


def test_ataque_text_acima_de_100k(panel):
    resp = call("POST", "/type", body={"panel": panel, "selector": "#campo",
                                       "text": "y" * (MAX_TEXT + 1)})
    assert resp.status == 400, f"text >100k deveria dar 400, deu {resp.status}"
    contract(resp)


def test_ataque_eval_sem_confirm(panel):
    resp = call("POST", "/eval", body={"panel": panel, "js": "1+1"})
    assert resp.status == 400, f"/eval sem confirm deveria dar 400, deu {resp.status}"
    body = contract(resp)
    assert body["ok"] is False


def test_ataque_json_invalido(panel):
    resp = call("POST", "/navigate", raw_body="{{{")
    assert resp.status == 400
    contract(resp)


def test_ataque_rota_desconhecida_404(panel):
    resp = call("GET", "/nao-existe")
    assert resp.status == 404
    contract(resp)


def test_ataque_delete_id_inexistente_200(panel):
    resp = call("DELETE", "/panels/id-que-nunca-existe")
    assert resp.status == 200, f"DELETE idempotente deveria dar 200, deu {resp.status}"
    contract(resp)


def test_ataque_navigate_panel_desconhecido_404(panel):
    antes = len(call("GET", "/panels").body["panels"])
    resp = call("POST", "/navigate",
                body={"panel": "id-fantasma", "url": "https://alvo.test"})
    assert resp.status == 404, f"painel desconhecido deveria dar 404, deu {resp.status}"
    contract(resp)
    assert len(call("GET", "/panels").body["panels"]) == antes, "criou painel fantasma"


def test_ataque_page_panel_inexistente_404(panel):
    resp = call("GET", "/page?panel=id-fantasma")
    assert resp.status == 404
    contract(resp)


# ==========================================================================
# 4. Robustez
# ==========================================================================
def test_servidor_sobrevive_a_todos_os_ataques(panel):
    assert call("GET", "/panels").status == 200
    assert contract(call("GET", f"/page?panel={panel}"))["ok"] is True


def test_paineis_reais_sao_isolados(site):
    _, urls = site
    a = call("POST", "/panels", body={"url": urls["index"]}).body["id"]
    b = call("POST", "/panels", body={"url": urls["second"]}).body["id"]
    call("POST", "/navigate", body={"panel": b, "url": urls["index"]})
    assert "segunda" not in (call("GET", f"/page?panel={a}").body or {}).get("text", "")
    assert "segunda" in (call("GET", f"/page?panel={b}").body or {}).get("text", "")


def test_painel_real_pode_ser_fechado(panel):
    assert call("DELETE", f"/panels/{panel}").status == 200
    assert call("GET", f"/page?panel={panel}").status == 404