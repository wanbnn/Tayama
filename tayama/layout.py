"""Painéis web e conexões persistidos por workspace em ~/.tayama.

Espelha o estilo de tayama/agents.py: _read/_write tolerantes a OSError/ValueError,
arquivo vazio some do disco, permissão 0o600 via config._write_private.

    ~/.tayama/panels/<ws_id>.json   ->  [{id, name, url, x, y, w, h}, ...]
    ~/.tayama/links/<ws_id>.json    ->  [[src_id, dst_id], ...]

Terminais NÃO moram aqui (continuam em agents.py) — este módulo cuida apenas do
que é exclusivo dos painéis e das setas entre eles.
"""
import json, re
from . import config

PANELS, LINKS = "panels", "links"

# O arquivo é nomeado pelo id do workspace, que vem de uuid4().hex[:8]:
# sem validação, '../../pwned' escapa do TAYAMA_HOME e 'sub/dir' estoura
# FileNotFoundError. Aceitamos só o que um id pode ter.
# \Z e não $: em Python '$' também casa antes de um '\n' final ('id\n' passaria).
WS_ID_RE = re.compile(r"^[A-Za-z0-9_-]+\Z")


def _valid(ws_id):
    return isinstance(ws_id, str) and bool(WS_ID_RE.match(ws_id))


def _file(kind, workspace_id):
    """Caminho do arquivo do workspace, ou None se o id não é aceitável.

    Preferimos degradar (não persistir, devolver []) a levantar erro: um id
    degenerado vem de dados velhos na tela, não de uma entrada do usuário, e
    o painel ainda precisa funcionar na sessão.
    """
    if not _valid(workspace_id): return None
    config.ensure()
    d = config.DIR / kind; d.mkdir(exist_ok=True)
    return d / f"{workspace_id}.json"


def _read(kind, workspace_id):
    # JSON válido mas de outro tipo ('null', '42') volta como lista vazia:
    # o chamador sempre espera lista, e um int aqui estouraria em len()/iteração.
    f = _file(kind, workspace_id)
    if f is None: return []
    try: data = json.loads(f.read_text("utf-8"))
    except (OSError, ValueError): return []
    return data if isinstance(data, list) else []


def _write(kind, workspace_id, entries):
    f = _file(kind, workspace_id)
    if f is None: return
    if not entries:
        if f.exists(): f.unlink()        # arquivo vazio some
        return
    config._write_private(f, json.dumps(entries, indent=2, ensure_ascii=False))


# --- painéis ---------------------------------------------------------------
def _spec(win):
    """Spec de um painel. Sem proxy (ainda não posicionado) grava em 0."""
    pos = win.proxy.pos() if getattr(win, "proxy", None) else None
    return {
        "id": win.id, "name": win.name,
        "url": getattr(win, "url", None) and win.url.text().strip() or "about:blank",
        "x": pos.x() if pos else 0, "y": pos.y() if pos else 0,
        "w": win.width(), "h": win.height(),
    }


def _ws_of(win):
    """Id do workspace do objeto, ou None se ele não pertence a nenhum."""
    return ((getattr(win, "workspace", None) or {}).get("id")) or None


def save_panel(win):
    """Atualiza (ou insere) o painel no arquivo do seu workspace.

    Painel sem workspace (nenhum criado ainda) simplesmente não é salvo.
    """
    ws_id = _ws_of(win)
    if not ws_id: return
    spec = _spec(win)
    entries = _read(PANELS, ws_id)
    for i, e in enumerate(entries):
        if e.get("id") == spec["id"]: entries[i] = spec; break
    else: entries.append(spec)
    _write(PANELS, ws_id, entries)


def remove_panel(panel_id, ws_id):
    if not ws_id: return
    _write(PANELS, ws_id, [e for e in _read(PANELS, ws_id) if e.get("id") != panel_id])


def load_panels(ws_id):
    return _read(PANELS, ws_id)


def _all(kind):
    """[(ws_id, item), ...] de todos os workspaces, lendo por _read().

    Delegar a _read() é o ponto: ela já normaliza JSON corrompido, de outro tipo
    e id invalido para []. Reparsear aqui criaria um segundo caminho com outras
    regras — foi assim que um '42' num panels/<ws>.json passou a derrubar o
    startup (restore_panels chama isto na abertura).
    """
    config.ensure()
    d = config.DIR / kind
    if not d.is_dir(): return []
    return [(f.stem, item) for f in sorted(d.glob("*.json"))
            for item in _read(kind, f.stem)]


def load_all_panels():
    """[(ws_id, spec), ...] de todos os workspaces (para restauração no startup)."""
    # item precisa ser dict: restore_panels chama spec.get() e spec["x"] nele.
    return [(ws, spec) for ws, spec in _all(PANELS) if isinstance(spec, dict)]


# --- conexões --------------------------------------------------------------
def save_links(ws_id, pairs):
    """pairs = lista de (src_id, dst_id). Lista vazia remove o arquivo."""
    _write(LINKS, ws_id, [[a, b] for a, b in pairs])


def _is_pair(item):
    """Um par de conexao: lista/tupla de exatamente 2 itens."""
    return isinstance(item, (list, tuple)) and len(item) == 2


def load_links(ws_id):
    return [tuple(p) for p in _read(LINKS, ws_id) if _is_pair(p)]


def load_all_links():
    """[(ws_id, (src_id, dst_id)), ...] de todos os workspaces."""
    return [(ws, tuple(item)) for ws, item in _all(LINKS) if _is_pair(item)]


def remove_workspace(ws_id):
    """Apaga painéis e conexões do workspace (chamado por workspaces.remove)."""
    for kind in (PANELS, LINKS):
        f = _file(kind, ws_id)
        if f is not None and f.exists(): f.unlink()