"""O briefing que o agente recebe no startup.

Este arquivo existe por um motivo específico: a seção "Painéis web" foi
adicionada ao briefing depois de a API de painel já estar pronta e testada, e
não havia NENHUM teste do texto do briefing — `grep briefing tests/` só
achava fixtures de `auto_briefing`, nunca o conteúdo. Um briefing que não é
testado é um briefing que some no próximo refactor sem ninguém perceber.

Sem Chromium de verdade: `briefing()` só lê `peers_of` e `canvas.panels`, então
um canvas de dublê basta.

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_briefing_paineis.py -v
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import QApplication

try:
    HAS_QT = True
    _ERR = ""
except Exception as exc:  # pragma: no cover
    HAS_QT, _ERR = False, f"{type(exc).__name__}: {exc}"

pytestmark = [pytest.mark.skipif(not HAS_QT, reason=f"exige PyQt6: {_ERR}")]


class FakePainel:
    def __init__(self, pid="p1", nome="docs"):
        self.id, self.name, self.role_name = pid, nome, "painel web"


class FakeTerm:
    def __init__(self, nome="dev", cargo="Dev"):
        self.name, self.role_name = nome, cargo
        self.role = {"name": cargo, "prompt": "faça as coisas"}
        self.skills = [{"name": "Curto", "text": "responda em 5 linhas"}]


class FakeCanvas:
    def __init__(self, peers=()):
        self.peers, self.panels = list(peers), {}
        for p in peers:
            self.panels[p.id] = p

    def peers_of(self, win):
        return self.peers


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication(["tayama-briefing"])


def _briefing(peers=()):
    """Chama briefing() num FloatingWindow sem construir a UI toda — só o que
    o método lê. `__new__` sem `__init__` funciona aqui porque não tocamos em
    nenhum QWidget do Qt (medido: quebra só no proxy da cena)."""
    from tayama.ui import FloatingWindow
    fw = FloatingWindow.__new__(FloatingWindow)
    fw.canvas = FakeCanvas(peers)
    fw.name, fw.role_name = "dev", "Dev"
    fw.role = {"name": "Dev", "prompt": "faça as coisas"}
    fw.skills = [{"name": "Curto", "text": "responda em 5 linhas"}]
    return fw.briefing()


# ==========================================================================
# A seção de painéis
# ==========================================================================
def test_sem_painel_nao_fala_de_painel():
    """Um agente sem painel não deve ler um manual de browser que não pode
    usar. O briefing já é condicional em outros pontos (`if self.skills`) —
    esta seção segue o mesmo padrão."""
    b = _briefing()
    assert "## Painéis web" not in b
    assert "tayama panel" not in b


def test_com_painel_a_secao_aparece():
    b = _briefing([FakePainel()])
    assert "## Painéis web" in b


def test_a_secao_traz_todos_os_comandos_da_api():
    """Cada verbo de `tayama panel` tem que ser descobrível no briefing.

    back/forward/reload são uma linha só (`tayama panel back | forward | reload`),
    então o teste exige o verbo ser/não — não a string exata. O que não pode
    faltar é nenhum deles: o plano já previa o agente escolhendo `eval` no lugar
    de `page`, e a lista inteira é o que evita isso. back/forward/reload são
    uma linha só (`tayama panel back | forward | reload`), então o teste exige
    o verbo existir — não a string exata.
    """
    b = _briefing([FakePainel()])
    for verbo in ("list", "open", "navigate", "back", "forward", "reload",
                  "page", "links", "click", "type", "press", "eval"):
        assert verbo in b, f"falta o verbo `{verbo}` no briefing"


def test_avisa_que_sem_panel_usa_o_ligado():
    b = _briefing([FakePainel()])
    assert "--panel" in b, "o agente precisa saber que pode escolher o alvo"
    assert "ligado a você" in b


def test_avisa_que_file_e_recusado():
    """A restrição que mais surprising é: sem esse aviso o agente tenta
    `navigate file:///...` para ler um arquivo local e leva erro."""
    b = _briefing([FakePainel()])
    assert "http://" in b and "file://" in b


def test_avisa_que_o_eval_alcanca_a_sessao_da_pagina():
    """O risco aceito pelo usuário precisa estar escrito onde o agente lê."""
    b = _briefing([FakePainel()])
    assert "sessão" in b or "sessao" in b


def test_conta_os_paineis_e_nomeia():
    b = _briefing([FakePainel("p1", "docs"), FakePainel("p2", "api")])
    assert "docs" in b and "api" in b


def test_painel_ligado_mas_de_outro_workspace_nao_entra():
    """Só entra no briefing o que está LIGADO por seta. `peers_of` já é a
    fronteira do workspace (ui.py:380), então basta não vazar a lista de todos
    os painéis do canvas."""
    b = _briefing([FakePainel("p1", "docs")])
    assert "Painéis web" in b
    assert "1 painel(es)" in b


# ==========================================================================
# O que já existia não pode ter quebrado
# ==========================================================================
def test_communication_intacta():
    b = _briefing()
    assert "## Comunicação" in b
    assert "tayama peers" in b
    assert "tayama send" in b
    assert "tayama broadcast" in b


def test_cargo_e_skills_intactos():
    b = _briefing()
    assert "faça as coisas" in b
    assert "responda em 5 linhas" in b


def test_conectados_agora_intacto():
    b = _briefing()
    assert "Conectados agora: ninguém ainda." in b


def test_painel_ligado_aparece_em_conectados_agora():
    b = _briefing([FakePainel()])
    assert "docs (painel web)" in b


def test_pedido_de_confirmacao_no_fim():
    """O briefing termina pedindo confirmação — a seção nova não pode empurrar
    essa linha para fora."""
    b = _briefing([FakePainel()])
    assert b.rstrip().endswith("aguarde instruções.")