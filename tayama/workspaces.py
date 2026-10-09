"""Workspaces persistidos em ~/.tayama/workspaces.json: {id, name, path}."""
import json, uuid
from . import config


def _file():
    config.ensure(); return config.DIR / "workspaces.json"


def load():
    try: return json.loads(_file().read_text("utf-8"))
    except (OSError, ValueError): return []


def save(items): _file().write_text(json.dumps(items, indent=2, ensure_ascii=False), "utf-8")


def add(name, path):
    w = {"id": uuid.uuid4().hex[:8], "name": name, "path": path}
    save(load() + [w]); return w


def remove(wid):
    save([w for w in load() if w["id"] != wid])
    from . import agents, layout
    agents.remove_workspace(wid)
    layout.remove_workspace(wid)      # painéis e conexões daquele workspace
