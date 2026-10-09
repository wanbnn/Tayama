"""Sidebar de workspaces: cada workspace define a pasta de trabalho; agentes aparecem agrupados."""
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
                             QTreeWidgetItem, QMenu, QMessageBox)
from . import workspaces
from .icons import icon, style_button


class Sidebar(QWidget):
    new_agent = pyqtSignal(object)      # workspace dict
    new_workspace = pyqtSignal()
    edit_workspace = pyqtSignal(object)     # workspace dict
    duplicate_workspace = pyqtSignal(object)  # workspace dict

    def __init__(self, canvas, parent=None):
        super().__init__(parent); self.canvas = canvas; self.setObjectName("sidebar"); self.setMinimumWidth(0)
        v = QVBoxLayout(self); v.setContentsMargins(12, 12, 12, 12); v.setSpacing(8)
        head = QHBoxLayout(); t = QLabel("WORKSPACES"); t.setStyleSheet("color:#8b949e;font-weight:700;font-size:8.5pt;letter-spacing:1px;")
        head.addWidget(t); head.addStretch(); v.addLayout(head)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setIndentation(14); self.tree.setExpandsOnDoubleClick(False)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.tree.setFrameShape(QTreeWidget.Shape.NoFrame)
        self.tree.customContextMenuRequested.connect(self._menu); self.tree.itemDoubleClicked.connect(self._open)
        # Clique simples = "me mostra aquele workspace". Duplo clique continua
        # sendo "me leva até aquele nó" (_open). São dois gestos diferentes: um
        # troca o canvas, o outro centraliza num terminal específico.
        self.tree.itemClicked.connect(self._select)
        self.empty = QLabel("Crie um workspace para definir a pasta de trabalho dos seus agentes."); self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color:#6e7681;padding:6px;")
        v.addWidget(self.empty); v.addWidget(self.tree, 1)
        b = QPushButton(); style_button(b, "folder-plus-outline", "+", color="#ffffff"); b.setText(" Novo workspace"); b.clicked.connect(self.new_workspace.emit); v.addWidget(b)
        canvas.changed.connect(self.refresh); self.refresh()

    def refresh(self):
        open_ids = {self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[1]["id"]
                    for i in range(self.tree.topLevelItemCount()) if self.tree.topLevelItem(i).isExpanded()}
        first = self.tree.topLevelItemCount() == 0
        self.tree.clear(); wss = workspaces.load()
        self.empty.setVisible(not wss); self.tree.setVisible(bool(wss))
        for ws in wss:
            wins = [w for w in self.canvas.windows.values() if w.workspace.get("id") == ws["id"]]
            # painéis do mesmo workspace entram na mesma árvore, depois dos terminais
            pans = [p for p in self.canvas.panels.values() if (getattr(p, "workspace", None) or {}).get("id") == ws["id"]]
            it = QTreeWidgetItem([f"{ws['name']}   {len(wins) + len(pans)}" if wins or pans else ws["name"]])
            it.setIcon(0, icon("folder-outline", "#e5c07b")); it.setToolTip(0, ws["path"]); it.setData(0, Qt.ItemDataRole.UserRole, ("ws", ws))
            # Marca o workspace que está no canvas: sem isso não há como saber
            # em qual ambiente se está — todas as árvores parecem iguais.
            if ws["id"] == (self.canvas.current_ws or {}).get("id"):
                f = it.font(0); f.setBold(True); it.setFont(0, f)
                it.setToolTip(0, f"{ws['path']}  ·  no canvas atual")
            self.tree.addTopLevelItem(it)
            for w in wins:
                c = QTreeWidgetItem([f"{w.name}  ·  {w.role_name}"])
                c.setIcon(0, icon("circle", w.role["color"] if w.role else "#6e7681")); c.setToolTip(0, f"{w.agent['name']} — {ws['path']}")
                c.setData(0, Qt.ItemDataRole.UserRole, ("win", w)); it.addChild(c)
            for p in pans:
                c = QTreeWidgetItem([f"{p.name}  ·  painel web"])
                c.setIcon(0, icon("circle", p.role["color"])); c.setToolTip(0, f"{p.url.text().strip() or 'about:blank'} — {ws['name']}")
                c.setData(0, Qt.ItemDataRole.UserRole, ("panel", p)); it.addChild(c)
            it.setExpanded(first or ws["id"] in open_ids)

    def _select(self, item, _=0):
        """Clique simples: leva ao canvas daquele workspace.

        Ler os dados ANTES de qualquer troca — set_current_ws emite `changed`, e
        a Sidebar.refresh() faz tree.clear() dentro deste handler, deixando o
        `item` pendurado.
        """
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data: return
        kind, obj = data
        ws = obj if kind == "ws" else (getattr(obj, "workspace", None) or None)
        if not ws: return
        if ws.get("id") == (self.canvas.current_ws or {}).get("id"): return
        # Adiado para fora do handler do Qt: trocar a cena agora rebuildaria a
        # árvore no meio do clique.
        QTimer.singleShot(0, lambda ws=ws: self.canvas.set_current_ws(ws))

    def _open(self, item, _=0):
        kind, obj = item.data(0, Qt.ItemDataRole.UserRole)
        if kind in ("win", "panel"): self.focus(obj)
        else: item.setExpanded(not item.isExpanded())

    def focus(self, win):
        """Traz um nó para o centro da tela.

        Troca a cena ANTES de centralizar: centerOn() num item de outra cena
        não levanta — ele centraliza em coordenadas sem sentido, em silêncio
        (medido: centralizou (1299,1249) para um proxy em (1100,1100)).
        """
        ws = getattr(win, "workspace", None)
        if ws and ws.get("id") != (self.canvas.current_ws or {}).get("id"):
            self.canvas.set_current_ws(ws)
        self.canvas._z = getattr(self.canvas, "_z", 0) + 1; win.proxy.setZValue(self.canvas._z)
        self.canvas.centerOn(win.proxy)
        # painéis não têm terminal — focar o frame evita AttributeError
        if getattr(win, "terminal", None): win.terminal.setFocus()

    def _menu(self, pos):
        item = self.tree.itemAt(pos); m = QMenu(self)
        if item is None: act = {m.addAction(icon("folder-plus-outline", "#c9d1d9"), "Novo workspace"): ("nw", None)}
        else:
            kind, obj = item.data(0, Qt.ItemDataRole.UserRole)
            if kind == "ws":
                act = {m.addAction(icon("plus", "#c9d1d9"), "Novo agente aqui"): ("na", obj),
                       m.addAction(icon("pencil", "#c9d1d9"), "Editar"): ("ed", obj),
                       m.addAction(icon("copy", "#c9d1d9"), "Duplicar"): ("dup", obj),
                       m.addAction(icon("trash-can-outline", "#f85149"), "Remover workspace"): ("rm", obj)}
            else:
                if kind == "panel":
                    act = {m.addAction(icon("target", "#c9d1d9"), "Ir até o painel"): ("go", obj),
                           m.addAction(icon("close", "#f85149"), "Fechar painel"): ("cp", obj)}
                else:
                    act = {m.addAction(icon("target", "#c9d1d9"), "Ir até o terminal"): ("go", obj),
                           m.addAction(icon("clipboard-text-outline", "#c9d1d9"), "Reenviar briefing"): ("br", obj),
                           m.addAction(icon("close", "#f85149"), "Fechar agente"): ("cl", obj)}
        chosen = m.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen not in act: return
        kind, obj = act[chosen]
        if kind == "nw": self.new_workspace.emit()
        elif kind == "na": self.new_agent.emit(obj)
        elif kind == "go": self.focus(obj)
        elif kind == "br": obj.send_briefing()
        elif kind == "cl": obj.close_window()
        elif kind == "cp": obj.close_panel()
        elif kind == "rm": self._remove(obj)
        elif kind == "ed": self.edit_workspace.emit(obj)
        elif kind == "dup": self.duplicate_workspace.emit(obj)

    def _remove(self, ws):
        wins = [w for w in self.canvas.windows.values() if w.workspace.get("id") == ws["id"]]
        pans = [p for p in self.canvas.panels.values() if (getattr(p, "workspace", None) or {}).get("id") == ws["id"]]
        n = len(wins) + len(pans)
        if QMessageBox.question(self, "Remover workspace", f"Remover '{ws['name']}'" + (f" e fechar {n} janela(s)?" if n else "?")) \
                != QMessageBox.StandardButton.Yes: return
        for w in wins: w.close_window()
        for p in pans: p.close_panel()
        # Antes de workspaces.remove: o canvas precisa soltar a cena e o
        # enquadramento do workspace, senão ficariam em canvas.scenes para
        # sempre, vazios e segurando memória.
        self.canvas.forget_workspace(ws["id"])
        workspaces.remove(ws["id"]); self.refresh()
