#!/usr/bin/env python3
"""CONTRATO HTTP do painel web — validado contra um servidor FAKE.

*** ATENCAO: estes testes validam o CONTRATO HTTP contra um servidor FAKE
*** definido NESTE MESMO ARQUIVO. Eles NAO validam a implementacao real do
*** Tayama — um bug no Tayama passa todos estes testes. Para evidencia real,
*** veja tests/test_browser_integration.py (sobe tayama/webapi.py de verdade).

Papel deste arquivo: fixar o contrato (status codes, formato JSON, limites,
regras de seguranca) para que servidor fake e servidor real sejam comparados
pela MESMA suite. Serve tambem como documentacao executavel do addendum.

Rodar (sem GUI, sem app, so stdlib + pytest):
    python3 -m pytest tests/test_browser_contract.py -v

O arquivo de integracao (o que vale como evidencia) roda separado:
    python3 -m pytest tests/test_browser_integration.py -v

Contrato coberto:
    Base http://127.0.0.1:8777 , header 'X-Tayama-Token: <token>'
    Toda resposta e JSON {"ok": bool, "error": str}
    'panel' = id do painel (padrao: o ultimo criado/focado)

      GET    /panels                  -> {ok, panels:[{id,title,url,state}]}
      POST   /panels    {url?}        -> {ok, id}          (http/https somente)
      DELETE /panels/<id>             -> {ok}              (idempotente)
      POST   /navigate   {panel,url}  -> {ok,url,title}    (http/https somente)
      POST   /search     {panel,q}    -> {ok,url,title}
      POST   /back | /forward | /reload {panel} -> {ok,url,title}
      GET    /page?panel=              -> {ok,url,title,text,html,truncated}
      GET    /links?panel=             -> {ok,links:[{text,href}]}
      POST   /click     {panel,selector}         -> {ok}
      POST   /type      {panel,selector,text,clear=true} -> {ok}   (text <= 100k)
      POST   /press     {panel,key}    -> {ok}
      POST   /eval      {panel,js,confirm} -> {ok,result}  (403 sem allow_eval)
      POST   /screenshot {panel,path?,full?} -> {ok,path}  (sandbox, ignora path)
"""

from __future__ import annotations

import ast
import base64
import hmac
import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, parse, request

import pytest

TOKEN = "token-de-teste"

# Limites do contrato (addendum do lider).
MAX_BODY_BYTES = 256 * 1024      # Content-Length acima disso -> 413
MAX_TEXT_CHARS = 100_000         # campo 'text' do /type acima disso -> 400
MAX_JS_CHARS = 64 * 1024          # campo 'js' do /eval acima disso -> 400
PAGE_TRUNCATE_CHARS = 100_000     # /page devolve truncated:true acima disso
ALLOWED_SCHEMES = ("http://", "https://")
FORBIDDEN_SCHEMES = ("file:", "data:", "javascript:", "ftp:", "about:")
# PNG 1x1 transparente — suficiente para validar o formato de png_base64.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)

BASE_URL = os.environ.get("TAYAMA_BASE_URL")
BASE_TOKEN = os.environ.get("TAYAMA_TOKEN", TOKEN)


# --------------------------------------------------------------------------
# Cliente HTTP minimo (stdlib)
# --------------------------------------------------------------------------
class Response:
    def __init__(self, status: int, body, raw: bytes):
        self.status = status
        self.body = body
        self.raw = raw

    def __repr__(self) -> str:  # pragma: no cover - debug
        return f"<Response {self.status} {self.body!r}>"


def call(
    method: str,
    path: str,
    body=None,
    token=BASE_TOKEN,
    raw_body=None,
    content_type="application/json",
) -> Response:
    url = (BASE_URL or server.base_url) + path
    data = None
    if raw_body is not None:
        data = raw_body if isinstance(raw_body, bytes) else raw_body.encode()
    elif body is not None:
        data = json.dumps(body).encode()
    req = request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", content_type)
    if token is not None:
        req.add_header("X-Tayama-Token", token)
    try:
        with request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            status = resp.status
    except error.HTTPError as exc:  # 4xx/5xx tambem tem corpo
        raw = exc.read()
        status = exc.code
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        parsed = None
    return Response(status, parsed, raw)


# --------------------------------------------------------------------------
# Servidor fake: implementacao de referencia do contrato
# --------------------------------------------------------------------------
def _bad_scheme(url: str) -> str | None:
    """Retorna o esquema proibido se a URL nao for aceito como http(s).

    Cobre o bypass por caixa/normalizacao: 'HTTP://' e aceito (normalizado),
    mas esquema proibido escrito com caixa alta tambem precisa ser barrado.
    """
    low = url.strip().lower()
    for scheme in FORBIDDEN_SCHEMES:
        if low.startswith(scheme):
            return scheme
    if not low.startswith(ALLOWED_SCHEMES):
        return "esquema desconhecido"
    return None


def _forbidden_in_query(text: str) -> str | None:
    """Em 'q' so barramos esquemas explicitamente proibidos.

    'q' e um termo de busca, nao uma URL: 'tayama' e um termo legitimo,
    entao aqui nao aplicamos a exigencia de http/https.
    """
    low = text.strip().lower()
    for scheme in FORBIDDEN_SCHEMES:
        if low.startswith(scheme):
            return scheme
    return None


class FakeBrowser:
    """Estado simulado de paineis. Nao acessa rede nem navegador real."""

    def __init__(self, allow_eval: bool = False) -> None:
        self.panels: dict[str, dict] = {}
        self.focus: str | None = None
        self._seq = 0
        # addendum 2: /eval so liga com browser.allow_eval=true
        self.allow_eval = allow_eval
        # addendum 3: servidor sempre grava em diretorio proprio
        self.shots_dir = tempfile.mkdtemp(prefix="tayama-shots-")

    # -- estado ---------------------------------------------------------
    def create(self, url: str | None = None) -> str:
        self._seq += 1
        pid = f"p{self._seq}"
        panel = {
            "id": pid,
            "url": url or "about:blank",
            "title": "Painel em branco" if url is None else f"Titulo de {url}",
            "text": "" if url is None else f"conteudo de {url}",
            "html": "<html><body></body></html>"
            if url is None
            else f"<html><body><p>conteudo de {url}</p></body></html>",
            "history": [url or "about:blank"],
            "index": 0,
            "fields": {},
            "state": "ok",
        }
        self.panels[pid] = panel
        self.focus = pid
        return pid

    def get(self, panel: str | None) -> dict | None:
        if panel:
            return self.panels.get(panel)
        return self.panels.get(self.focus) if self.focus else None

    def close(self, pid: str) -> bool:
        if pid not in self.panels:
            return False
        del self.panels[pid]
        if self.focus == pid:
            self.focus = next(iter(self.panels), None)
        return True

    # -- navegacao ------------------------------------------------------
    def _visit(self, panel: dict, url: str) -> None:
        panel["history"] = panel["history"][: panel["index"] + 1]
        panel["history"].append(url)
        panel["index"] = len(panel["history"]) - 1
        panel["url"] = url
        panel["title"] = f"Titulo de {url}"
        panel["text"] = f"conteudo de {url}"
        panel["html"] = f"<html><body><p>conteudo de {url}</p></body></html>"

    def navigate(self, panel: dict, url: str) -> dict:
        self._visit(panel, url)
        return panel

    def search(self, panel: dict, q: str) -> dict:
        url = "https://busca.example/?q=" + parse.quote(q)
        self._visit(panel, url)
        return panel

    def back(self, panel: dict) -> dict:
        if panel["index"] > 0:
            panel["index"] -= 1
            panel["url"] = panel["history"][panel["index"]]
        return panel

    def forward(self, panel: dict) -> dict:
        if panel["index"] < len(panel["history"]) - 1:
            panel["index"] += 1
            panel["url"] = panel["history"][panel["index"]]
        return panel


def make_handler(state: FakeBrowser):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        # -- infra ------------------------------------------------------
        def log_message(self, *_args):  # silencia o log durante os testes
            pass

        def _send(self, status: int, payload: dict) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def ok(self, status: int = 200, **extra) -> None:
            self._send(status, {"ok": True, "error": "", **extra})

        def fail(self, status: int, message: str) -> None:
            self._send(status, {"ok": False, "error": message})

        def _token(self) -> str | None:
            return self.headers.get("X-Tayama-Token")

        def _auth(self) -> bool:
            """Token checado ANTES de qualquer rota, em tempo constante."""
            sent = self.headers.get("X-Tayama-Token")
            if not sent or not hmac.compare_digest(sent, TOKEN):
                # Mensagem generica: nao revela rota nem token esperado.
                return self.fail(401, "nao autorizado")
            return True

        def _read_json(self):
            """Retorna (body, erro). erro = (status, mensagem) ou None."""
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                return None, (413, f"corpo excede {MAX_BODY_BYTES} bytes")
            raw = self.rfile.read(length) if length else b""
            if not raw.strip():
                return {}, None
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None, (400, "JSON invalido")
            if not isinstance(parsed, dict):
                return None, (400, "corpo deve ser um objeto JSON")
            return parsed, None

        def _panel_arg(self, body: dict, query: dict) -> str | None:
            return body.get("panel") or query.get("panel")

        def _resolve(self, panel_id):
            """Retorna (painel, erro). erro = (status, mensagem) ou None."""
            panel = state.get(panel_id)
            if panel is None:
                if panel_id:
                    return None, (404, f"painel inexistente: {panel_id}")
                return None, (404, "nenhum painel disponivel")
            return panel, None

        # -- roteamento -------------------------------------------------
        def do_GET(self):
            if not self._auth():
                return None
            parsed = parse.urlparse(self.path)
            route = parsed.path.rstrip("/") or "/"
            query = parse.parse_qs(parsed.query)
            query = {k: v[0] for k, v in query.items()}

            if route == "/panels":
                return self.ok(
                    panels=[
                        {
                            "id": p["id"],
                            "title": p["title"],
                            "url": p["url"],
                            "state": p["state"],
                        }
                        for p in state.panels.values()
                    ]
                )

            if route == "/page":
                panel, err = self._resolve(query.get("panel"))
                if err:
                    return self.fail(*err)
                truncated = (
                    len(panel["html"]) > PAGE_TRUNCATE_CHARS
                    or len(panel["text"]) > PAGE_TRUNCATE_CHARS
                )
                html, text = panel["html"], panel["text"]
                if truncated:
                    html, text = html[:PAGE_TRUNCATE_CHARS], text[:PAGE_TRUNCATE_CHARS]
                return self.ok(
                    url=panel["url"],
                    title=panel["title"],
                    text=text,
                    html=html,
                    truncated=truncated,
                )

            if route == "/links":
                panel, err = self._resolve(query.get("panel"))
                if err:
                    return self.fail(*err)
                return self.ok(
                    links=[
                        {"text": "Inicial", "href": "/"},
                        {"text": "Docs", "href": "https://docs.example/guia"},
                    ]
                )

            return self.fail(404, f"rota desconhecida: {route}")

        def do_POST(self):
            if not self._auth():
                return None
            body, err = self._read_json()
            if err:
                return self.fail(*err)
            route = parse.urlparse(self.path).path.rstrip("/") or "/"

            if route == "/panels":
                url = body.get("url")
                if url is not None and not isinstance(url, str):
                    return self.fail(400, "url deve ser string")
                if isinstance(url, str) and _bad_scheme(url):
                    return self.fail(
                        400, f"esquema nao permitido em url: {_bad_scheme(url)}"
                    )
                return self.ok(id=state.create(url.strip() if url else url))

            if route == "/navigate":
                url = body.get("url")
                if not url or not isinstance(url, str):
                    return self.fail(400, "campo 'url' obrigatorio")
                if _bad_scheme(url):
                    return self.fail(
                        400, f"esquema nao permitido em url: {_bad_scheme(url)}"
                    )
                panel, err = self._resolve(body.get("panel"))
                if err:
                    return self.fail(*err)
                state.navigate(panel, url.strip())
                return self.ok(url=panel["url"], title=panel["title"])

            if route == "/search":
                q = body.get("q")
                if not q or not isinstance(q, str):
                    return self.fail(400, "campo 'q' obrigatorio")
                if _forbidden_in_query(q):
                    return self.fail(
                        400, f"esquema nao permitido em q: {_forbidden_in_query(q)}"
                    )
                panel, err = self._resolve(body.get("panel"))
                if err:
                    return self.fail(*err)
                state.search(panel, q)
                return self.ok(url=panel["url"], title=panel["title"])

            if route in ("/back", "/forward", "/reload"):
                panel, err = self._resolve(body.get("panel"))
                if err:
                    return self.fail(*err)
                if route == "/reload":
                    state._visit(panel, panel["url"])
                else:
                    (state.back if route == "/back" else state.forward)(panel)
                return self.ok(url=panel["url"], title=panel["title"])

            if route == "/click":
                sel = body.get("selector")
                if not sel or not isinstance(sel, str):
                    return self.fail(400, "campo 'selector' obrigatorio")
                panel, err = self._resolve(body.get("panel"))
                if err:
                    return self.fail(*err)
                if sel.startswith("#inexistente"):
                    return self.fail(404, f"selector nao encontrado: {sel}")
                return self.ok()

            if route == "/type":
                sel, text = body.get("selector"), body.get("text")
                if not sel or not isinstance(sel, str):
                    return self.fail(400, "campo 'selector' obrigatorio")
                if text is not None and not isinstance(text, str):
                    return self.fail(400, "campo 'text' deve ser string")
                if text is not None and len(text) > MAX_TEXT_CHARS:
                    return self.fail(
                        400,
                        f"campo 'text' excede {MAX_TEXT_CHARS} caracteres "
                        f"({len(text)})",
                    )
                panel, err = self._resolve(body.get("panel"))
                if err:
                    return self.fail(*err)
                clear = body.get("clear", True)
                previous = "" if clear else panel["fields"].get(sel, "")
                panel["fields"][sel] = previous + (text or "")
                return self.ok()

            if route == "/press":
                key = body.get("key")
                if not key or not isinstance(key, str):
                    return self.fail(400, "campo 'key' obrigatorio")
                panel, err = self._resolve(body.get("panel"))
                if err:
                    return self.fail(*err)
                if not re.fullmatch(r"[A-Za-z0-9+\-]+", key):
                    return self.fail(400, f"tecla invalida: {key}")
                return self.ok()

            if route == "/eval":
                if not state.allow_eval:
                    return self.fail(403, "eval desabilitado")
                js = body.get("js")
                if not js or not isinstance(js, str):
                    return self.fail(400, "campo 'js' obrigatorio")
                if len(js) > MAX_JS_CHARS:
                    return self.fail(
                        400,
                        f"campo 'js' excede {MAX_JS_CHARS} caracteres ({len(js)})",
                    )
                if body.get("confirm") is not True:
                    return self.fail(400, "campo 'confirm' obrigatorio (true)")
                panel, err = self._resolve(body.get("panel"))
                if err:
                    return self.fail(*err)
                result = _fake_eval(js, panel)
                if result is _INVALID:
                    return self.fail(400, f"JS invalido: {js}")
                return self.ok(result=result)

            if route == "/screenshot":
                panel, err = self._resolve(body.get("panel"))
                if err:
                    return self.fail(*err)
                # 'path' do cliente e IGNORADO por contrato: sempre sandbox.
                state._seq += 1
                safe = os.path.join(
                    state.shots_dir, f"panel-{panel['id']}-{state._seq}.png"
                )
                try:
                    with open(safe, "wb") as fh:
                        fh.write(PNG_1PX)
                except OSError as exc:
                    return self.fail(500, f"falha ao gravar captura: {exc}")
                return self.ok(path=safe)

            return self.fail(404, f"rota desconhecida: {route}")

        def do_PUT(self):
            if not self._auth():
                return None
            return self.fail(405, "metodo nao suportado: PUT")

        def do_DELETE(self):
            if not self._auth():
                return None
            route = parse.urlparse(self.path).path.rstrip("/") or "/"
            match = re.fullmatch(r"/panels/([^/]+)", route)
            if not match:
                return self.fail(404, f"rota desconhecida: {route}")
            # addendum 6: DELETE e idempotente -> 200 mesmo se nao existir
            state.close(match.group(1))
            return self.ok()

    return Handler


_INVALID = object()


def _fake_eval(js: str, panel: dict):
    """Interpretador minimo, so para validar o formato de 'result'."""
    expr = js.strip().rstrip(";")
    if expr in ("document.title", "document.querySelector('title').textContent"):
        return panel["title"]
    if expr in ("location.href", "window.location.href"):
        return panel["url"]
    if expr == "document.body.innerText":
        return panel["text"]
    try:
        return ast.literal_eval(expr)
    except (ValueError, SyntaxError):
        pass
    # Aritmetica simples (+ - * /) — o suficiente para validar o tipo do result.
    try:
        node = ast.parse(expr, mode="eval")
        if all(
            isinstance(n, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                           ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd))
            for n in ast.walk(node)
        ):
            return eval(compile(node, "<js>", "eval"), {"__builtins__": {}}, {})
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError, NameError):
        pass
    # JS invalido: sintaxe nao suportada pelo interpretador minimo.
    return _INVALID


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
class FakeServer:
    def __init__(self, state: FakeBrowser):
        self.state = state
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


server: FakeServer | None = None


@pytest.fixture(scope="session", autouse=True)
def fake_server():
    global server
    if BASE_URL:  # alvo real: nao sobe o fake
        yield None
        return
    server = FakeServer(FakeBrowser())
    yield server
    server.stop()


@pytest.fixture
def state(fake_server) -> FakeBrowser:
    fake_server.state.panels.clear()
    fake_server.state.focus = None
    fake_server.state._seq = 0
    fake_server.state.allow_eval = False  # padrao do contrato
    return fake_server.state


@pytest.fixture
def panel(state) -> str:
    return state.create("https://exemplo.test/pagina1")


def assert_contract(resp: Response) -> dict:
    """Toda resposta, com ou sem erro, deve ser JSON com ok(bool)+error(str)."""
    assert resp.body is not None, f"corpo nao-JSON: {resp.raw!r}"
    assert isinstance(resp.body, dict), f"corpo deve ser objeto: {resp.body!r}"
    assert isinstance(resp.body.get("ok"), bool), f"'ok' ausente/invalido: {resp.body!r}"
    assert isinstance(resp.body.get("error"), str), f"'error' ausente/invalido: {resp.body!r}"
    if not resp.body["ok"]:
        assert resp.body["error"], "resposta de erro deve ter 'error' preenchido"
    return resp.body


# ==========================================================================
# 1. Autenticacao por token
# ==========================================================================
def test_sem_token_responde_401(panel):
    resp = call("GET", "/panels", token=None)
    assert resp.status == 401
    assert_contract(resp)
    assert resp.body["ok"] is False


def test_token_invalido_responde_401(panel):
    assert_contract(call("GET", "/panels", token="token-errado"))
    assert call("GET", "/panels", token="").status == 401


def test_401_nao_vaza_token_nem_detalhe_da_rota(panel):
    for method, path, body in [
        ("GET", "/panels", None),
        ("GET", "/nao-existe-secreta", None),
        ("POST", "/navigate", {"panel": panel, "url": "https://a.test"}),
        ("DELETE", "/panels/x", None),
    ]:
        resp = call(method, path, body=body, token="token-errado")
        assert resp.status == 401
        assert_contract(resp)
        bruto = resp.raw.decode()
        assert TOKEN not in bruto, "401 nao pode conter o token esperado"
        assert path.strip("/") not in bruto, "401 nao pode conter detalhe da rota"
        assert resp.body["error"] == "nao autorizado"


def test_401_e_verificado_antes_da_rota(panel):
    """Rota desconhecida + token invalido ainda da 401 (nao 404)."""
    assert call("GET", "/nao-existe", token=None).status == 401
    assert call("POST", "/nao-existe", body={}, token=None).status == 401


def test_401_em_get_e_em_delete(panel):
    assert call("GET", "/panels", token=None).status == 401
    assert call("DELETE", f"/panels/{panel}", token=None).status == 401


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/panels", None),
        ("POST", "/panels", {}),
        ("POST", "/navigate", {"url": "https://a.test"}),
        ("GET", "/page", None),
        ("GET", "/links", None),
        ("DELETE", "/panels/qualquer", None),
    ],
)
def test_sem_token_401_em_todas_as_rotas(method, path, body, panel):
    resp = call(method, path, body=body, token=None)
    assert resp.status == 401, f"{method} {path} nao exigiu token"
    assert_contract(resp)


def test_delete_sem_token_nao_fecha_painel(panel):
    assert call("DELETE", f"/panels/{panel}", token=None).status == 401
    assert panel in [i["id"] for i in call("GET", "/panels").body["panels"]]


def test_token_valido_responde_200(state):
    assert call("GET", "/panels").status == 200


# ==========================================================================
# 2. Rotas desconhecidas
# ==========================================================================
@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/nao-existe"),
        ("GET", "/"),
        ("POST", "/nao-existe"),
        ("POST", "/click-all"),
        ("DELETE", "/panels"),
    ],
)
def test_rota_desconhecida_responde_404_json(method, path, state):
    resp = call(method, path, body={} if method == "POST" else None)
    assert resp.status == 404, f"{method} {path} deveria dar 404"
    assert_contract(resp)
    assert resp.body["ok"] is False


def test_metodo_nao_suportado_responde_405_json(state):
    resp = call("PUT", "/panels", body={})
    assert resp.status == 405, f"PUT deveria dar 405, deu {resp.status}"
    assert_contract(resp)
    assert resp.body["ok"] is False


def test_rota_desconhecida_nao_derruba_o_servidor(state):
    call("GET", "/nao-existe")
    assert call("GET", "/panels").status == 200


# ==========================================================================
# 3. JSON invalido
# ==========================================================================
@pytest.mark.parametrize("raw", ["{{{", "", "nao-e-json", "[1, 2, 3]", '"uma string"'])
def test_json_invalido_responde_400(raw, panel):
    resp = call("POST", "/navigate", raw_body=raw)
    assert resp.status == 400, f"corpo {raw!r} deveria dar 400"
    assert_contract(resp)


def test_json_invalido_nao_derruba_o_servidor(panel):
    call("POST", "/navigate", raw_body="{{{")
    assert call("GET", "/panels").status == 200


def test_campos_obrigatorios_ausentes_respondem_400(panel):
    for path, body in [
        ("/navigate", {"panel": panel}),
        ("/search", {"panel": panel}),
        ("/click", {"panel": panel}),
        ("/type", {"panel": panel}),
        ("/press", {"panel": panel}),
    ]:
        resp = call("POST", path, body=body)
        assert resp.status == 400, f"{path} sem campo obrigatorio"
        assert_contract(resp)


def test_tipos_invalidos_respondem_400(panel):
    assert call("POST", "/navigate", body={"panel": panel, "url": 123}).status == 400
    assert call("POST", "/type", body={"panel": panel, "text": 123}).status == 400


def test_tipos_invalidos_respondem_400_com_eval_ligado(panel, state):
    state.allow_eval = True
    assert call("POST", "/eval", body={"panel": panel, "js": 123,
                                       "confirm": True}).status == 400
    assert call("POST", "/eval", body={"panel": panel, "js": "1+1",
                                       "confirm": "sim"}).status == 400


# ==========================================================================
# 4. Painel inexistente
# ==========================================================================
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/page?panel=nao-existe", None),
        ("GET", "/links?panel=nao-existe", None),
        ("POST", "/navigate", {"panel": "nao-existe", "url": "https://a.test"}),
        ("POST", "/search", {"panel": "nao-existe", "q": "termo"}),
        ("POST", "/back", {"panel": "nao-existe"}),
        ("POST", "/forward", {"panel": "nao-existe"}),
        ("POST", "/reload", {"panel": "nao-existe"}),
        ("POST", "/click", {"panel": "nao-existe", "selector": "#a"}),
        ("POST", "/type", {"panel": "nao-existe", "selector": "#a", "text": "x"}),
        ("POST", "/press", {"panel": "nao-existe", "key": "Enter"}),
        ("POST", "/screenshot", {"panel": "nao-existe"}),
    ],
)
def test_painel_inexistente_responde_404_json(method, path, body, panel):
    resp = call(method, path, body=body)
    assert resp.status == 404, f"{method} {path} com painel inexistente"
    assert_contract(resp)
    assert resp.body["ok"] is False


def test_eval_com_painel_inexistente_403_quando_desabilitado(panel):
    """addendum 2 tem precedencia: 403 antes de resolver o painel."""
    resp = call("POST", "/eval", body={"panel": "nao-existe", "js": "1+1",
                                       "confirm": True})
    assert resp.status == 403
    assert_contract(resp)


def test_eval_com_painel_inexistente_404_quando_ligado(panel, state):
    state.allow_eval = True
    resp = call("POST", "/eval", body={"panel": "nao-existe", "js": "1+1",
                                       "confirm": True})
    assert resp.status == 404
    assert_contract(resp)


def test_sem_paineis_responde_404_controlado(state):
    assert list(call("GET", "/panels").body["panels"]) == []
    resp = call("GET", "/page")
    assert resp.status == 404
    assert_contract(resp)


# ==========================================================================
# 5. Formato de resposta de cada endpoint
# ==========================================================================
def test_get_panels_formato(state):
    pid = state.create("https://a.test")
    body = assert_contract(call("GET", "/panels"))
    assert len(body["panels"]) == 1
    item = body["panels"][0]
    assert {"id", "title", "url", "state"} <= set(item)
    assert item["id"] == pid
    assert item["state"] == "ok"
    assert isinstance(item["title"], str) and isinstance(item["url"], str)


def test_get_panels_vazio_formato(state):
    body = assert_contract(call("GET", "/panels"))
    assert body["ok"] is True
    assert body["panels"] == []


def test_post_panels_formato(state):
    body = assert_contract(call("POST", "/panels"))
    assert body["ok"] is True
    assert isinstance(body["id"], str) and body["id"]


def test_post_panels_com_url_formato(state):
    body = assert_contract(call("POST", "/panels", body={"url": "https://b.test"}))
    pid = body["id"]
    listed = call("GET", "/panels").body["panels"]
    assert listed[0]["id"] == pid
    assert listed[0]["url"] == "https://b.test"


def test_ids_unicos(state):
    ids = {call("POST", "/panels").body["id"] for _ in range(5)}
    assert len(ids) == 5


def test_delete_panels_formato(state):
    pid = state.create()
    body = assert_contract(call("DELETE", f"/panels/{pid}"))
    assert body == {"ok": True, "error": ""}
    assert call("GET", "/panels").body["panels"] == []


@pytest.mark.parametrize("path,body", [
    ("/navigate", {"url": "https://c.test"}),
    ("/search", {"q": "termo de teste"}),
    ("/back", {}),
    ("/forward", {}),
    ("/reload", {}),
])
def test_navegacao_formato(path, body, panel):
    resp = assert_contract(call("POST", path, body={**body, "panel": panel}))
    assert resp["ok"] is True
    assert isinstance(resp["url"], str) and resp["url"]
    assert isinstance(resp["title"], str)


def test_navigate_atualiza_url_e_title(panel):
    body = call("POST", "/navigate",
                body={"panel": panel, "url": "https://alvo.test/pagina"}).body
    assert body["url"] == "https://alvo.test/pagina"
    assert body["title"]
    assert call("GET", f"/page?panel={panel}").body["url"] == "https://alvo.test/pagina"


def test_search_monta_query(panel):
    body = call("POST", "/search", body={"panel": panel, "q": "tayama"}).body
    assert "tayama" in body["url"]
    assert body["url"].startswith("http")


def test_back_forward_reload(panel):
    call("POST", "/navigate", body={"panel": panel, "url": "https://um.test"})
    segunda = call("POST", "/navigate",
                   body={"panel": panel, "url": "https://dois.test"}).body["url"]
    assert call("POST", "/back", body={"panel": panel}).body["url"] == "https://um.test"
    assert call("POST", "/forward", body={"panel": panel}).body["url"] == segunda
    assert call("POST", "/reload", body={"panel": panel}).body["url"] == segunda


def test_back_sem_historico_nao_quebra(panel):
    body = call("POST", "/back", body={"panel": panel}).body
    assert_contract(call("POST", "/back", body={"panel": panel}))
    assert body["ok"] is True


def test_forward_no_fim_do_historico_nao_quebra(panel):
    assert_contract(call("POST", "/forward", body={"panel": panel}))


def test_get_page_formato(panel):
    body = assert_contract(call("GET", f"/page?panel={panel}"))
    assert body["ok"] is True
    for chave in ("url", "title", "text", "html"):
        assert isinstance(body[chave], str), f"{chave} ausente ou nao-string"
    assert body["text"]
    assert body["html"]
    assert body["truncated"] is False


def test_get_links_formato(panel):
    body = assert_contract(call("GET", f"/links?panel={panel}"))
    assert isinstance(body["links"], list)
    for link in body["links"]:
        assert set(link) == {"text", "href"}
        assert isinstance(link["text"], str) and isinstance(link["href"], str)


def test_click_formato(panel):
    assert_contract(call("POST", "/click", body={"panel": panel, "selector": "#botao"}))


def test_type_formato_e_clear(panel):
    assert_contract(
        call("POST", "/type",
             body={"panel": panel, "selector": "#campo", "text": "abc"})
    )
    assert_contract(
        call("POST", "/type",
             body={"panel": panel, "selector": "#campo", "text": "xyz",
                   "clear": False})
    )
    assert_contract(
        call("POST", "/type",
             body={"panel": panel, "selector": "#campo", "text": "abc"})
    )


def test_press_formato(panel):
    assert_contract(call("POST", "/press", body={"panel": panel, "key": "Enter"}))


def test_press_tecla_invalida_respond_400(panel):
    resp = call("POST", "/press", body={"panel": panel, "key": "F13!"})
    assert resp.status == 400
    assert_contract(resp)


def _eval(panel, js, **extra):
    return call("POST", "/eval", body={"panel": panel, "js": js, **extra})


def test_eval_desabilitado_por_padrao_responde_403(panel, state):
    """addendum 2: sem browser.allow_eval=true o endpoint nao existe."""
    assert state.allow_eval is False
    resp = _eval(panel, "document.title", confirm=True)
    assert resp.status == 403
    body = assert_contract(resp)
    assert body["error"] == "eval desabilitado"
    assert "result" not in body


def test_eval_desabilitado_403_mesmo_com_confirm_e_sem_confirm(panel):
    for extra in ({}, {"confirm": True}, {"confirm": False}):
        assert _eval(panel, "1+1", **extra).status == 403


def test_eval_ligado_exige_confirm(panel, state):
    """addendum 2: com a flag ligada, so aceita confirm:true."""
    state.allow_eval = True
    resp = _eval(panel, "document.title")
    assert resp.status == 400
    body = assert_contract(resp)
    assert "confirm" in body["error"]


def test_eval_ligado_formato_do_resultado(panel, state):
    state.allow_eval = True
    resp = assert_contract(_eval(panel, "document.title", confirm=True))
    assert resp["ok"] is True
    assert isinstance(resp["result"], str) and resp["result"]


def test_eval_numero_nao_vem_como_string(panel, state):
    state.allow_eval = True
    resp = _eval(panel, "1+1", confirm=True).body
    assert resp["result"] == 2


def test_eval_js_invalido_responde_400(panel, state):
    state.allow_eval = True
    resp = _eval(panel, "sintaxe !!!", confirm=True)
    assert resp.status == 400
    assert_contract(resp)


def test_eval_nao_derruba_o_servidor_quando_desabilitado(panel):
    _eval(panel, "1+1", confirm=True)
    assert call("GET", "/panels").status == 200


def test_screenshot_grava_no_sandbox(panel, state):
    """addendum 3: sempre grava em diretorio proprio do servidor."""
    resp = call("POST", "/screenshot", body={"panel": panel}).body
    assert resp["ok"] is True
    destino = resp["path"]
    assert destino.startswith(state.shots_dir)
    assert os.path.isfile(destino)
    assert open(destino, "rb").read().startswith(b"\x89PNG")


def test_screenshot_ignora_path_do_cliente(panel, tmp_path):
    """addendum 3: path enviado pelo cliente e descartado."""
    falso = tmp_path / "nao-deve-existir.png"
    resp = call("POST", "/screenshot",
                body={"panel": panel, "path": str(falso), "full": True}).body
    assert resp["ok"] is True
    assert resp["path"] != str(falso)
    assert not falso.exists(), "servidor NAO pode gravar no path do cliente"


@pytest.mark.parametrize("malicioso", [
    "../../../../tmp/tayama-evil.png",
    "~/tayama-evil.png",
    "/etc/tayama-evil.png",
    "/etc/passwd",
    "..\\..\\tayama-evil.png",
])
def test_screenshot_path_malicioso_fica_no_sandbox(panel, state, malicioso):
    """addendum 3: '../' , '~/' e '/etc/' nao escapam do sandbox."""
    resp = call("POST", "/screenshot",
                body={"panel": panel, "path": malicioso}).body
    assert resp["ok"] is True
    assert resp["path"].startswith(state.shots_dir)
    assert os.path.isfile(resp["path"])
    assert not os.path.exists("/tmp/tayama-evil.png")
    assert not os.path.exists("/etc/tayama-evil.png")


def test_screenshot_path_malicioso_nao_derruba_servidor(panel):
    assert_contract(call("POST", "/screenshot",
                         body={"panel": panel, "path": "/nao/existe/dir/x.png"}))
    assert call("GET", "/panels").status == 200


# ==========================================================================
# 6. Parametro 'panel' (padrao = ultimo criado/focado)
# ==========================================================================
def test_panel_padrao_e_o_ultimo_criado(state):
    state.create("https://um.test")
    ultimo = state.create("https://dois.test")
    assert call("GET", "/page").body["url"] == "https://dois.test"
    assert ultimo  # sanity


def test_panel_explicito_tem_precedencia(state):
    primeiro = state.create("https://um.test")
    state.create("https://dois.test")
    call("POST", "/navigate", body={"panel": primeiro, "url": "https://alvo.test"})
    assert call("GET", f"/page?panel={primeiro}").body["url"] == "https://alvo.test"
    assert call("GET", "/page").body["url"] == "https://dois.test"


def test_paineis_sao_isolados(state):
    a = state.create("https://a.test")
    b = state.create("https://b.test")
    call("POST", "/navigate", body={"panel": b, "url": "https://b2.test"})
    assert call("GET", f"/page?panel={a}").body["url"] == "https://a.test"
    assert call("GET", f"/page?panel={b}").body["url"] == "https://b2.test"
    assert call("GET", f"/page?panel={a}").body["text"] != call(
        f"GET", f"/page?panel={b}"
    ).body["text"]


def test_painel_padrao_muda_apos_criar_novo(state):
    antigo = state.create("https://antigo.test")
    novo = call("POST", "/panels", body={"url": "https://novo.test"}).body["id"]
    call("POST", "/reload", body={})  # sem panel -> usa o foco (novo)
    assert call("GET", "/page").body["url"] == "https://novo.test"
    assert call("GET", f"/page?panel={antigo}").body["url"] == "https://antigo.test"
    assert novo


def test_fechar_painel_reencontra_foco(state):
    a = state.create("https://a.test")
    b = state.create("https://b.test")
    call("DELETE", f"/panels/{b}")
    resp = call("GET", "/page")  # foco deve cair em 'a', nao dar 404
    assert resp.status == 200
    assert resp.body["url"] == "https://a.test"
    assert a


def test_delete_e_idempotente(state):
    """addendum 6: apagar id inexistente devolve 200, nao 404."""
    pid = state.create()
    assert call("DELETE", f"/panels/{pid}").status == 200
    resp = call("DELETE", f"/panels/{pid}")
    assert resp.status == 200
    assert_contract(resp)
    assert resp.body["ok"] is True
    assert call("DELETE", "/panels/nao-existe-nunca-criado").status == 200


def test_delete_id_com_caractere_invalido_nao_derruba(state):
    assert_contract(call("DELETE", "/panels/../../etc/passwd"))
    assert call("GET", "/panels").status == 200


# ==========================================================================
# 7. Contrato da CLI (opcional — valida a saida de 'tayama browser')
# ==========================================================================
CLI = os.path.join(os.path.dirname(__file__), "..", "bin", "tayama")


@pytest.mark.skipif(not os.path.exists(CLI), reason="bin/tayama ausente")
def test_cli_ajuda_nao_quebra():
    import subprocess

    proc = subprocess.run([CLI, "browser"], capture_output=True, text=True)
    assert proc.returncode != 0 or proc.stdout.strip()
    assert "Traceback" not in proc.stderr


# ==========================================================================
# 8. Addendum — navegacao aceita apenas http:// e https://
# ==========================================================================
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "file:///home/wanbnn/.ssh/id_rsa",
    "data:text/html,<script>alert(1)</script>",
    "javascript:alert(1)",
    "javascript:fetch('http://exfil.test')",
    "ftp://exemplo.test/segredo.txt",
    "about:blank",
    "ABOUT:BLANK",
    "about:config",
])
def test_navigate_recusa_esquemas_proibidos(panel, state, url):
    """addendum 1: so http(s) sao aceitos; o resto da 400 com erro claro."""
    antes = state.panels[panel]["url"]
    resp = call("POST", "/navigate", body={"panel": panel, "url": url})
    assert resp.status == 400, f"{url} deveria dar 400, deu {resp.status}"
    body = assert_contract(resp)
    assert body["error"] and "esquema" in body["error"].lower()
    assert state.panels[panel]["url"] == antes, "painel nao pode ter navegado"


@pytest.mark.parametrize("q", ["file:///etc/passwd", "javascript:alert(1)",
                               "about:blank", "data:text/html,x"])
def test_search_recusa_esquemas_proibidos(panel, q):
    resp = call("POST", "/search", body={"panel": panel, "q": q})
    assert resp.status == 400
    body = assert_contract(resp)
    assert "esquema" in body["error"].lower()


@pytest.mark.parametrize("url", [
    "http://alvo.test/a",
    "https://alvo.test/a",
    "HTTPS://alvo.test/a",
])
def test_navigate_aceita_http_e_https(panel, url):
    body = assert_contract(call("POST", "/navigate", body={"panel": panel, "url": url}))
    assert body["ok"] is True


def test_panels_recusa_url_com_esquema_proibido(state):
    resp = call("POST", "/panels", body={"url": "file:///etc/passwd"})
    assert resp.status == 400
    assert_contract(resp)
    assert call("GET", "/panels").body["panels"] == []


def test_url_malformada_nao_trava_servidor(panel):
    call("POST", "/navigate", body={"panel": panel, "url": "nao-e-url"})
    call("POST", "/navigate", body={"panel": panel, "url": "://"})
    assert call("GET", "/panels").status == 200


# ==========================================================================
# 9. Addendum — limites
# ==========================================================================
def test_content_length_acima_do_limite_responde_413(panel):
    grande = "x" * (MAX_BODY_BYTES + 1024)
    resp = call("POST", "/navigate", body={"panel": panel, "url": "https://a.test",
                                           "padding": grande})
    assert resp.status == 413, f"deveu dar 413, deu {resp.status}"
    assert_contract(resp)


def test_413_nao_derruba_o_servidor(panel):
    call("POST", "/navigate", body={"panel": panel, "url": "https://a.test",
                                    "padding": "x" * (MAX_BODY_BYTES + 1024)})
    assert call("GET", "/panels").status == 200


def test_text_acima_de_100k_responde_400(panel):
    resp = call("POST", "/type", body={"panel": panel, "selector": "#c",
                                       "text": "y" * (MAX_TEXT_CHARS + 1)})
    assert resp.status == 400, f"deveu dar 400, deu {resp.status}"
    body = assert_contract(resp)
    assert str(MAX_TEXT_CHARS) in body["error"]


def test_text_no_limite_exato_e_aceito(panel, state):
    texto = "y" * MAX_TEXT_CHARS
    resp = call("POST", "/type", body={"panel": panel, "selector": "#c",
                                       "text": texto})
    assert resp.status == 200, f"limite exato foi rejeitado: {resp.body}"
    assert len(state.panels[panel]["fields"]["#c"]) == MAX_TEXT_CHARS


def test_js_acima_de_64k_responde_400(panel, state):
    state.allow_eval = True
    resp = _eval(panel, "1;" * (MAX_JS_CHARS // 2 + 10), confirm=True)
    assert resp.status == 400, f"deveu dar 400, deu {resp.status}"
    body = assert_contract(resp)
    assert str(MAX_JS_CHARS) in body["error"]


def test_page_trunca_conteudo_grande(panel, state):
    enorme = "z" * (PAGE_TRUNCATE_CHARS + 5_000)
    state.panels[panel]["html"] = f"<html><body>{enorme}</body></html>"
    state.panels[panel]["text"] = enorme
    body = call("GET", f"/page?panel={panel}").body
    assert body["truncated"] is True
    assert len(body["html"]) <= PAGE_TRUNCATE_CHARS + len("<html><body></body></html>")
    assert len(body["text"]) == PAGE_TRUNCATE_CHARS


def test_page_sem_truncar_quando_conteudo_pequeno(panel):
    assert call("GET", f"/page?panel={panel}").body["truncated"] is False


# ==========================================================================
# 10. Addendum — painel em estado crashed
# ==========================================================================
def test_painel_crashed_aparece_em_panels(panel, state):
    """addendum 6: painel quebrado continua listado, com state=crashed."""
    state.panels[panel]["state"] = "crashed"
    itens = call("GET", "/panels").body["panels"]
    assert len(itens) == 1
    assert itens[0]["id"] == panel
    assert itens[0]["state"] == "crashed"


def test_painel_crashed_ainda_aparece_mesmo_com_outros_ok(state):
    quebrado = state.create("https://quebrado.test")
    state.create("https://saudavel.test")
    state.panels[quebrado]["state"] = "crashed"
    itens = {i["id"]: i for i in call("GET", "/panels").body["panels"]}
    assert itens[quebrado]["state"] == "crashed"
    assert itens[state.focus]["state"] == "ok"


def test_painel_crashed_ainda_e_deletavel(panel, state):
    state.panels[panel]["state"] = "crashed"
    assert call("DELETE", f"/panels/{panel}").status == 200
    assert call("GET", "/panels").body["panels"] == []


def test_navigate_com_panel_desconhecido_responde_404(state):
    """addendum 6: navegação com id desconhecido é 404, nao 500 nem criacao."""
    antes = call("GET", "/panels").body["panels"]
    resp = call("POST", "/navigate",
                body={"panel": "id-fantasma", "url": "https://alvo.test"})
    assert resp.status == 404
    assert_contract(resp)
    assert call("GET", "/panels").body["panels"] == antes, "nao pode criar painel"


# ==========================================================================
# 11. Addendum — bypass de validacao de esquema
# ==========================================================================
@pytest.mark.parametrize("url", ALLOWED_SCHEMES + ("HTTPS://", "HtTpS://"))
def test_esquema_http_aceito_qualquer_caixa(panel, url):
    body = call("POST", "/navigate", body={"panel": panel, "url": url})
    assert body.status == 200, f"{url} deveria ser aceito, deu {body.status}"
    assert body.body["ok"] is True


def test_esquema_ignora_espaco_a_frente_e_frente_e_vras(panel, state):
    """Espaco em volta nao pode transformar a URL em outra coisa."""
    resp = call("POST", "/navigate", body={"panel": panel, "url": "  https://a.test  "})
    assert resp.status == 200
    assert state.panels[panel]["url"] == "https://a.test"


@pytest.mark.parametrize("url", [
    "htt ps://alvo.test",      # esquema quebrado
    "hxxp://alvo.test",        # esquema inexistente
    "://alvo.test",            # sem esquema
    "alvo.test",               # sem esquema
    "/etc/passwd",             # caminho local
])
def test_esquema_invalido_ou_ausente_responde_400(panel, state, url):
    antes = state.panels[panel]["url"]
    resp = call("POST", "/navigate", body={"panel": panel, "url": url})
    assert resp.status == 400, f"{url!r} deveria dar 400, deu {resp.status}"
    assert_contract(resp)
    assert state.panels[panel]["url"] == antes


@pytest.mark.parametrize("prefixo", FORBIDDEN_SCHEMES)
def test_bypass_com_caixa_alta_e_espaco(panel, state, prefixo):
    """'FILE:///...' e ' file:///...' nao podem escapar da validacao."""
    for url in (prefixo + "etc/passwd", prefixo.upper() + "etc/passwd",
                "  " + prefixo + "etc/passwd", "\t" + prefixo + "etc/passwd"):
        resp = call("POST", "/navigate", body={"panel": panel, "url": url})
        assert resp.status == 400, f"{url!r} deveria dar 400, deu {resp.status}"
        assert_contract(resp)
    assert state.panels[panel]["url"] != "file:///etc/passwd"


def test_file_url_nao_pode_vazar_conteudo_de_arquivo(panel, state):
    """Nenhuma resposta pode conter conteudo de arquivo local."""
    resp = call("POST", "/navigate", body={"panel": panel, "url": "file:///etc/passwd"})
    assert resp.status == 400
    bruto = resp.raw.decode()
    assert "root:x:" not in bruto
    assert state.panels[panel]["url"] != "file:///etc/passwd"
