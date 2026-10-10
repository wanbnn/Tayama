"""Parsing do `bin/tayama` — o que o agente realmente digita.

Sem Qt e sem display: é o `build()` do bin, importado por caminho. Rápido, e
cobre a classe de bug mais invisível desta camada — o request ficar correto
mas perder a chave `from`, que só aparece quando o Bridge recusa.

    .venv/bin/python -m pytest tests/test_cli_parsing.py -v
"""
import os, sys, json, subprocess

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(RAIZ, "bin", "tayama")


def _carrega():
    """Importa bin/tayama pelo caminho. Não é módulo no path, e `sys.exit` no
    fim do arquivo mataria o pytest no import."""
    src = open(BIN, encoding="utf-8").read()
    src = src.replace("sys.exit(main())", "pass")     # neutraliza o ponto de entrada
    mod = type(sys)("tayama_cli")
    mod.__dict__["__file__"] = BIN
    exec(compile(src, BIN, "exec"), mod.__dict__)
    return mod


CLI = _carrega()


def build(argv, tid="t1"):
    old = os.environ.get("TAYAMA_ID")
    os.environ["TAYAMA_ID"] = tid
    try:
        return CLI.build(argv)
    finally:
        if old is None:
            os.environ.pop("TAYAMA_ID", None)
        else:
            os.environ["TAYAMA_ID"] = old


# ==========================================================================
# A regressão: `from` tem que sobreviver a todo comando
# ==========================================================================
def test_todo_comando_carrega_o_from():
    """A REGRESSÃO que o ponta a ponta pegou: o ramo `panel` devolvia o dict de
    `painel()` direto, e esse dict não tem `from`. Resultado: TAYAMA_ID Some,
    o Bridge responde "terminal de origem desconhecido" e NENHUM comando de
    painel funciona — com o parsing inteiro correto, o que não aparece em
    nenhum teste que só olhe o request."""
    cmds = [
        ["peers"],
        ["send", "dev", "oi"],
        ["broadcast", "oi"],
        ["panel", "list"],
        ["panel", "page"],
        ["panel", "click", "#b"],
        ["panel", "type", "#q", "texto"],
        ["panel", "press", "Enter"],
        ["panel", "eval", "1+1"],
        ["panel", "navigate", "https://exemplo.com"],
        ["panel", "open", "https://exemplo.com"],
        ["panel", "close", "--panel", "p1"],
    ]
    for argv in cmds:
        req, err = build(argv)
        assert req is not None, (argv, err)
        assert req.get("from") == "t1", f"{argv} perdeu o TAYAMA_ID: {req}"


def test_panel_tem_cmd_e_op_certo():
    req, _ = build(["panel", "click", "#b", "--panel", "p1"])
    assert req["cmd"] == "panel" and req["op"] == "click"
    assert req["selector"] == "#b" and req["panel"] == "p1"


# ==========================================================================
# Posicionais e flags por op
# ==========================================================================
def test_type_tem_dois_posicionais():
    req, _ = build(["panel", "type", "#q", "abc", "--append"])
    assert req["selector"] == "#q" and req["text"] == "abc" and req["append"] is True


def test_type_aceita_texto_com_espaco_quando_ve_aspas():
    """`type "#q" "abc def"` chega como 2 posicionais e o texto traz o espaço —
    é assim que o shell entrega. Sem aspas, 3 posicionais viram erro de uso."""
    req, err = build(["panel", "type", "#q", "abc def"])
    assert req is not None and req["text"] == "abc def", err


def test_type_com_tres_posicionais_da_erro_de_uso():
    req, err = build(["panel", "type", "#q", "abc", "def"])
    assert req is None and "type exige" in err


def test_eval_espaco_volta_junto():
    """`eval` recebe código com espaços; tem que remontar com espaço, senão
    `var x = 1` viraria `varx = 1`."""
    req, _ = build(["panel", "eval", "var x = 1; x + 1"])
    assert req["js"] == "var x = 1; x + 1"


def test_eval_comecado_por_parentese_nao_e_confundido_com_flag():
    req, _ = build(["panel", "eval", "document.querySelector('#q').value"])
    assert req["js"] == "document.querySelector('#q').value"


def test_eval_com_flag_no_meio_tem_a_flag_comida():
    """O `--panel` é consumido como flag; o resto é o código."""
    req, _ = build(["panel", "eval", "--panel", "p1", "1+1"])
    assert req["panel"] == "p1" and req["js"] == "1+1"


def test_page_full_e_boolean():
    req, _ = build(["panel", "page", "--full"])
    assert req["full"] is True


def test_back_nao_aceita_posicional():
    req, err = build(["panel", "back", "extra"])
    assert req is None and "posicionais" in err


def test_open_nome():
    req, _ = build(["panel", "open", "https://x.com", "--name", "docs"])
    assert req["url"] == "https://x.com" and req["name"] == "docs"


def test_open_sem_url_e_about_blank():
    req, _ = build(["panel", "open"])
    assert "url" not in req


# ==========================================================================
# Erro de uso: código != 0 e SEM traceback
# ==========================================================================
def _roda_cli(*args, **env_extra):
    env = dict(os.environ, **env_extra)
    return subprocess.run([sys.executable, BIN, *args],
                          capture_output=True, text=True, env=env, timeout=30)


@pytest.mark.parametrize("args", [
    [],
    ["nada"],
    ["panel"],
    ["panel", "op-inexistente"],
    ["panel", "click"],
    ["panel", "click", "a", "b"],
    ["panel", "type", "so-um"],
    ["panel", "press"],
    ["panel", "eval"],
    ["panel", "page", "--flag-nao-existe"],
    ["panel", "list", "--panel", "p1"],       # list não aceita --panel
    ["panel", "open", "u", "--panel", "p1"],  # open também não
    ["send", "so-um-arg"],
    ["broadcast"],
])
def test_uso_errado_sai_sem_traceback(args):
    """A regra do repo: erro de uso sai com código != 0 e sem traceback — o
    agente recebe uma frase, não um traceback."""
    r = _roda_cli(*args, TAYAMA_SOCK="/tmp/nada.sock", TAYAMA_ID="t1")
    assert r.returncode != 0, (args, r.stdout)
    assert "Traceback" not in r.stderr, (args, r.stderr)
    assert r.stderr.strip(), f"sem mensagem de uso: {args}"


def test_tayama_indisponivel_da_erro_e_nao_traceback():
    r = _roda_cli("peers", TAYAMA_SOCK="/tmp/inexistente-tayama.sock", TAYAMA_ID="t1")
    assert r.returncode == 1
    assert "indispon" in r.stdout.lower(), r.stdout
    assert "Traceback" not in r.stderr


# ==========================================================================
# O formatador de resposta — a armadilha do KeyError
# ==========================================================================
def test_formata_nao_levanta_key_error_em_resposta_nova():
    """O formatador antigo só conhecia `peers` e `sent_to`; qualquer resposta
    nova caía no else e levantava KeyError: 'sent_to' — com traceback, no
    lugar do valor."""
    respostas = [
        {"ok": True, "value": 2},
        {"ok": True, "panels": []},
        {"ok": True, "panels": [{"id": "p1", "url": "http://x", "state": "ok", "linked": True}]},
        {"ok": True, "peers": [{"name": "d", "role": "Dev", "agent": "X", "kind": "terminal"}]},
        {"ok": True, "peers": [{"name": "p", "role": "painel web", "agent": "Chromium", "kind": "painel"}]},
        {"ok": True, "sent_to": ["d"]},
        {"ok": True, "url": "http://x", "title": "T"},
        {"ok": True, "value": [{"text": "siga", "href": "http://x/o"}]},
        {"ok": True},
    ]
    for r in respostas:
        out = CLI.formata(r)          # não pode levantar
        assert isinstance(out, str), r


def test_formata_painel_marca_o_ligado():
    out = CLI.formata({"ok": True, "panels": [
        {"id": "p1", "url": "http://x", "state": "ok", "linked": True},
        {"id": "p2", "url": "http://y", "state": "loading", "linked": False}]})
    assert "p1" in out and "ligado a voce" in out
    assert "p2" in out and "ligado a voce" not in out.split("p2")[1]


def test_formata_panel_vazio():
    assert CLI.formata({"ok": True, "panels": []}) == "nenhum painel aberto"


def test_formata_value_mostra_o_valor():
    assert CLI.formata({"ok": True, "value": 2}).strip() == "2"
    assert "siga" in CLI.formata({"ok": True, "value": [{"text": "siga"}]})


def test_formata_peers_marca_painel():
    out = CLI.formata({"ok": True, "peers": [
        {"name": "docs", "role": "painel web", "agent": "Chromium", "kind": "painel"}]})
    assert "docs" in out and "[painel]" in out


import pytest   # noqa: E402  (depois dos helpers, para o parametrize acima)