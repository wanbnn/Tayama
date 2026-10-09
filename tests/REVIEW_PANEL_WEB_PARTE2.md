# Revisão PARTE 2 — API HTTP + CLI

Data: 2026-10-08. Status: **implementação ainda não existe — nada a revisar no servidor.**

## 1. Constatação de estado (verificada)

Nenhum código da parte 2 no repositório:

```
$ grep -rln "8777" tayama/ bin/ main.py
(nenhum modulo da parte 2)

$ grep -n "browser" bin/tayama
(sem subcomando browser)
```

Não existe `tayama/webapi.py` (nem `webapi`, `panel_api`, `api`), o `bin/tayama` ainda só aceita
`peers|send|broadcast` (linhas 11-12), e nenhum módulo referencia a porta 8777. O único artefato
da parte 2 no disco é `tests/test_browser_api.py` (867 linhas), que é **suite de contrato**, não
implementação.

`git status`: `tayama/browser.py` e `tayama/agents.py` untracked; `main.py`, `requirements.txt`,
`tayama/ui.py`, `tayama/workspaces.py`, `README.md` modificados. `tayama/browser.py` é a **parte 1**
(`BrowserWindow`, o `QWebEngineView` flutuante) — sem servidor.

---

## 2. Achado crítico na suite que já existe

### 2.1 — CRÍTICO — os 6 itens que o líder pediu para revisar não têm cobertura nenhuma

**Problema concreto:** `tests/test_browser_api.py:446` — `if BASE_URL:  # alvo real: nao sobe o fake`.
Quando `TAYAMA_BASE_URL` não está setado (o default), a suite levanta um servidor **fake** definido no
próprio arquivo (`make_handler(state)`, linha 420, `ThreadingHTTPServer(("127.0.0.1", 0), ...)`) e testa
*aquele* servidor. Ou seja: os ~50 testes rodam contra o contrato que o developer **escreveu como mock**.

Consequência concreta: os testes que parecem cobrir segurança — `test_sem_token_responde_401` (481),
`test_token_invalido_responde_401` (488), `test_sem_token_401_em_todas_as_rotas` (504) —
validam a *implementação do fake*, não a do Tayama. Se o servidor real comparar o token com `==`,
não usar `compare_digest`, ouponential-o depois do roteamento, a suite continua 100% verde.
`test_delete_nao_e_idempotente` (850), `test_paineis_sao_isolados` (820) e todos os de
`screenshot` (768-804) são do mesmo tipo.

**Cenário de falha concreto:** o developer sobe o servidor real com a allowlist de scheme ausente e
`/eval` liberado; `python3 -m pytest tests/test_browser_api.py` passa inteiro e o time conclude que a
parte 2 está validada. Não está — só o mock foi validado.

**Sugestão:** manter a suite (ela é boa para o formato das respostas) mas **não considerá-la
validação da parte 2** até existir um segundo alvo. Concretamente:
- fazer `-k real` (linha 12) de fato funcionar: currently `grep -n "def test_" | grep -i real`
  retorna **zero** testes — o caminho documentado para validar a implementação real seleciona nada,
  é um no-op silencioso. Ou os testes ganham nomes `test_real_*`, ou o modo real passa a ser uma
  flag `-m real` com `pytestmark`, e o CI falhar se `--real` rodar 0 testes.
- escrever, contra o alvo real, pelo menos: `POST /navigate {"url":"file:///etc/passwd"}` → 400;
  `"url":"  https://x"` com espaço à frente; `"url":"FILE:///etc/passwd"`; `POST /eval` com a flag
  desligada → 400; `POST /screenshot {"path":"../../x"}` → não escreve fora do sandbox;
  `POST /screenshot {"path":"/home/<user>/.bashrc"}` → não escreve;
  header ausente em **todas** as rotas → 401; `Content-Length: 10MB` → 413.

### 2.2 — ALTO — `ALLOWED_SCHEMES` declarado e nunca usado (nem no fake)

**Problema concreto:** `tests/test_browser_api.py:56-57`
```python
ALLOWED_SCHEMES = ("http://", "https://")
FORBIDDEN_SCHEMES = ("file:", "data:", "javascript:", "ftp:", "about:")
```
`grep -n "ALLOWED_SCHEMES"` retorna **apenas a linha 56** — a constante é declarada e nunca lida.
Nenhum teste faz `/navigate` com scheme proibido.

**Cenário de falha concreto:** o developer implementa a allowlist no servidor real believing que a
suite a cobre. Ela não cobre. E se ele copiar o padrão de prefixo `startswith(ALLOWED_SCHEMES)` do
fake para o servidor real, o item **1.1** da revisão da parte 1 fica sem proteção.

**Sugestão:** antes de existir o servidor real, adicionar à suite os casos de bypass com o *nome*
declarando que rodam contra os dois alvos:
- `"HTTP://x"` (maiúsculas) — `startswith("http://")` é `False`: rejeitar, **não** cair no
  fallback "não parece URL → trata como busca", que em `browser.py:139-140` transformaria em
  `QUrl.fromUserInput` e devolveria uma URL搜索引擎.
- `" https://x"` (espaço à frente) — precisa de `.strip()` **antes** do check; sem strip, cai no
  mesmo fallback.
- `"https:/\\/"` e `"https:\\\\evil"` — normalizar com `QUrl` e re-checar `scheme()` **depois** de
  normalizar, não só a string crua.
- `"file:///etc/passwd"`, `"FILE:///etc/passwd"`, `"data:text/html,<script>…"` → 400.
- redirect: um `http://` que responde `302 Location: file:///…` — exigir `requestInterceptor`
  checando scheme em **cada** `NavigationRequest`, não só no `setUrl` inicial.

### 2.3 — MÉDIO — o fake é mais permissivo que o servidor real deber允许 ser, e isso mascara

O fake define `MAX_BODY_BYTES`, `MAX_TEXT_CHARS`, `MAX_JS_CHARS`, `PAGE_TRUNCATE_CHARS` (linhas
52-55) mas o grep mostra que o comportamento correspondente só é testado contra o fake. Os limites
do item **6** (tamanho de corpo, `text`, `js`, truncamento de `/page`) estão **declarados e não
verificados** contra a implementação real — mesmo problema da 2.1, isolado nos limites de tamanho.

---

## 3. Não avaliado (por não existir)

Itens 2 (Job + `QueuedConnection` + `threading.Event` com timeout), 3 (`eval_js` com callback +
watchdog, `QEventLoop` aninhado), 4 (`compare_digest`, chmod, ordem da checagem do header),
5 (sandbox de `/screenshot`, `open(...,'xb')`) — **sem código para ler**. Serão revisados quando o
developer avisar que fechou a parte 2.

## 4. Reavaliação da parte 1 (arquivo já existente, leitura rápida)

Não era o escopo pedido, mas `tayama/browser.py` tem dois pontos que a parte 2 vai herdar:

- `browser.py:138` — `_normalize` aceita explicitamente `t.startswith("file:")` e constrói
  `QUrl(t)`. A barra de endereço é operada pelo **usuário**, então é navegação local legítima; mas
  qualquer função de allowlist da parte 2 **não pode** reaproveitar `_normalize`: `_normalize`
  aceita `file:`, a API não. Separar as duas.
- `browser.py:84-86` — `QWebEngineView()` sem `QWebEnginePage`/`QWebEngineProfile` próprio usa o
  perfil default **off-the-record** do Qt (não persiste em disco por padrão no Qt6), o que é
  aceitável. Mas o perfil default é **compartilhado entre todos os painéis**, então cookies de um
  painel são visíveis no `document.cookie` de outro. Se o addendum do líder pede perfil por painel,
  isso ainda **não** foi implementado aqui.