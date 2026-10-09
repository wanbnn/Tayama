#!/usr/bin/env python3
"""Rede de seguranca da UI — pega metodo definido na classe ERRADA.

Por que este arquivo existe: a suite era 100% logica pura em workspaces.py e o
app nao abria NENHUM terminal (AttributeError em FloatingWindow.__init__ por
refresh_agent_label ter sido incluido em WorkspaceDialog). 254 testes verdes e
o produto quebrado conviviam. Aqui a UI e de fato instanciada.

Tres camadas, da mais barata pra mais cara:
  1. Estatico: todo self.<metodo>() chamado em ui.py/sidebar.py resolve? Todo
     metodo usado em main.py existe na classe correspondente?
  2. Construtural: um FloatingWindow de verdade monta sem levantar excecao.
  3. Smoke: o MainWindow de verdade monta offscreen, com os signals conectados.

Rodar:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_workspace_smoke.py -v

Sem PyQt6 (ou sem o venv que o tem) os testes PULAM, com o motivo no skip —
nao falham. PyQt6 so esta no .venv do projeto; o python do sistema nao tem.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Offscreen SEMPRE: este arquivo nao pode abrir janela nem exigir X11.
# setdefault e proposital — quem setou antes (ex.: o runner) manda.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# --- PyQt6 tem de vir ANTES do QApplication (restricao do QtWebEngine) ------
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PyQt6.QtWidgets import QApplication

    HAS_QT, _ERR = True, ""
except Exception as exc:  # pragma: no cover
    QApplication = None
    HAS_QT, _ERR = False, f"{type(exc).__name__}: {exc}"

pytestmark = pytest.mark.skipif(
    not HAS_QT,
    reason=(f"precisa de PyQt6 + QtWebEngineWidgets no interpretador. qt={HAS_QT} ({_ERR}). "
            "Rode com .venv/bin/python (o python do sistema nao tem PyQt6)."),
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(["tayama-test"])
    yield app


@pytest.fixture
def home(tmp_path, monkeypatch):
    """TAYAMA_HOME isolado — sem isto o teste escreveria no ~/.tayama do usuario."""
    fake = tmp_path / "tayama-home"
    fake.mkdir()
    monkeypatch.setenv("TAYAMA_HOME", str(fake))
    from tayama import config
    monkeypatch.setattr(config, "DIR", fake)
    monkeypatch.setattr(config, "CFG", fake / "config.json")
    monkeypatch.setattr(config, "SKILLS", fake / "skills")
    config.ensure()
    return fake


# ===========================================================================
# helpers de analise estatica
# ===========================================================================
def classes_do_modulo(path: pathlib.Path):
    """{nome_da_classe: [nomes_de_metodos definidos nela]} via AST."""
    arvore = ast.parse(path.read_text("utf-8"))
    out = {}
    for node in arvore.body:
        if isinstance(node, ast.ClassDef):
            metodos = {n.name for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            out[node.name] = metodos
    return out


def chamadas_self(path: pathlib.Path):
    """{classe: {metodos que o codigo chama via self.}} de um modulo."""
    arvore = ast.parse(path.read_text("utf-8"))
    out = {}
    for node in arvore.body:
        if not isinstance(node, ast.ClassDef):
            continue
        chamadas = set()
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name) and sub.func.value.id == "self"):
                chamadas.add(sub.func.attr)
        out[node.name] = chamadas
    return out


def metodos_de_main():
    """{objeto_em_main: {atributos usados}} — quem main.py chama em quem.

    Parseia 'self.sidebar.refresh()' como sidebar -> {refresh}, e
    'WorkspaceDialog(...)' como WorkspaceDialog -> {}.
    """
    arvore = ast.parse((ROOT / "main.py").read_text("utf-8"))
    main_window = next(n for n in ast.walk(arvore)
                       if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    usados = {}

    def registrar(chave, attr):
        usados.setdefault(chave, set()).add(attr)

    for sub in ast.walk(main_window):
        # self.<algo>.<metodo>(...)
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and isinstance(sub.func.value, ast.Attribute)
                and isinstance(sub.func.value.value, ast.Name)
                and sub.func.value.value.id == "self"):
            registrar(sub.func.value.attr, sub.func.attr)
        # ClasseImportada(...).<metodo>(...)
        elif (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
              and isinstance(sub.func.value, ast.Name)
              and sub.func.value.id in IMPORTA_DE):
            registrar(sub.func.value.id, sub.func.attr)
    return usados


IMPORTA_DE = {"Sidebar", "InfiniteCanvas", "WorkspaceDialog", "NewTerminalDialog",
              "ConfigDialog", "Bridge"}


# ===========================================================================
# 1. estatico: metodo em classe errada
# ===========================================================================
def test_self_calls_resolvem_na_classe_ou_no_pai(qapp):
    """O bug do refresh_agent_label mora aqui.

    Todo self.<x>() chamado dentro de uma classe precisa existir NAQUELA classe
    ou numa classe base real. Sem isto, um metodo colocado na classe vizinha
    (WorkspaceDialog em vez de FloatingWindow) so estoura em runtime — no meio
    do __init__, com o usuario olhando.

    A heranca e resolvida pelo MRO de verdade (importando as classes), nao por
    uma lista escrita a mao: uma lista manual erra assim que uma classe nova
    herda de algo (Edge herda QGraphicsPathItem, nao QFrame).
    """
    from tayama import sidebar, ui

    modulos = {"ui.py": ROOT / "tayama" / "ui.py", "sidebar.py": ROOT / "tayama" / "sidebar.py"}
    reais = {nome: getattr(mod, nome) for mod in (ui, sidebar)
             for nome in dir(mod) if isinstance(getattr(mod, nome), type)}

    verificados = 0
    for rotulo, arquivo in modulos.items():
        chamadas = chamadas_self(arquivo)
        for classe, metodos_chamados in chamadas.items():
            obj = reais.get(classe)
            assert obj is not None, f"{rotulo}: classe {classe} nao encontrada no modulo"
            disponiveis = set(dir(obj))          # inclui a classe E todo o MRO
            faltando = sorted(m for m in metodos_chamados if m not in disponiveis)
            assert not faltando, (
                f"{classe} chama self.{', '.join(faltando)} mas nao define e nao herda. "
                "Provavelmente o metodo foi colocado na classe errada."
            )
            verificados += len(metodos_chamados)
    assert verificados > 20, f"a varredura cobriu pouco demais ({verificados})"


def test_metodos_usados_em_main_existem_na_classe(qapp):
    """main.py usa Sidebar.refresh, WorkspaceDialog.value, ... — tudo tem que existir."""
    from tayama import sidebar, ui
    disponiveis = {}
    for mod in (ui, sidebar):
        for nome in dir(mod):
            obj = getattr(mod, nome)
            if isinstance(obj, type):
                disponiveis[nome] = set(dir(obj))

    faltando = []
    for classe, attrs in sorted(metodos_de_main().items()):
        if classe not in disponiveis:
            continue
        for attr in sorted(attrs):
            if attr not in disponiveis[classe]:
                faltando.append(f"{classe}.{attr}")
    assert not faltando, f"main.py usa metodos inexistentes: {faltando}"


def test_contrato_minimo_de_metodos(qapp):
    """As tres existencia nominales do contrato, uma a uma, com motivo."""
    from tayama.sidebar import Sidebar
    from tayama.ui import FloatingWindow, WorkspaceDialog

    # MainWindow.edit_workspace chama w.refresh_agent_label() em cada janela.
    assert hasattr(FloatingWindow, "refresh_agent_label"), (
        "FloatingWindow.refresh_agent_label ausente: __init__ (ui.py) e "
        "MainWindow.edit_workspace chamam este metodo na FloatingWindow."
    )
    # MainWindow chama d.value() no WorkspaceDialog, em new/edit_workspace.
    assert hasattr(WorkspaceDialog, "value"), "WorkspaceDialog.value ausente"
    # new_workspace/edit_workspace/duplicate_workspace chamam sidebar.refresh().
    assert hasattr(Sidebar, "refresh"), "Sidebar.refresh ausente"


def test_metodo_nao_esta_na_classe_vizinha(qapp):
    """O contralado: nao pode ter vazado para WorkspaceDialog.

    Se refresh_agent_label reaparecer la, o hasattr acima volta a passar por
    acidente enquanto a FloatingWindow continua quebrada.
    """
    from tayama.ui import WorkspaceDialog
    assert not hasattr(WorkspaceDialog, "refresh_agent_label"), (
        "refresh_agent_label esta em WorkspaceDialog — foi de volta para a classe errada."
    )


def test_signals_de_workspace_existem_na_sidebar(qapp):
    """MainWindow conecta estes quatro; sem eles o connect() estoura em runtime."""
    from tayama.sidebar import Sidebar
    for sinal in ("new_agent", "new_workspace", "edit_workspace", "duplicate_workspace"):
        assert hasattr(Sidebar, sinal), f"Sidebar.{sinal} ausente"


# ===========================================================================
# 2. construtural: um FloatingWindow de verdade
# ===========================================================================
def _pty_mock():
    """Patch do PTY.

    ui.py faz 'from .terminal import PtyTerminal' — o nome vive em tayama.ui,
    entao patchar tayama.terminal NAO intercepta nada (e abriria PTYs reais
    durante o teste). Mockeamos no ponto de uso.
    """
    from contextlib import contextmanager
    from unittest import mock

    @contextmanager
    def ctx():
        from PyQt6.QtCore import QObject, pyqtSignal
        from PyQt6.QtWidgets import QWidget
        from tayama import ui

        class FakeTerminal(QWidget):
            """Precisa ser QWidget de verdade: FloatingWindow faz lay.addWidget()."""
            finished = pyqtSignal()
            def __init__(self, argv=None, cwd=None, env=None, parent=None):
                super().__init__(parent)
                self.argv, self.cols, self.rows = argv or [], 80, 24
            def send_text(self, t): pass

        with mock.patch.object(ui, "PtyTerminal", FakeTerminal):
            yield FakeTerminal
    return ctx()


def test_floating_window_monta_de_verdade(qapp, home):
    """Caminho de codigo que o bug impedia: FloatingWindow(canvas, spec)."""
    from tayama.ui import FloatingWindow, InfiniteCanvas

    canvas = InfiniteCanvas()
    spec = {
        "name": "teste",
        "agent": {"name": "Shell", "command": "sleep", "args": ["600"],
                  "auto_briefing": False, "startup_delay_ms": 0},
        "role": {"name": "Tester", "color": "#d6336c"},
        "skills": [{"name": "git"}],
        "workspace": {"id": "ws1", "name": "WS de teste", "path": str(home)},
    }

    with _pty_mock():
        try:
            win = FloatingWindow(canvas, spec)
        except AttributeError as e:
            pytest.fail(
                f"FloatingWindow(canvas, spec) levantou {e!r}. Quase sempre e metodo "
                "chamado no __init__ que foi definido na classe errada."
            )

    assert win.agent_label.text() == "Shell  ·  WS de teste"
    assert win.title.text() == "teste"
    canvas.deleteLater()


def test_refresh_agent_label_reage_ao_rename(qapp, home):
    """Metodo quebrado em silencio ainda: texto do label nao acompanha o rename."""
    from tayama.ui import FloatingWindow, InfiniteCanvas

    canvas = InfiniteCanvas()
    spec = {
        "name": "t",
        "agent": {"name": "Shell", "command": "sleep", "args": ["600"],
                  "auto_briefing": False, "startup_delay_ms": 0},
        "role": None, "skills": [],
        "workspace": {"id": "ws1", "name": "Antes", "path": str(home)},
    }
    with _pty_mock():
        win = FloatingWindow(canvas, spec)

    win.workspace = {"id": "ws1", "name": "Depois", "path": str(home)}
    win.refresh_agent_label()
    assert win.agent_label.text() == "Shell  ·  Depois"

    win.workspace = {}
    win.refresh_agent_label()
    assert win.agent_label.text() == "Shell"
    canvas.deleteLater()


# ===========================================================================
# 3. smoke: o app inteiro
# ===========================================================================
def test_main_window_monta_offscreen(qapp, home):
    """O 'nao abre nenhum terminal' virava 'a janela nem abre' — testamos a montagem."""
    from tayama import workspaces
    workspaces.add("Smoke", str(home))

    import main
    try:
        w = main.MainWindow()
    except Exception as e:  # noqa: BLE001
        if "QtWebEngine" in f"{type(e).__name__}: {e}" or "WebEngine" in repr(e):
            pytest.skip(f"offscreen nao sustenta o Chromium/QtWebEngine aqui: {type(e).__name__}: {e}")
        raise

    assert w.canvas is not None, "canvas nao foi criado"
    assert w.sidebar is not None, "sidebar nao foi criada"
    assert w.centralWidget() is not None

    # os signals de workspace tem de estar conectados a handlers reais.
    # PyQt6 nao expoe QObject.receivers(); disconnect() e o probe: levanta
    # TypeError quando nao ha nada conectado.
    for sinal in ("edit_workspace", "duplicate_workspace", "new_workspace", "new_agent"):
        assert hasattr(w.sidebar, sinal), f"sidebar.{sinal} ausente"
        try:
            getattr(w.sidebar, sinal).disconnect()
        except TypeError:
            pytest.fail(f"sidebar.{sinal} nao tem nenhum slot conectado em MainWindow")

    assert callable(getattr(w, "edit_workspace", None)), "MainWindow.edit_workspace ausente"
    assert callable(getattr(w, "duplicate_workspace", None)), "MainWindow.duplicate_workspace ausente"
    w.deleteLater()


def test_duplicate_workspace_pela_ui_atualiza_a_sidebar(qapp, home):
    """O caminho do main.py: sidebar -> duplicate_workspace -> lista re-renderizada."""
    from unittest import mock

    from tayama import workspaces
    original = workspaces.add("Smoke", str(home))
    antes = workspaces.load()

    import main
    with mock.patch.object(main.WorkspaceDialog, "exec", return_value=False):
        w = main.MainWindow()
        try:
            w.duplicate_workspace(original)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"MainWindow.duplicate_workspace levantou {type(e).__name__}: {e}")
        depois = workspaces.load()
        assert len(depois) == len(antes) + 1, "a UI nao duplicou o workspace"
        assert any(x["id"] != original["id"] for x in depois)
        w.deleteLater()


def test_edit_workspace_renomeia_pela_ui(qapp, home):
    """O caminho do rename: d.value() -> workspaces.update, mantendo o id."""
    from unittest import mock

    from tayama import workspaces
    original = workspaces.add("Velho", str(home))

    import main
    with mock.patch.object(main.WorkspaceDialog, "exec", return_value=True), \
         mock.patch.object(main.WorkspaceDialog, "value",
                           return_value=("Novo nome", "/tmp/novo")):
        w = main.MainWindow()
        try:
            w.edit_workspace(original)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"MainWindow.edit_workspace levantou {type(e).__name__}: {e}")
        achado = [x for x in workspaces.load() if x["id"] == original["id"]]
        assert len(achado) == 1, "update pela UI nao preservou o id"
        assert achado[0]["name"] == "Novo nome"
        assert achado[0]["path"] == "/tmp/novo"
        w.deleteLater()


def test_config_do_teste_nao_vaza_para_a_home_real(qapp, home):
    """Sanidade: o TAYAMA_HOME isolado realmente segurou a escrita."""
    real = pathlib.Path(os.path.expanduser("~/.tayama"))
    antes = set(os.listdir(real)) if real.is_dir() else set()
    from tayama import workspaces
    workspaces.add("Isolado", str(home))
    depois = set(os.listdir(real)) if real.is_dir() else set()
    assert depois == antes, f"o teste escreveu na home real: {depois - antes}"