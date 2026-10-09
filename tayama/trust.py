"""Pré-aprova a pasta de trabalho nos CLIs para pular o aviso "você confia nesta pasta?"."""
import json, os, shlex, tempfile
from pathlib import Path


def _roots(cwd):
    p = Path(cwd).resolve(); out = [str(p)]
    for d in (p, *p.parents):
        if (d / ".git").exists():
            out.append(str(d)); break
    return list(dict.fromkeys(out))


def _claude(cwd):
    base = Path(os.environ["CLAUDE_CONFIG_DIR"]) if os.environ.get("CLAUDE_CONFIG_DIR") else Path.home()
    f = base / ".claude.json"
    if not f.exists(): return
    data = json.loads(f.read_text("utf-8")); projs = data.setdefault("projects", {})
    for r in _roots(cwd):
        e = projs.setdefault(r, {}); e["hasTrustDialogAccepted"] = True; e["hasCompletedProjectOnboarding"] = True
    fd, tmp = tempfile.mkstemp(dir=str(f.parent)); os.close(fd)
    Path(tmp).write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8"); os.replace(tmp, f)


def _codex(cwd):
    d = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")); d.mkdir(parents=True, exist_ok=True)
    f = d / "config.toml"; txt = f.read_text("utf-8") if f.exists() else ""
    for r in _roots(cwd):
        header = f'[projects."{r}"]'
        if header not in txt:
            txt += ("\n" if txt and not txt.endswith("\n") else "") + f'\n{header}\ntrust_level = "trusted"\n'
    f.write_text(txt, "utf-8")


def kind_of(agent):
    try: exe = os.path.basename(shlex.split(agent["command"])[0])
    except (ValueError, IndexError): return "none"
    return next((k for k in ("claude", "codex", "opencode") if exe.startswith(k)), "none")


def ensure(agent, cwd):
    """agent["trust"]: "claude" | "codex" | "none" (padrão: detecta pelo comando)."""
    kind = agent.get("trust") or kind_of(agent)
    {"claude": _claude, "codex": _codex}.get(kind, lambda c: None)(cwd)


def quiet(agent):
    """(variáveis de ambiente, argumentos extras) que desligam avisos/instalação de atualização."""
    kind = agent.get("trust") or kind_of(agent)
    if kind == "claude": return {"DISABLE_AUTOUPDATER": "1", "DISABLE_UPDATES": "1"}, []
    if kind == "codex": return {}, ["-c", "check_for_update_on_startup=false"]
    if kind == "opencode": return {"OPENCODE_DISABLE_AUTOUPDATE": "true"}, []
    return {}, []
