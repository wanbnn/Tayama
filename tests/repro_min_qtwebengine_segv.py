#!/usr/bin/env python3
"""REPRO MINIMO do SIGSEGV — nao usa NADA do Tayama.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/repro_min_qtwebengine_segv.py

Se isso crashar, o bug e do PyQt6 6.11/QtWebEngine neste ambiente, nao do tayama/ui.py.
"""
import sys
sys.path.insert(0, "/home/wanbnn/Documentos/Docs/dok/tayama_git")
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QUrl
app = QApplication(["t"])
p = QWebEngineView()
from PyQt6.QtWebEngineCore import QWebEnginePage
pg = QWebEnginePage(p); p.setPage(pg)
p.load(QUrl(sys.argv[1] if len(sys.argv)>1 else "about:blank"))
app.processEvents()
print("etapa 1: view criado e carregado", flush=True)
p.deleteLater()
app.processEvents()                      # <-- aqui
print("ETAPA 2 ALCANCADA", flush=True)
