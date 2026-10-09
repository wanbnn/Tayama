"""Servidor local: permite que os agentes usem `tayama` para falar com terminais conectados."""
import json
from PyQt6.QtCore import QObject
from PyQt6.QtNetwork import QLocalServer
from . import config


class Bridge(QObject):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        QLocalServer.removeServer(config.SOCK)
        self.server = QLocalServer(self)
        self.server.listen(config.SOCK)
        self.server.newConnection.connect(self._new)

    def _new(self):
        sock = self.server.nextPendingConnection(); buf = bytearray()

        def ready():
            buf.extend(bytes(sock.readAll()))
            if b"\n" not in buf: return
            try: resp = self.handle(json.loads(buf.decode()))
            except Exception as e: resp = {"ok": False, "error": str(e)}
            sock.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
            sock.flush(); sock.disconnectFromServer()
        sock.readyRead.connect(ready)

    def handle(self, req):
        src = self.canvas.windows.get(req.get("from"))
        if not src: return {"ok": False, "error": "terminal de origem desconhecido (rode dentro do Tayama)"}
        peers = self.canvas.peers_of(src)
        info = [{"name": p.name, "role": p.role_name, "agent": p.agent["name"]} for p in peers]
        cmd = req.get("cmd")
        if cmd == "peers":
            return {"ok": True, "peers": info}
        if cmd == "broadcast":
            targets = peers
        elif cmd == "send":
            t = req.get("target", "").lower()
            targets = [p for p in peers if t in (p.name.lower(), p.role_name.lower())]
        else:
            return {"ok": False, "error": f"comando inválido: {cmd}"}
        if not targets:
            return {"ok": False, "error": "nenhum destino conectado corresponde", "peers": info}
        for p in targets: p.receive(src, req.get("text", ""))
        return {"ok": True, "sent_to": [p.name for p in targets]}
