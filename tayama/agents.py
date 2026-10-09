"""Agentes persistidos por workspace em ~/.tayama/agents/<workspace_id>.json."""
import json
from . import config


def _dir():
    config.ensure()
    d = config.DIR / "agents"; d.mkdir(exist_ok=True); return d


def _file(workspace_id):
    return _dir() / f"{workspace_id}.json"


def _read(workspace_id):
    # JSON válido mas de outro tipo ('null', '42') volta como lista vazia —
    # save_one/remove_one assumem lista, e um int aqui estouraria em len()/iteração.
    try: data = json.loads(_file(workspace_id).read_text("utf-8"))
    except (OSError, ValueError): return []
    return data if isinstance(data, list) else []


def _write(workspace_id, entries):
    f = _file(workspace_id)
    if not entries:
        if f.exists(): f.unlink()        # arquivo vazio some
        return
    # config._write_private (0o600) como em layout.py: os specs carregam nome de
    # agente, cargo e path de trabalho — o mesmo cuidado dos painéis/conexões.
    config._write_private(f, json.dumps(entries, indent=2, ensure_ascii=False))


def load_all():
    """Todos os agentes de todos os workspaces (para restauração no startup).

    Lê por _read() (que normaliza JSON corrompido/de outro tipo para []) e
    descarta entradas que não são dict — restore_agents indexa campos da spec
    direto, e um item solto derrubaria a abertura do app.
    """
    d = _dir()
    return [e for f in sorted(d.glob("*.json")) for e in _read(f.stem)
            if isinstance(e, dict)]


def save_one(win):
    """Atualiza (ou insere) a entrada de um FloatingWindow no arquivo do seu workspace."""
    ws_id = win.workspace["id"]
    spec = {
        "id": win.id, "name": win.name,
        "agent_name": win.agent["name"],
        "role_name": win.role["name"] if win.role else None,
        "workspace_id": ws_id,
        "skills": [s["name"] for s in win.skills],
        "x": win.proxy.pos().x(), "y": win.proxy.pos().y(),
        "w": win.size().width(), "h": win.size().height(),
    }
    entries = _read(ws_id)
    for i, e in enumerate(entries):
        if e["id"] == win.id: entries[i] = spec; break
    else: entries.append(spec)
    _write(ws_id, entries)


def load_one(workspace_id):
    """Entradas cruas de um workspace, sem filtrar por dict.

    Usado por workspaces.duplicate para clonar as specs persistidas — quem
    clona decide o que é um item válido, já que precisa reescrever ids.
    """
    return _read(workspace_id)


def save_all(workspace_id, specs):
    """Sobrescreve todas as specs de um workspace (usado por workspaces.duplicate).

    Simétrico a load_one(): a clonagem reescreve os ids antes de gravar, o que
    o caminho por save_one() — que upserta uma janela por vez — não permite.
    """
    _write(workspace_id, [s for s in specs if isinstance(s, dict)])


def remove_one(win_id, ws_id):
    _write(ws_id, [e for e in _read(ws_id) if e["id"] != win_id])


def remove_workspace(ws_id):
    f = _file(ws_id)
    if f.exists(): f.unlink()