"""Configuração persistente em ~/.tayama (agentes, cargos, skills)."""
import json, os
from pathlib import Path

DIR = Path(os.environ.get("TAYAMA_HOME", Path.home() / ".tayama"))
CFG = DIR / "config.json"
SKILLS = DIR / "skills"          # cada .md aqui vira uma skill automaticamente
BIN = str(Path(__file__).resolve().parent.parent / "bin")
SOCK = f"/tmp/tayama-{os.getuid()}.sock" if hasattr(os, "getuid") else ""

DEFAULT = {
    "agents": [
        {"name": "Claude Code", "command": "claude", "args": [], "auto_briefing": True, "startup_delay_ms": 5000},
        {"name": "Codex CLI", "command": "codex", "args": [], "auto_briefing": True, "startup_delay_ms": 5000},
        {"name": "OpenCode CLI", "command": "opencode", "args": [], "auto_briefing": True, "startup_delay_ms": 5000},
        {"name": "Shell", "command": "$SHELL", "args": ["-l"], "auto_briefing": False, "startup_delay_ms": 800},
    ],
    "roles": [
        {"name": "Líder", "color": "#e5a400",
         "prompt": "Você coordena a equipe. Quebre o objetivo em tarefas pequenas, delegue aos terminais conectados com `tayama send`, acompanhe o progresso, revise entregas e consolide o resultado. Evite programar você mesmo."},
        {"name": "Desenvolvedor", "color": "#2f9e44",
         "prompt": "Você implementa as tarefas recebidas com código limpo e commits pequenos. Ao terminar, avise quem pediu (e o tester, se conectado) dizendo o que mudou e como testar."},
        {"name": "Tester", "color": "#d6336c",
         "prompt": "Você valida o trabalho: escreve e roda testes, tenta quebrar a implementação e reporta bugs reproduzíveis (passos, esperado, obtido) ao desenvolvedor."},
        {"name": "Revisor", "color": "#1c7ed6",
         "prompt": "Você faz code review: aponte bugs, riscos de segurança e problemas de design com sugestões objetivas."},
    ],
    "skills": [
        {"name": "Relatórios curtos", "text": "Ao concluir uma tarefa, responda a quem delegou com no máximo 5 linhas: o que foi feito, arquivos alterados e pendências."},
        {"name": "Trabalho incremental", "text": "Faça mudanças pequenas e verificáveis. Rode os testes antes de dizer que terminou."},
    ],
}


def _write_private(path: Path, text: str, mode: int = 0o600):
    """Escreve arquivo com permissão restrita.

    write_text() respeita o umask e NÃO corrige permissão de arquivo já existente,
    então passamos por os.open(..., O_CREAT|O_WRONLY, mode) e um chmod final.
    """
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, mode)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def ensure():
    DIR.mkdir(parents=True, exist_ok=True)
    SKILLS.mkdir(exist_ok=True)
    try:
        os.chmod(DIR, 0o700)          # ~/.tayama só para o usuário
    except OSError:
        pass
    if not CFG.exists():
        _write_private(CFG, json.dumps(DEFAULT, indent=2, ensure_ascii=False))
    else:
        try:
            os.chmod(CFG, 0o600)
        except OSError:
            pass
    try:
        os.chmod(Path(BIN) / "tayama", 0o755)
    except OSError:
        pass


def raw() -> str:
    ensure()
    return CFG.read_text("utf-8")


def save_raw(text: str):
    json.loads(text)  # valida
    _write_private(CFG, text)


def load() -> dict:
    cfg = json.loads(raw())
    cfg.setdefault("agents", []); cfg.setdefault("roles", []); cfg.setdefault("skills", [])
    for p in sorted(SKILLS.glob("*.md")):
        cfg["skills"].append({"name": p.stem, "text": p.read_text("utf-8")})
    return cfg
