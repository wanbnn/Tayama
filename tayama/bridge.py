"""Servidor local: permite que os agentes usem `tayama` para falar com terminais
conectados e para controlar os painéis web.

Duas responsabilidades com contratos diferentes:

  * `peers` / `send` / `broadcast` — respondem na hora, como sempre.
  * `panel ...` — precisa falar com o Chromium, que responde por callback. Logo
    a resposta é ASSÍNCRONA: o request fica em voo até o callback chegar.

Por isso `_Conn`: um request que segura o próprio socket e tem `reply()`
idempotente. Sem a guarda `done`, o callback do Chromium e o watchdog são dois
slots da main thread que podem disparar em qualquer ordem, e o socket receberia
duas respostas — o `bin/tayama` lê até EOF e faz `json.loads` do buffer inteiro,
virando `JSONDecodeError` no lado do agente.
"""
import json
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtNetwork import QLocalServer
from . import config, panelapi


class _Conn:
    """Um request em voo: segura o socket porque a resposta pode ser assíncrona."""

    # Sem `timer`: o watchdog vive em `_roda`, dono do seu QTimer, e para na
    # própria closure. Guardá-lo aqui seria um segundo dono sem quem o start().
    __slots__ = ("sock", "buf", "done")

    def __init__(self, sock):
        self.sock, self.buf, self.done = sock, bytearray(), False

    def reply(self, resp):
        """Escreve a resposta uma única vez e fecha. Idempotente de propósito."""
        if self.done:
            return
        self.done = True
        s = self.sock
        try:
            s.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
            s.flush()
            s.disconnectFromServer()
        except (RuntimeError, OSError):
            pass          # cliente já desconectou; nada a fazer
        finally:
            # O deleteLater tem que acontecer aqui, exatamente uma vez. Sem ele
            # o socket fica filho do QLocalServer pelo resto da sessão — hoje
            # isso já vaza, e retê-lo até o callback pioraria.
            s.deleteLater()


class Bridge(QObject):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._live = set()       # requests em voo; manter a referência é OBRIGATÓRIO
        QLocalServer.removeServer(config.SOCK)
        self.server = QLocalServer(self)
        self.server.listen(config.SOCK)
        self.server.newConnection.connect(self._new)

    def _new(self):
        sock = self.server.nextPendingConnection()
        c = _Conn(sock)
        self._live.add(c)       # sem esta referência o GC derruba o socket no meio

        def ready():
            c.buf.extend(bytes(sock.readAll()))
            if b"\n" not in c.buf:
                return
            if len(c.buf) > panelapi.MAX_BODY:
                c.reply({"ok": False, "error": "requisicao grande demais"}); return
            try:
                req = json.loads(bytes(c.buf).decode())
            except (ValueError, UnicodeDecodeError) as e:
                c.reply({"ok": False, "error": f"JSON invalido: {e}"}); return
            try:
                resp = self.handle(req, reply=c.reply)
            except Exception as e:                      # nunca derruba o Bridge
                resp = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            if resp is not None:
                c.reply(resp)   # None = a resposta sai mais tarde, do callback

        sock.readyRead.connect(ready)
        # Cliente que desiste (Ctrl+C) antes da resposta: solta a referência.
        sock.disconnected.connect(lambda: self._live.discard(c))

    # ------------------------------------------------------------------
    def handle(self, req, reply):
        """Despacha um request. Devolve a resposta, ou None se ela sai depois.

        `reply` é a saída assíncrona: quem precisa do Chromium devolve None e
        chama `reply(env)` do callback.
        """
        cmd = req.get("cmd")

        # A origem é validada ANTES do dispatch, e só em canvas.windows de
        # propósito: painéis vivem em canvas.panels e não têm TAYAMA_ID, então
        # um `panel` disparado de dentro de um painel é recusado. Se esta checagem
        # viesse depois, um cmd desconhecido responderia erro genérico e um
        # painel passaria a forjar `from` livremente.
        src = self.canvas.windows.get(req.get("from"))
        if not src:
            return {"ok": False, "error": "terminal de origem desconhecido (rode dentro do Tayama)"}

        if cmd == "panel":
            self._painel(req, src, reply)     # assíncrono: sempre devolve None
            return None

        peers = self.canvas.peers_of(src)
        panels = self.canvas.panels
        info = [{"id": p.id, "name": p.name, "role": p.role_name,
                 "kind": "painel" if p in panels.values() else "terminal",
                 "agent": p.agent["name"]} for p in peers]
        if cmd == "peers":
            return {"ok": True, "peers": info}
        if cmd == "broadcast":
            targets = peers
        elif cmd == "send":
            t = req.get("target", "").lower()
            targets = [p for p in peers if t in (p.name.lower(), p.role_name.lower())]
        else:
            return {"ok": False, "error": f"comando invalido: {cmd}"}
        if not targets:
            return {"ok": False, "error": "nenhum destino conectado corresponde", "peers": info}
        for p in targets:
            p.receive(src, req.get("text", ""))
        return {"ok": True, "sent_to": [p.name for p in targets]}

    # --- painéis --------------------------------------------------------
    def _painel(self, req, src, reply):
        """Roteia uma op de painel. Pode responder na hora (reply) ou não."""
        op = req.get("op")

        # `list` não depende do Chromium: responde direto.
        if op == "list":
            ligados = {p.id for p in self.canvas.peers_of(src)}
            rows = []
            for p in self.canvas.panels.values():
                mesmo = panelapi.mesmo_workspace(p, src)
                rows.append({"id": p.id, "name": p.name,
                             "url": p.url.text().strip(), "title": p.title.text(),
                             "state": p.state, "linked": p.id in ligados,
                             "accessible": mesmo})
            return reply({"ok": True, "panels": rows})

        if op == "open":
            url = req.get("url") or "about:blank"
            if url != "about:blank":
                ok, err = panelapi.check_url(url)
                if not ok:
                    return reply({"ok": False, "error": err})
                url = ok
            win = self.canvas.add_panel(url, name=req.get("name"))
            return reply({"ok": True, "id": win.id, "name": win.name})

        # Daqui pra frente toda op tem alvo.
        panel, err = panelapi.resolve_panel(self.canvas, src, req.get("panel"))
        if panel is None:
            return reply({"ok": False, "error": err})
        if not panelapi.mesmo_workspace(panel, src):
            return reply({"ok": False,
                          "error": "esse painel esta em outro workspace"})

        # Navegação: síncrona, não passa pelo Chromium.
        if op == "navigate":
            return reply(panel.navigate_api(req.get("url", "")))
        if op == "back":
            panel.go_back(); return reply({"ok": True, "url": panel.url.text().strip()})
        if op == "forward":
            panel.go_forward(); return reply({"ok": True, "url": panel.url.text().strip()})
        if op == "reload":
            panel.go_reload(); return reply({"ok": True, "url": panel.url.text().strip()})
        if op == "close":
            # Idempotente: o agente repete comando sem querer.
            nome = panel.name
            self.canvas.remove_panel(panel)
            return reply({"ok": True, "closed": nome})

        # Daqui pra frente é JS no renderer — sempre assíncrono.
        if op == "eval":
            js = req.get("js", "")
            if not isinstance(js, str) or not js.strip():
                return reply({"ok": False, "error": "eval exige <js>"})
            if len(js) > panelapi.MAX_JS:
                return reply({"ok": False, "error": f"js excede {panelapi.MAX_JS} caracteres"})
            return self._roda(panel, panelapi.envelope(js), reply)

        if op == "click":
            sel = req.get("selector", "")
            if not sel:
                return reply({"ok": False, "error": "click exige <seletor>"})
            return self._roda(panel, panelapi.js_click(sel), reply)

        if op == "type":
            sel, text = req.get("selector", ""), req.get("text", "")
            if not sel:
                return reply({"ok": False, "error": "type exige <seletor>"})
            if not isinstance(text, str):
                return reply({"ok": False, "error": "type exige <texto>"})
            if len(text) > panelapi.MAX_TEXT:
                return reply({"ok": False, "error": f"texto excede {panelapi.MAX_TEXT} caracteres"})
            return self._roda(panel, panelapi.js_type(sel, text, req.get("append", False)), reply)

        if op == "press":
            js, err = panelapi.js_press(req.get("key", ""), req.get("selector"))
            if err:
                return reply({"ok": False, "error": err})
            return self._roda(panel, js, reply)

        if op == "page":
            return self._roda(panel, panelapi.js_page(req.get("full", False)), reply)
        if op == "links":
            return self._roda(panel, panelapi.js_links(req.get("selector")), reply)

        reply({"ok": False, "error": f"op desconhecida: {op}"})

    def _roda(self, panel, js, reply):
        """Manda JS ao painel e devolve o dict cru do gadget como resposta.

        O painel pode ter sido fechado entre o despacho e a resposta, então o
        callback confere se ainda está no canvas antes de tocar na page.

        O watchdog do `run_js` protege o callback; este protege o SOCKET, para o
        caso de o painel morrer de um jeito que nem o callback nem o timer dele
        ainda disparem. `reply` é idempotente, então quem chegar primeiro vence
        e o outro sai sem escrever nada.
        """
        guarda = QTimer()
        guarda.setSingleShot(True)
        guarda.timeout.connect(
            lambda: reply({"ok": False, "error": "painel nao respondeu a tempo"}))
        guarda.start(panelapi.JS_TIMEOUT_MS + 5000)

        def _responde(resp):
            guarda.stop()
            reply(resp)

        def _voltou(env):
            if panel not in self.canvas.panels.values():
                _responde({"ok": False, "error": "painel fechou antes de responder"})
                return
            # `js_links` devolve um ARRAY puro, não um dict: `{**env}` num list
            # levanta TypeError. E uma exceção de Python dentro de um callback do
            # Qt é qFatal no PyQt — ABORT, sem traceback, morrendo o app inteiro
            # (medido: `tayama panel links` derrubava o Tayama). O `links` fica
            # em `value`, que é o que o formatador do CLI já sabe ler.
            if isinstance(env, dict):
                _responde({"ok": True, **env})
            else:
                _responde({"ok": True, "value": env})

        try:
            panel.run_js(js, _voltou)
        except RuntimeError as e:
            _responde({"ok": False, "error": f"painel indisponivel: {e}"})