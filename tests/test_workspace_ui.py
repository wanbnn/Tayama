#!/usr/bin/env python3
"""CONTRATO DE UI de editar/duplicar workspace (a camada que os testes de
workspaces.py NAO cobriam).

    ./run.sh                                  # roda com as dependencias
    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_workspace_ui.py -v

Por que este arquivo existe
---------------------------
tests/test_workspace_clone.py exercita so tayama/workspaces.py (funcoes puras
sobre arquivos). Um metodo placed na classe errada passa essa suite inteira e
so estoura quando o usuario abre o app: foi exatamente o que aconteceu com
refresh_agent_label, que foi parar em WorkspaceDialog em vez de
FloatingWindow — 254 testes verdes e o app morrendo ao abrir o primeiro
terminal. Estes testes montam widgets de verdade (QApplication offscreen) para
que esse erro apareca na suite.

Se PyQt6 nao estiver instalado, pula com o motivo explicito.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- PyQt6 tem de vir ANTES do QApplication (restricao do QtWebEngine) ------
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PyQt6.QtWidgets import QApplication
    HAS_QT = True
    _ERR = ""
except Exception as exc:  # pragma: no cover
    HAS_QT = False
    _ERR = f"{type(exc).__name__}: {exc}"

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
                   or os.environ.get("QT_QPA_PLATFORM") == "offscreen")

pytestmark = [
    pytest.mark.skipif(
        not (HAS_QT and HAS_DISPLAY),
        reason=(f"contrato de UI exige PyQt6 + display. qt={HAS_QT} ({_ERR}); "
                f"display={HAS_DISPLAY}. Rode com QT_QPA_PLATFORM=offscreen sob um "
                "venv com os requisitos instalados."),
    ),
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """TAYAMA_HOME isolado — nada toca no ~/.tayama do usuario."""
    fake = tmp_path / "tayama-home"
    fake.mkdir()
    monkeypatch.setenv("TAYAMA_HOME", str(fake))
    from tayama import config
    monkeypatch.setattr(config, "DIR", fake)
    monkeypatch.setattr(config, "CFG", fake / "config.json")
    monkeypatch.setattr(config, "SKILLS", fake / "skills")
    config.ensure()
    return fake


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(["tayama-test"])
    yield app


@pytest.fixture
def ws(home):
    """Workspace persistido, com um agente 'sleep' configurado (nada e lancado)."""
    from tayama import config, workspaces
    cfg = json.loads((home / "config.json").read_text("utf-8"))
    cfg["agents"] = [{"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0}]
    (home / "config.json").write_text(json.dumps(cfg), "utf-8")
    return workspaces.add("Original", str(home))


def term_spec(ws, name="alfa", pid="term-1"):
    """Spec de terminal — mesmo formato que agents.save_one grava."""
    return {"id": pid, "name": name,
            "agent": {"name": "sleep", "command": "sleep", "args": ["600"],
                      "auto_briefing": False, "startup_delay_ms": 0},
            "role": {"name": "Dev", "color": "#58a6ff"},
            "workspace": ws, "cwd": ws["path"], "skills": []}


class _FakeCanvas:
    """Canvas mínimo para FloatingWindow.

    A janela só toca o canvas em lambda/sinal (start_link, peers_of, changed),
    nunca no __init__ — entao um stub cobre o contrato do label sem subir o
    InfiniteCanvas inteiro nem o QtWebEngine.
    """
    def peers_of(self, win): return []

    def start_link(self, win): pass

    def remove_window(self, win): pass

    def _persist_window(self, win): pass


@pytest.fixture
def canvas():
    return _FakeCanvas()


# ==========================================================================
# 1. WorkspaceDialog com ws existente = edicao (id preservado)
# ==========================================================================
def test_dialog_com_ws_existente_preenche_campos(qapp, ws):
    from tayama.ui import WorkspaceDialog
    d = WorkspaceDialog(ws)
    assert d.name.text() == "Original"
    assert d.path.text() == str(ws["path"])
    assert d.windowTitle() == "Editar workspace"


def test_dialog_com_ws_existente_marca_o_ws(qapp, ws):
    """O dialog precisa saber qual workspace esta editando — e o id nao muda."""
    from tayama.ui import WorkspaceDialog
    d = WorkspaceDialog(ws)
    assert d.existing == ws
    assert d.value() == ("Original", str(ws["path"]))


def test_dialog_com_ws_existente_preserva_o_browse(qapp, ws):
    """Editar precisa continuar permitindo trocar o diretorio."""
    from PyQt6.QtWidgets import QPushButton
    from tayama.ui import WorkspaceDialog
    d = WorkspaceDialog(ws)
    assert any(isinstance(b, QPushButton) for b in d.findChildren(QPushButton)), \
        "o botao de browse sumiu no modo edicao"


def test_dialog_com_ws_existente_nao_sobrescreve_workspace_nenhum(qapp, ws):
    """Abrir o dialog e recusar não pode gravar nada."""
    from tayama import workspaces
    from tayama.ui import WorkspaceDialog
    antes = workspaces.load()
    d = WorkspaceDialog(ws)
    d.name.setText("Outro nome"); d.reject()
    assert workspaces.load() == antes


# ==========================================================================
# 2. WorkspaceDialog sem ws = criar (comportamento intacto)
# ==========================================================================
def test_dialog_sem_ws_fica_vazio_e_titulado_novo(qapp, home):
    from tayama.ui import WorkspaceDialog
    d = WorkspaceDialog()
    assert d.name.text() == "" and d.path.text() == ""
    assert d.windowTitle() == "Novo workspace"
    assert d.existing is None


def test_dialog_novo_aceita_parent_como_keyword(qapp, home):
    """main.py abre WorkspaceDialog(parent=self) — o primeiro posicional virou
    'existing', entao o uso por keyword tem de continuar funcionando."""
    from tayama.ui import WorkspaceDialog
    d = WorkspaceDialog(parent=None)
    assert d.existing is None and d.windowTitle() == "Novo workspace"


# ==========================================================================
# 3. FloatingWindow: o metodo tem de estar AQUI
# ==========================================================================
def test_refresh_agent_label_mora_em_floating_window(qapp):
    """O defeito: refresh_agent_label foi definido em WorkspaceDialog.
    O chamador (__init__) e MainWindow.edit_workspace vivem em FloatingWindow,
    entao o metodo na classe errada quebrava o app ao abrir o 1o terminal."""
    from tayama.ui import FloatingWindow, WorkspaceDialog
    assert hasattr(FloatingWindow, "refresh_agent_label"), \
        "refresh_agent_label ausente em FloatingWindow (colocado na classe errada?)"
    assert not hasattr(WorkspaceDialog, "refresh_agent_label"), \
        "refresh_agent_label nao pertence a WorkspaceDialog"


def test_floating_window_constroi_sem_attributeerror(qapp, ws, canvas):
    """Abrir um terminal nao pode estourar."""
    from tayama.ui import FloatingWindow
    win = FloatingWindow(canvas, term_spec(ws))
    assert win.agent_label.text() == "sleep  ·  Original"


def test_floating_window_sem_workspace_nao_quebra(qapp, ws, canvas):
    """Sem workspace o label mostra so o agente — o sufixo some."""
    from tayama.ui import FloatingWindow
    spec = term_spec(ws); spec["workspace"] = {}
    win = FloatingWindow(canvas, spec)
    assert win.agent_label.text() == "sleep"


def test_refresh_agent_label_reflete_workspace_novo(qapp, ws, canvas):
    """MainWindow.edit_workspace troca win.workspace ao vivo; o label tem de
    acompanhar, senao o nome novo so aparece depois de reiniciar."""
    from tayama.ui import FloatingWindow
    win = FloatingWindow(canvas, term_spec(ws))
    win.workspace = {"id": ws["id"], "name": "Renomeado", "path": ws["path"]}
    win.refresh_agent_label()
    assert win.agent_label.text() == "sleep  ·  Renomeado"


def test_refresh_agent_label_e_idempotente(qapp, ws, canvas):
    """Chamar duas vezes nao pode duplicar o sufixo."""
    from tayama.ui import FloatingWindow
    win = FloatingWindow(canvas, term_spec(ws))
    win.refresh_agent_label(); win.refresh_agent_label()
    assert win.agent_label.text() == "sleep  ·  Original"


def test_ciclo_de_editar_workspace_com_janela_aberta(qapp, ws, canvas):
    """O caminho do main.py: dialogo aceito -> update no disco -> dict da
    janela trocado -> label refrescado. Tudo sem tocar no app inteiro."""
    from tayama import workspaces
    from tayama.ui import FloatingWindow, WorkspaceDialog
    win = FloatingWindow(canvas, term_spec(ws))

    d = WorkspaceDialog(ws)
    d.name.setText("Renomeado")
    nome, path = d.value()
    updated = workspaces.update(ws["id"], nome, path)

    if win.workspace.get("id") == ws["id"]:
        win.workspace = updated
        win.refresh_agent_label()
    assert win.agent_label.text() == "sleep  ·  Renomeado"
    assert updated["id"] == ws["id"], "editar nao pode trocar o id do workspace"
    assert workspaces.load()[0]["name"] == "Renomeado"