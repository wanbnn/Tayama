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


def update(wid, name, path):
    """Reescreve nome/diretório de um workspace existente, mantendo o id.

    Devolve o dict atualizado, ou None se o id não existir mais (a sidebar
    pode estar mostrando uma entrada velha).
    """
    items = load()
    for w in items:
        if w.get("id") == wid:
            w["name"], w["path"] = name, path; save(items); return w
    return None


def _unique_name(base):
    """'<base> (copia)', '<base> (2)', '<base> (3)'... até não colidir com nenhum nome."""
    taken = {w.get("name") for w in load()}
    if f"{base} (copia)" not in taken: return f"{base} (copia)"
    i = 2
    while f"{base} ({i})" in taken: i += 1
    return f"{base} ({i})"


def duplicate(ws):
    """Clona um workspace inteiro (id, nome e conteúdo persistido) e devolve o dict do novo.

    Só o que está no disco é clonado: agentes/painéis abertos na tela continuam
    no workspace original. Cada item clonado ganha id novo e os links são
    reescritos pelo mapa id_antigo -> id_novo — sem isso as setas apontariam
    para janelas que não existem no workspace novo.
    """
    from . import agents, layout
    old_id = ws.get("id")
    new = {"id": uuid.uuid4().hex[:8], "name": _unique_name(ws.get("name") or "workspace"), "path": ws.get("path", "")}
    save(load() + [new])

    # itens que não são dict são descartados: só eles carregam os campos copiados
    src_a = [e for e in agents.load_one(old_id) if isinstance(e, dict)]
    src_p = [p for p in layout.load_panels(old_id) if isinstance(p, dict)]
    ids = {}
    def _fresh(i):
        new_i = ids.setdefault(i, uuid.uuid4().hex[:8]); return new_i

    agents_out = [{**e, "id": _fresh(e.get("id")), "workspace_id": new["id"]} for e in src_a]
    panels_out = [{**p, "id": _fresh(p.get("id"))} for p in src_p]
    agents.save_all(new["id"], agents_out)
    layout.save_panels(new["id"], panels_out)
    # par cujo ponta não está no mapa é solto (setaria para janela inexistente)
    layout.save_links(new["id"], [(ids.get(a), ids.get(b)) for a, b in layout.load_links(old_id) if a in ids and b in ids])
    return new


def remove(wid):
    save([w for w in load() if w["id"] != wid])
    from . import agents, layout
    agents.remove_workspace(wid)
    layout.remove_workspace(wid)      # painéis e conexões daquele workspace
