# Validação manual — Painel Web do Tayama

Status: **pendente** (recurso em construção pelo developer).
Preenchimento: rode os passos, preencha a coluna **Obtido** e marque ✅/❌.
Data de execução: ____/____/______  Testador: ____________  Versão/commit: ____________

Ambiente usado neste roteiro:
- Base da API: `http://127.0.0.1:8777`
- Header obrigatório: `X-Tayama-Token: <token>`
- Toda resposta é JSON no formato `{"ok": bool, "error": str}`
- CLI auxiliar: `tayama browser <comando> [args]`

Convenções:
- [ ] = pendente  ✅ = ok  ❌ = falha (abrir bug com passos/esperado/obtido)
- Quando um passo falhar, **não pule** os seguintes: registre e siga, porque a dependência entre eles é o que isola a causa.
- Use sempre a mesma página de teste (ex.: `https://example.com`) para não introduzir variação de rede nos resultados.

---

## 0. Pré-requisitos

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 0.1 | Painel web sobe (`tayama browser ...` ou serviço equivalente) e responde | Servidor no ar em 127.0.0.1:8777 | |
| 0.2 | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8777/panels` sem header | `401` | |
| 0.3 | `curl -s -H "X-Tayama-Token: <token>" http://127.0.0.1:8777/panels` | `200` e corpo `{"ok": true, "error": "", "panels": [...]}` | |
| 0.4 | Token errado (`X-Tayama-Token: invalido`) | `401`, corpo `{"ok": false, "error": "<alguma mensagem>"}` | |
| 0.5 | Nenhum painel aberto no início | `GET /panels` devolve lista vazia `[]` | |

---

## 1. Criar painel

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 1.1 | `POST /panels` sem `url` no corpo | `200`, `{"ok": true, "error": "", "id": "<id>"}` com id não vazio | |
| 1.2 | `GET /panels` imediatamente depois | o novo painel aparece em `panels` | |
| 1.3 | Verifique o item em `panels` | exatamente as chaves `{id, title, url}` (sem chave extra obligatoria) | |
| 1.4 | `POST /panels {"url": "https://example.com"}` | `200`, novo `id`, e após listar `url` == `https://example.com` | |
| 1.5 | Crie dois painéis seguidos sem informar `panel` em nenhum comando | ambos usam o **último criado/focado** como padrão | |
| 1.6 | Crie 3 painéis, deixe um painel "antigo" sem tocar nele e chame `/back` sem `panel` | opera no painel foco, **não** no mais antigo | |
| 1.7 | Ids são únicos entre painéis | dois `POST /panels` nunca devolvem o mesmo `id` | |
| 1.8 | `tayama browser open https://example.com` (equivalente CLI) | mesmo efeito do `POST /panels {"url": ...}` | |

---

## 2. Navegação

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 2.1 | `POST /navigate {"panel": "<id>", "url": "https://example.com"}` | `200`, `{"ok": true, "url": "...", "title": "..."}` com `url` e `title` preenchidos | |
| 2.2 | Navegue para uma segunda URL | `url` da resposta reflete a **nova** URL | |
| 2.3 | `POST /navigate` sem o campo `url` | `400`, `{"ok": false, "error": "<motivo>"}` (não 500, não crash) | |
| 2.4 | `POST /navigate` com url inválida (`"nao-e-url"`) | erro controlado: `400` ou `ok:false`; **nunca** exceção/traceback no log | |
| 2.5 | `POST /navigate {"panel": "<id inexistente>", "url": ...}` | `404`, `{"ok": false, "error": "..."}` | |

---

## 3. Voltar / Avançar / Recarregar

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 3.1 | Após 2.1–2.2, `POST /back {"panel": "<id>"}` | `200`, `url` volta para a **primeira** URL | |
| 3.2 | `POST /forward` em seguida | `url` volta para a **segunda** URL | |
| 3.3 | `POST /back` duas vezes sem histórico suficiente | não quebra: ou `ok:true` na mesma URL, ou erro controlado; nunca `500` | |
| 3.4 | `POST /forward` na última posição do histórico | mesmo comportamento de 3.3 (sem crash) | |
| 3.5 | `POST /reload {"panel": "<id>"}` | `200`, `url` igual à anterior, página recarregada | |
| 3.6 | Recarregue uma página com campo de texto preenchido (se aplicável) | comportamento definido e documentado (não obrigatoriamente preservado) | |
| 3.7 | `/back`, `/forward`, `/reload` **sem** `panel` | operam no painel foco | |

---

## 4. Recarregar / estado após reload

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 4.1 | Navegue, faça `/back`, depois `/reload` | sem erro; `url` corresponde à página atual do histórico | |
| 4.2 | Após reload, `GET /page` | `html` e `text` continuam disponíveis (painel não "morreu") | |
| 4.3 | Recarregue 5x seguidas no mesmo painel | sem vazamento: `GET /panels` continua listando o painel | |

---

## 5. Digitar URL / barra de endereço

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 5.1 | Digitar uma URL completa na barra e confirmar | página carregada; `GET /page` mostra o conteúdo novo | |
| 5.2 | Digitar URL sem esquema (`example.com`) | ou carrega `https://example.com`, ou erro visível na barra — **nunca** painel em branco silencioso | |
| 5.3 | Digitação incremental (caracter a caracter) no campo da barra | sem travar a UI; sem erro 4xx/5xx por tecla | |
| 5.4 | Apagar o campo da barra (string vazia) e confirmar | erro controlado ou nenhum envio; sem crash | |
| 5.5 | Autocomplete/lista de sugestões (se existir) | clicável e leva à URL completa | |

---

## 6. Drag (arrastar)

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 6.1 | Arrastar um elemento pela página (ex.: um `<h1>`) | elemento se move; sem erro no log | |
| 6.2 | Após o drag, `POST /eval` lendo a posição do elemento | posição reflete o novo lugar | |
| 6.3 | Drag com destino fora da área do painel | elemento volta/limpa sem travar o painel | |
| 6.4 | Drag de um elemento inexistente (via selector inválido) | erro controlado (`ok:false`), sem `500` | |
| 6.5 | Arrastar o painel/janela do navegador (se houver) entre monotonicidades/áreas | sem perda de sessão | |

---

## 7. Resize (redimensionar)

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 7.1 | Redimensionar a janela para ~50% da largura | layout reflita a largura; sem elementos cortado | |
| 7.2 | Redimensionar novamente (vários tamanhos em sequência) | sem vazamento de memória perceptível, sem crash | |
| 7.3 | Após resize, `POST /screenshot` | imagem com as dimensões novas | |
| 7.4 | Altura mínima / janela muito pequena | painel continua utilizável ou exibe aviso | |

---

## 8. Zoom do canvas com Ctrl+scroll

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 8.1 | Ctrl+scroll **para cima** sobre a página | zoom aumenta (ex.: 100% → 110%) | |
| 8.2 | Ctrl+scroll **para baixo** | zoom diminui | |
| 8.3 | Ctrl+scroll repetidamente até o limite superior | zoom **satura** (ex.: máx. 200%) e não trava nem estoura | |
| 8.4 | Ctrl+scroll até o limite inferior | satura em 100%/minimo sem valor negativo ou NaN | |
| 8.5 | Scroll **sem** Ctrl na página | rolagem normal do conteúdo; zoom **não** muda | |
| 8.6 | Zoom aplicado é resetado ao recarregar ou muda de URL? | comportamento definido e **consistente** (anotar qual dos dois) | |
| 8.7 | Ctrl+scroll com 2 painéis lado a lado | zoom afeta **só** o painel sob o cursor | |
| 8.8 | Zoom + `POST /screenshot` | captura reflete o zoom aplicado | |

---

## 9. Múltiplos painéis simultâneos

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 9.1 | Abrir 3 painéis, cada um em URL diferente | `GET /panels` lista 3 itens com urls distintas | |
| 9.2 | Navegar apenas no painel B | painéis A e C permanecem nas URLs anteriores | |
| 9.3 | `GET /page` para cada painel, em sequência | cada painel devolve o conteúdo **seu** (sem mistura de conteúdo) | |
| 9.4 | Fechar o painel B (`DELETE /panels/<id>`) | `{"ok": true, "error": ""}`; `GET /panels` lista 2 | |
| 9.5 | Após fechar B, chamar `/page` com `panel = <id do B>` | `404` com `ok:false` | |
| 9.6 | Após fechar B, chamar `/page` **sem** `panel` | painel foco passa a ser um dos restantes (A ou C); sem `404` inesperado | |
| 9.7 | Fechar todos os painéis, um a um | último `DELETE` também devolve `ok:true`; `GET /panels` = `[]` | |
| 9.8 | Com 0 painéis, chamar `/page` sem `panel` | erro controlado (`404` com `ok:false`); sem crash do processo | |
| 9.9 | `DELETE /panels/<id>` com id inexistente | `404` com `ok:false` (idempotência **não** esperada) | |
| 9.10 | Reabrir painéis até 5+ simultâneos | todos listados e operáveis; sem interferência | |
| 9.11 | Sincronização de foco: `tayama browser ...` sem `panel` após criar um novo painel | opera no novo painel | |

---

## 10. Foco de teclado

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 10.1 | `Tab` / `Shift+Tab` percorre a UI | foco move em ordem previsível; foco sempre visível (outline) | |
| 10.2 | `Enter` no botão focado | ação executada (ex.: "Ir", "Voltar") | |
| 10.3 | `Esc` no painel | foco volta ao painel principal **ou** nenhum efeito harmful; sem fechar o painel sem querer | |
| 10.4 | Teclas de navegação (`PageUp`, `PageDown`, `Home`, `End`) dentro do painel | rolagem da página, sem mover o foco para fora da UI | |
| 10.5 | Digitar texto com acento e caracteres especiais (`ç`, `ã`, `~`, `"`, `<`) | texto entra corretamente, sem HTML escapado errado (`&amp;` visível) | |
| 10.6 | Ctrl+C / Ctrl+V em campo de texto | copiar/colar funcionam; Ctrl+C **não** derruba o processo quando há seleção | |
| 10.7 | Foco em campo de texto da página + `POST /type` | valor final exatamente o enviado (sem caractere sobrando/faltando) | |
| 10.8 | `POST /press {"key": "Enter"}` em página de busca fictícia | formulário submetido, URL mudou | |
| 10.9 | Foco não "presa": após navegar, `Tab` deve chegar nos elementos da nova página | foco não fica em elemento destruído | |
| 10.10 | Tecla desconhecida em `/press` (`{"key": "F13"}`) | erro controlado (`ok:false`), sem crash | |

---

## 11. Leitura de conteúdo (`/page`, `/links`, `/eval`)

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 11.1 | `GET /page` | chaves `{ok, url, title, text, html}` presentes; `text` não vazio | |
| 11.2 | `GET /links` | `links` é lista de `{text, href}`; href relativo continua relativo | |
| 11.3 | `GET /links` em página sem links | `[]`, não erro | |
| 11.4 | `POST /eval {"js": "document.title"}` | `{"ok": true, "result": "<titulo>"}` — `result` é o valor, não a string do código | |
| 11.5 | `POST /eval {"js": "1+1"}` | `result` == `2` (numérico, não `"1+1"`) | |
| 11.6 | `POST /eval` com JS inválido (`"sintaxe !!!"`) | `ok:false` + `error`; **nunca** derruba o servidor | |
| 11.7 | `POST /click {"selector": "#inexistente"}` | `ok:false` controlado, sem `500` | |
| 11.8 | `POST /type {"selector": "#campo", "text": "abc", "clear": true}` | campo fica **exatamente** `"abc"` (sem `abcabc`) | |
| 11.9 | `POST /type` com `clear: false` sobre campo já preenchido | texto concatenado ao final do existente | |
| 11.10 | `POST /screenshot` sem `path` | `{"ok": true, "path": "", "png_base64": "<base64>"}` decodificável | |
| 11.11 | `POST /screenshot {"path": "/tmp/x.png", "full": true}` | arquivo existe no caminho, é PNG válido, `ok:true` | |
| 11.12 | `POST /screenshot {"path": "/caminho/inexistente/dir/x.png"}` | erro controlado; o processo do painel **sobrevive** | |

---

## 12. Robustez / contrato geral

| # | Passo | Esperado | Obtido |
|---|-------|----------|--------|
| 12.1 | Rota desconhecida (`GET /nao-existe`) | `404` com corpo JSON `{"ok": false, "error": ...}` | |
| 12.2 | Corpo JSON inválido (`"{{{"`) em qualquer POST | `400` com corpo JSON, sem traceback | |
| 12.3 | Content-Type ausente no POST | ou aceito, ou `415`/`400` — nunca `500` | |
| 12.4 | Verifique que **toda** resposta tem `ok` e `error` | em 20+ chamadas, sempre presentes | |
| 12.5 | Em caso de erro, `error` não está vazio | mensagem útil | |
| 12.6 | Header `X-Tayama-Token` ausente em `DELETE /panels/<id>` | `401` (o painel **não** é fechado) | |
| 12.7 | Servidor sobrevive a todas as tentativas acima | processo ainda respondendo ao final do roteiro | |
| 12.8 | Reinício do servidor com painéis abertos | comportamento definido (panéis persistem ou somem — anotar) | |
| 12.9 | `tayama browser` sem subcomando | mensagem de uso clara, exit code != 0 | |
| 12.10 | `tayama browser <comando-inexistente>` | erro claro; não traceback cru | |

---

## 13. Achados

| # | Passo | Severidade | Bug (resumo) | Esperado | Obtido | Repro |
|---|-------|-----------|--------------|----------|--------|-------|
| | | alta/média/baixa | | | | |

Severidade: **alta** = quebra fluxo principal / crash; **média** = comportamento errado mas contornável; **baixa** = cósmetico/inconsistência.