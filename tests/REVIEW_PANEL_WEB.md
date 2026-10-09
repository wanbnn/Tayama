# Revisão de diseño — Painel Web (QWebEngineView + API HTTP)

Alvo: `main.py`, `tayama/ui.py`, `tayama/bridge.py`, `tayama/config.py`, `bin/tayama`.
Escopo: riscos antes de virar código (parte 2 = API HTTP). Não questiona decisões já tomadas.

---

## 0. Achados que já existem hoje e serão ampliados

| # | Problema | Sugestão |
|---|---|---|
| 0.1 | `config.ensure()` cria `~/.tayama` e `config.json` com umask padrão (0o644). `config.json` guarda comandos de agentes; `browser.json` vai guardar o token que controla o navegador do usuário. | `os.chmod(DIR, 0o700)` e `os.chmod(CFG, 0o600)` no `ensure()`; idem para `browser.json`. Criar o arquivo com `os.open(..., O_CREAT\|O_WRONLY, 0o600)` em vez de `write_text`, que respeita umask e não corrige permissão existente. |
| 0.2 | `config.SOCK` = `/tmp/tayama-<uid>.sock`. Qualquer processo do mesmo usuário fala com o bridge — ok. Mas o bridge aceita `from` = qualquer id conhecido; com `broadcast` reach. | Ao adicionar a API HTTP, o token precisa ser o mesmo nível de garantia; não "confie no loopback". |
| 0.3 | `main.py` cria `QApplication` em `__main__`. `QtWebEngineWidgets` **deve** ser importado antes da criação do `QApplication` (restrição do QtWebEngine no PyQt6 — senão crash em `QApplication` já existente / crash no import do QML). | Em `main.py`, colocar `from PyQt6.QtWebEngineWidgets import QWebEngineView` no topo do arquivo, antes de `QApplication(sys.argv)`. Adicionar `PyQt6-WebEngine` ao `requirements.txt` (hoje só há `PyQt6`, `pyte`, `qtawesome` — a dependência não existe e o developer vai bater nisso primeiro). |

---

## 1. Segurança

### 1.1 — CRÍTICO — `/navigate` sem allowlist de scheme = exfiltração de disco
**Problema:** o contrato diz `POST /navigate {url}` sem restriction. `QWebEngineView.setUrl("file:///etc/passwd")` seguido de `GET /page` devolve o conteúdo em `text`/`html`. Qualquer agente com o token lê o disco inteiro do usuário (`~/.ssh/id_rsa`, `~/.tayama/config.json`, histórico do browser de outros perfis). O mesmo vale para `file://` no `/search`.
**Sugestão:** allowlist estrita de scheme **no servidor**, antes de tocar na view:
```python
ALLOWED = ("http://", "https://")
u = str(url).strip()
if not u.startswith(ALLOWED):
    # permite só o explicitamente pedido pelo usuário
    if u in ("about:blank", "about:home"):
        ...
    return {"ok": False, "error": "scheme não permitido"}
```
Não confiar em `QUrl.scheme()` só (aceita `file:`, `javascript:`, `data:`); faça checagem de string + normalização antes.

### 1.2 — CRÍTICO — `/eval` = RCE no perfil do Chromium
**Problema:** `runJavaScript(js)` roda no renderer com acesso total ao contexto da página: `document.cookie`, `localStorage`, `sessionStorage`, `indexedDB`, e o JS de qualquer site onde o usuário esteja logado. Combinado com 1.1, é um shell completo. Mesmo sem `file://`, um agente pode ler a sessão do Gmail/banco do usuário que abriu o painel.
**Sugestão (três camadas):**
1. Perfil **off-the-record** para os painéis: `QWebEngineProfile()` não-persistente criado por painel + `QWebEnginePage` usando esse perfil. Assim os painéis de agente **não compartilham cookies com o browser real do usuário**, e nada persiste em disco.
2. `/eval` desligado por padrão: flag em `config.py` (`browser.allow_eval: false`) habilitada explicitamente. Quando ligado, exigir `{"js": ..., "confirm": true}` ou um sub-token de uso único.
3. Whatever o exposto: bloquear `QWebEngineSettings.JavascriptCanOpenWindows` e `JavascriptDialogs`, e limpar cookies/sessão ao fechar o painel (`page.profile().clearHttpCache()` / `cookieStore().deleteAllCookies()`).

### 1.3 — ALTO — token: comparação e armazenamento
**Problema:** comparação com `==` vaza por timing; token curto/bruteforcável via loopback.
**Sugestão:**
- Gerar com `secrets.token_urlsafe(32)` (não `random`, não uuid4 truncado).
- Comparar com `hmac.compare_digest(token, esperado)` sobre bytes de tamanho fixo.
- Checar o header **antes** de parsear corpo/rota, em todas as rotas inclusive `GET /panels`; e responder `401` sem detail.
- Não logar o token em log do Qt nem em `print`.

### 1.4 — ALTO — path traversal em `/screenshot {path}`
**Problema:** o agente escolhe o caminho de escrita com os privilégios do processo do usuário. `path: "~/.bashrc"` sobrescreve arquivo de shell; `../../../etc/x` idem.
**Sugestão:**
- Ignorar `path` do cliente; sempre gravar dentro de um diretório sandbox criado pelo Tayama (`~/.tayama/shots/`), nome gerado pelo servidor (`<panel>-<timestamp>.png`). Se o cliente quiser escolher, aceitar **só basename** e ainda assim prefixar o sandbox e rejeitar `..`, `/`, `\`, byte nulo.
- Se a API precisar de path arbitrário mesmo assim, exigir flag explícita `allow_external_screenshot` na config.
- Criar o arquivo com `open(path, "xb")` (O_EXCL) para não seguir symlink pré-existente no sandbox.

### 1.5 — MÉDIO — `/eval` e `/type` como DoS + vector de prompt-injection
**Problema:** `POST /type` sem limite de `text` = alocar memória arbitrária no renderer; `POST /eval` com `while(true){}` trava o renderer.
**Sugestão:** limite de corpo no servidor (`Content-Length` > N → 413; N = 1 MB default), limite de `text` (ex. 100 k chars) e de `js` (ex. 64 k), e `GET /page` com **truncamento explícito** (`{"truncated": true, "html": ...[:1_000_000]}`) em vez de despejar a página inteira.

---

## 2. Threading / marshaling (o ponto que mais vai doer)

**Regra:** `QWebEngineView` / `QWebEnginePage` só podem ser tocados na thread do Qt que os criou (a main). Nada da API HTTP pode chamá-los direto.

**Padrão correto em PyQt6:** servidor HTTP em `threading` puro (não Qt) dentro de uma `QThread`; a thread só **enfileira** pedidos e **bloqueia o próprio resultado**; um `pyqtSignal(object)`发出 para a main thread executar o trabalho no Qt; a main thread publica o resultado de volta.

```python
# tayama/webapi.py
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QThread

class PanelApi(QObject):
    call = pyqtSignal(object)          # emitido da thread HTTP, recebido na main thread

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller   # vive na main thread
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @pyqtSlot(object)
    def _run(self, job):               # roda na main thread
        job.result = job.fn()          # único lugar que toca QWebEngineView
        job.done.set()                 # destrava a thread HTTP

    def submit(self, fn):              # chamado da thread HTTP
        job = Job(fn)
        self.call.emit(job)
        if not job.done.wait(20_000):  # SEMPRE com timeout; nunca wait() infinito
            job.timed_out = True
        return job
```

Pontos obrigatórios:
- `Qt.QueuedConnection` é o default quando emissor e receptor estão em threads diferentes, mas **declare explicitamente** — evita herança sutil se alguém reconectar o sinal.
- O `Job` precisa de `threading.Event`; resultado e erro em atributos, nunca em `QMutex` com `QMutexLocker` przez o limite de thread.
- **Nunca** usar `QMetaObject.invokeMethod(..., Qt.BlockingQueuedConnection)` para algo que possa reentrar no servidor, e **nunca** `QApplication.processEvents()` no handler HTTP.
- `runJavaScript()` é assíncrono (callback): o handler HTTP tem que esperar o callback do Chromium sem segurar a main thread. O padrão é `asyncio`/loop de eventos no Chromium (`QWebEngineScript` é outra saída) ou um `QEventLoop` local **apenas** se o loop rodando for um `QEventLoop` aninhado na main thread — nunca em `app.exec()`. Recomendo: helper `eval_js(page, js, timeout)` que registra um `id` e resolve via callback, com `QTimer.singleShot(timeout, ...)` de watchdog.
- O servidor HTTP (`http.server.ThreadingHTTPServer` ou `socketserver`) vive na `QThread`; `server.serve_forever()` em `run()`, `shutdown()` + `wait()` no `__del__`/fechamento do app para não deixar thread órfã no `sys.exit`.

---

## 3. Robustez

### 3.1 — JS infinito trava o painel, e o request HTTP fica pendurado
**Problema:** `while(true){}` no renderer congela a página; `loadFinished` nunca dispara e um agente que fique em polling de `/page` consome o servidor. No Chromium o renderer é outro processo, então **o app não morre**, mas o painel fica morto e requisições ficam penduradas.
**Sugestão:**
- Watchdog em toda operação assíncrona: `QTimer.singleShot(10_000, watchdog)` que chama `view.stop()` e marca o painel como `crashed`. Estado `crashed` no `GET /panels` para o agente perceber.
- Botão/`POST /kill` que recria o painel (`QWebEngineView` novo + perfil novo) — recriar é mais barato e mais seguro que tentar recuperar o renderer.
- Timeout no `submit()` acima (20 s) para que a thread HTTP nunca fique presa.

### 3.2 — `data:` URL
`data:text/html,<script>...</script>` dá ao agente origem opaca, phish visual no painel do usuário e um jeito de exibir UI falsa. Tratar no mesmo lugar do allowlist de 1.1 (negar `data:` exceto se explicitamente necessário — não é).

### 3.3 — `about:blank` como destino padrão
Navegar o painel para `about:blank` é a forma limpa de "resetar" um painel possivelmente comprometido; mover isso para um endpoint explícito (`POST /navigate {"url":"about:blank"}` já cobre, desde que a allowlist permita).

### 3.4 — ciclo de vida e estado
`canvas.windows` é keyed por id e `agents.save_one(win)` persiste. Painéis devem: herdar `FloatingWindow`/sinal `changed` para aparecerem na sidebar e serem gravados; e ser removidos com `remove_window` para não deixar proxy órfão no `QGraphicsScene`. `DELETE /panels/<id>` precisa ser idempotente (200 mesmo se já removido) — agente vai repetir comandos.

### 3.5 — CLI
`bin/tayama` hoje faz `json.loads(os.environ["TAYAMA_SOCK"])` e falha se ausente. O subcomando `browser` precisa ler `~/.tayama/browser.json` (token **e** porta) com fallback de `TAYAMA_BROWSER_PORT`, e sair com mensagem clara se o Tayama não estiver rodando (mesmo estilo de "Tayama indisponível"). Não imprimir o token em caso de erro.

---

## Prioridade sugerida para a parte 2

1. Allowlist de scheme + perfil off-the-record (1.1, 1.2) — fecha exfiltração de disco e de sessão.
2. `/eval` atrás de flag desligada por padrão (1.2.2).
3. Sandbox de `/screenshot`, ignorando `path` do cliente (1.4).
4. `Job` + `QueuedConnection` + timeout em toda chamada Qt (seção 2) — é o que define se a parte 2 funciona.
5. Token com `compare_digest` + `chmod 600` no `ensure()` (1.3, 0.1).
6. Watchdog de 10 s + recriação de painel (3.1).