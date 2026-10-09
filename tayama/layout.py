"""Painéis web e conexões persistidos por workspace em ~/.tayama.

Espelha o estilo de tayama/agents.py: _read/_write tolerantes a OSError/ValueError,
arquivo vazio some do disco, permissão 0o600 via config._write_private.

    ~/.tayama/panels/<ws_id>.json   ->  [{id, name, url, x, y, w, h}, ...]
    ~/.tayama/links/<ws_id>.json    ->  [[src_id, dst_id], ...]

Terminais NÃO moram aqui (continuam em agents.py) — este módulo cuida apenas do
que é exclusivo dos painéis e das setas entre eles.
"""
import json
from . import config

PANELS, LINKS = "panels", "links"


def _file(kind, workspace_id):
    config.ensure()
    d = config.DIR / kind; d.mkdir(exist_ok=True)
    return d / f"{workspace_id}.json"


def _read(kind, workspace_id):
    try: return json.loads(_file(kind, workspace_id).read_text("utf-8"))
    except (OSError, ValueError): return []


def _write(kind, workspace_id, entries):
    f = _file(kind, workspace_id)
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


def load_all_panels():
    """[(ws_id, spec), ...] de todos os workspaces (para restauração no startup)."""
    config.ensure()
    d = config.DIR / PANELS
    out = []
    for f in sorted(d.glob("*.json")):
        try: out += [(f.stem, spec) for spec in json.loads(f.read_text("utf-8"))]
        except (OSError, ValueError): continue
    return out


# --- conexões --------------------------------------------------------------
def save_links(ws_id, pairs):
    """pairs = lista de (src_id, dst_id). Lista vazia remove o arquivo."""
    if not ws_id: return
    _write(LINKS, ws_id, [[a, b] for a, b in pairs])


def load_links(ws_id):
    return [tuple(p) for p in _read(LINKS, ws_id) if isinstance(p, (list, tuple)) and len(p) == 2]


def load_all_links():
    """[(ws_id, (src_id, dst_id)), ...] de todos os workspaces."""
    config.ensure()
    d = config.DIR / LINKS
    out = []
    for f in sorted(d.glob("*.json")):
        try: out += [(f.stem, tuple(p)) for p in json.loads(f.read_text("utf-8"))
                     if isinstance(p, (list, tuple)) and len(p) == 2]
        except (OSError, ValueError): continue
    return out


def remove_workspace(ws_id):
    """Apaga painéis e conexões do workspace (chamado por workspaces.remove)."""
    for kind in (PANELS, LINKS):
        f = _file(kind, ws_id)
        if f.exists(): f.unlink()