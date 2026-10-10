"""API de painéis web: allowlist de URL, envelope JS e gadgets de DOM.

Sem Qt no topo de propósito. Este módulo é importado pelo Bridge e precisa
continuar importável sem WebEngine e sem QApplication — é o que permite testar
a camada de protocolo em milissegundos, sem display.

O envelope e os gadgets foram MEDIDOS contra um QWebEngineView de verdade; as
duas conclusões que moldaram o código:

  * `keyPressEvent`/`mousePressEvent` do QWebEnginePage são protegidos e nao
    expostos pelo PyQt6, e `QTest.keyClick` NAO alcanca o renderer (o campo de
    texto ficava vazio depois de duas teclas). Portanto todo mouse/teclado aqui
    e evento sintetico em JS, nunca a API de input do Qt.

  * O envelope precisa de `(0, eval)(src)`. Medido: `return (src)` dentro de
    try/catch derruba o wrapper inteiro em erro de sintaxe e devolve None
    SILENCIOSO; `new Function("return ("+src+")")` quebra em statements;
    `new Function(src)` devolve undefined para `1+1`. So `(0, eval)` trata
    expressao E statement, e SyntaxError/throw chegam como erro.
"""
import json, re

# Limites: mesmos numeros que o contrato HTTP abandonado usava
# (tests/test_browser_contract.py:57-60). Um terminal de agente nao aguenta
# um dump de pagina inteira.
MAX_BODY = 1 << 20          # request inteiro pelo socket
MAX_JS = 64 * 1024          # campo 'js' do eval
MAX_TEXT = 100_000          # campo 'text' do type
PAGE_TRUNCATE = 100_000     # /page trunca e diz que truncar
JS_TIMEOUT_MS = 15_000

ALLOWED_SCHEMES = ("http://", "https://")
ALLOWED_EXACT = ("about:blank",)

# Teclas: nome simples. O teclado e sintetico, entao o que importa e o nome
# que o JS usa em KeyboardEvent.key. Mesmo padrao do contrato antigo
# (test_browser_contract.py:449).
KEY_RE = re.compile(r"[A-Za-z0-9]{1,24}")


def check_url(u):
    """Valida uma URL pedida pela API. Devolve (url_normalizada, None) ou (None, erro).

    Nao reusar `BrowserWindow._normalize` (browser.py:164-170): ele aceita
    `file:` de proposito, porque a barra de endereco e operada pelo usuario. A
    API do agente precisa da allowlist — sem ela, `navigate file:///home/<u>/.ssh/id_rsa`
    seguido de `page` le o disco do usuario.

    A checagem e por PREFIXO DE STRING, nao por QUrl.scheme(): medido que o Qt
    normaliza `java\\tscript:alert(1)` para o esquema `javascript:`, entao
    confiar so no QUrl abre bypass.
    """
    if not isinstance(u, str):
        return None, "url deve ser texto"
    s = u.strip()
    if not s:
        return None, "url vazia"
    low = s.lower()
    if low in ALLOWED_EXACT:
        return s, None
    # Caractere de controle (tab, newline) sobrevivendo ao strip é a assinatura
    # classica de ofuscacao de esquema. Recusa outright.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in s):
        return None, "url com caractere de controle"
    if not low.startswith(ALLOWED_SCHEMES):
        return None, "esquema nao permitido: use http:// ou https://"
    return s, None


def envelope(src):
    """Monta o wrapper JS. `src` entra como literal JSON, nunca colado.

    JSON.stringify no fim faz o retorno ser SEMPRE string, o que elimina a
    ambiguidade do QVariant no Python: o Bridge sempre faz json.loads de uma str.
    Valor nao serializável (circular) faz o stringify lançar DENTRO do try e
    vira ok:false — nunca um objeto truncado em silêncio.

    O erro usa String(e) e NÃO e.message: medido que o SyntaxError do eval vem
    com message='Unexpected token !' e sem o nome da classe, então o agente
    receberia um erro sem pista de que foi sintaxe.

    ATENÇÃO: este envelope é para o código que o AGENTE escreve. Os gadgets
    abaixo (js_click, js_type, js_press, js_page, js_links) já devolvem um dict
    pronto com `ok` — mandá-los pelo envelope faz o `eval` do IIFE devolver
    undefined e o resultado se perder. Gadget vai cru para o renderer.
    """
    return ("(function(){try{var v=(0,eval)(%s);"
            "if(typeof v==='undefined')v=null;"
            "return JSON.stringify({ok:true,value:v});}"
            "catch(e){return JSON.stringify({ok:false,error:String(e)});}})()"
            % json.dumps(src))


def js_click(sel):
    return ("(function(){var s=document.querySelector(%s);"
            "if(!s){return {ok:false,error:'seletor nao encontrado: '+%s};}"
            "s.scrollIntoView({block:'center'});"
            "var o={bubbles:true,cancelable:true,view:window,button:0};"
            "['pointerdown','mousedown','mouseup','click'].forEach(function(t){"
            "s.dispatchEvent(new MouseEvent(t,o));});"
            "return {found:true,tag:s.tagName,"
            "text:(s.textContent||'').slice(0,80)};})()"
            % (json.dumps(sel), json.dumps(sel)))


def js_type(sel, text, append=False):
    """Digita num campo.

    Usa o setter NATIVO de value, nao `s.value = x`: em input controlado por
    React/Vue o framework guarda o valor num tracker interno e ignora atribuicao
    direta — o campo ficaria vazio na tela mesmo com o property setado.
    """
    anterior = "(s.value||'')+" if append else "''+"
    return ("(function(){var s=document.querySelector(%s);"
            "if(!s){return {ok:false,error:'seletor nao encontrado: '+%s};}"
            "s.focus();"
            "var set=Object.getOwnPropertyDescriptor("
            "s.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,"
            "'value');if(set&&set.set){set.set.call(s,%s%s);}else{s.value=%s%s;}"
            "s.dispatchEvent(new Event('input',{bubbles:true}));"
            "s.dispatchEvent(new Event('change',{bubbles:true}));"
            "return {found:true,value:s.value};})()"
            % (json.dumps(sel), json.dumps(sel), anterior, json.dumps(text),
               anterior, json.dumps(text)))


def js_press(key, selector=None):
    """Teclado no elemento (ou no que tem foco). Enter num form submete."""
    if not KEY_RE.fullmatch(key or ""):
        return None, "tecla invalida: %s (use o nome, ex.: Enter, Tab, Escape, a)" % key
    alvo = ("document.querySelector(%s)" % json.dumps(selector)) if selector else "document.activeElement"
    return ("(function(){var s=%s;"
            "if(!s){return {ok:false,error:'sem elemento para a tecla'};}"
            "s.focus();"
            "var o={key:%s,code:%s,bubbles:true,cancelable:true};"
            "['keydown','keypress','keyup'].forEach(function(t){"
            "var e=new KeyboardEvent(t,o);var go=s.dispatchEvent(e);"
            "if(t==='keydown'&&%s&&!e.defaultPrevented&&s.form){s.form.requestSubmit();}"
            "});"
            "return {found:true,key:%s};})()"
            % (alvo, json.dumps(key), json.dumps("Key" + key[0].upper() + key[1:]),
               json.dumps(key == "Enter"), json.dumps(key)), None)


def js_page(with_html=False):
    """Conteudo da pagina. `innerText` (renderizado) e `textContent` (bruto)
    diferem em paginas com CSS, entao devolvemos o renderizado."""
    html = (",html:(document.documentElement.outerHTML||'').slice(0,%d),"
            "truncatedHtml:(document.documentElement.outerHTML||'').length>%d"
            % (PAGE_TRUNCATE, PAGE_TRUNCATE)) if with_html else ""
    return ("(function(){var t=document.body?(document.body.innerText||''):'';"
            "return {url:location.href,title:document.title,"
            "text:t.slice(0,%d),truncated:t.length>%d%s};})()"
            % (PAGE_TRUNCATE, PAGE_TRUNCATE, html))


def js_links(root=None):
    """Links da pagina. `href` resolvido (util para navegar) e `raw` do atributo
    (util quando o agente quer recombinar com a URL atual)."""
    base = ("document.querySelector(%s)||document" % json.dumps(root)) if root else "document"
    return ("(function(){return Array.from(%s.querySelectorAll('a[href]')).slice(0,300)"
            ".map(function(a){return {text:(a.textContent||'').trim().slice(0,80),"
            "href:a.href,raw:a.getAttribute('href')};});})()" % base)


def resolve_panel(canvas, src, pid):
    """Escolhe o painel alvo. Devolve (painel, None) ou (None, erro).

    Ordem: id exacto, nome, e por fim o painel ligado por seta ao terminal que
    chamou. Adivinhar entre varios painéis e pior que falhar — por isso o erro
    de "mais de um" lista os ids.
    """
    panels = canvas.panels
    if pid:
        if pid in panels:
            return panels[pid], None
        for p in panels.values():
            if p.name == pid:
                return p, None
        return None, "painel inexistente: %s (use 'tayama panel list')" % pid
    ligados = [p for p in canvas.peers_of(src) if p in panels.values()]
    if not ligados:
        return None, ("nenhum painel conectado a voce. Ligue um pela seta "
                      "(clique no link dos dois) ou passe --panel <id>.")
    if len(ligados) > 1:
        return None, ("%d paineis conectados (%s) — use --panel <id>"
                      % (len(ligados), ", ".join(p.id for p in ligados)))
    return ligados[0], None


def mesmo_workspace(panel, src):
    """Workspaces sao ambientes separados: um painel so e acionavel de dentro
    do workspace de quem pergunta. Mesmo criterio de ui.py:390-397."""
    a = (getattr(panel, "workspace", None) or {}).get("id")
    b = (getattr(src, "workspace", None) or {}).get("id")
    return not a or not b or a == b