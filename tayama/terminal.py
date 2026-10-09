"""Terminal PTY com renderização própria (cores, estilos, cursor, tela alternativa, mouse, seleção)."""
import os, sys, re, math, copy, struct, select
from collections import defaultdict
import pyte
from pyte import modes as mo
from PyQt6.QtCore import Qt, QSocketNotifier, QTimer, QPointF, QRect, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetricsF, QPainter, QColor, QPen
from PyQt6.QtWidgets import QWidget, QApplication

if sys.platform != "win32":
    import pty, fcntl, termios, signal

FG, BG, PAD = "#d4d4d4", "#121212", 6
ANSI = dict(black="#1d1f21", red="#e06c75", green="#98c379", brown="#e5c07b", blue="#61afef", magenta="#c678dd",
            cyan="#56b6c2", white="#c5c8c6", brightblack="#6b7280", brightred="#ff7b86", brightgreen="#b5e08c",
            brightbrown="#f5d58a", brightblue="#7cc0ff", brightmagenta="#e08cf5", brightcyan="#6fd3e0", brightwhite="#ffffff")
STRIP = re.compile(rb"\x1b\[[>=<][0-9;:]*[A-Za-z]")   # protocolos de teclado que o pyte confundiria com SGR
K = Qt.Key
CURS = {K.Key_Up.value: "A", K.Key_Down.value: "B", K.Key_Right.value: "C", K.Key_Left.value: "D", K.Key_Home.value: "H", K.Key_End.value: "F"}
TILDE = {K.Key_Delete.value: 3, K.Key_Insert.value: 2, K.Key_PageUp.value: 5, K.Key_PageDown.value: 6}
FKEYS = {K.Key_F1.value: "\x1bOP", K.Key_F2.value: "\x1bOQ", K.Key_F3.value: "\x1bOR", K.Key_F4.value: "\x1bOS",
         **{K.Key_F5.value + i: f"\x1b[{n}~" for i, n in enumerate((15, 17, 18, 19, 20, 21, 23, 24))}}


class Screen(pyte.HistoryScreen):
    """HistoryScreen + tela alternativa (1049) + modos privados + respostas ao app (DA, CPR)."""
    alt = False

    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.priv = set(); self._main = None; self.on_reply = lambda b: None

    def write_process_input(self, data): self.on_reply(data.encode())

    def _alt(self, on):
        if on == self.alt: return
        self.alt = on
        if on:
            self._main = (self.buffer, copy.copy(self.cursor))
            self.buffer = defaultdict(self.buffer.default_factory); self.cursor_position()
        elif self._main:
            self.buffer, self.cursor = self._main
        self.dirty.update(range(self.lines))

    def set_mode(self, *modes, **kw):
        if kw.get("private"):
            self.priv.update(modes)
            if {47, 1047, 1049} & set(modes): self._alt(True)
        super().set_mode(*modes, **kw)

    def reset_mode(self, *modes, **kw):
        if kw.get("private"):
            self.priv.difference_update(modes)
            if {47, 1047, 1049} & set(modes): self._alt(False)
        super().reset_mode(*modes, **kw)

    def index(self):
        pyte.Screen.index(self) if self.alt else super().index()


class PtyTerminal(QWidget):
    finished = pyqtSignal()
    FAMILIES = ["JetBrains Mono", "Fira Code", "Cascadia Mono", "DejaVu Sans Mono", "Noto Sans Mono", "Monospace"]
    SIZE = 10.5

    def __init__(self, argv, cwd=None, env=None, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus); self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        self.setCursor(Qt.CursorShape.IBeamCursor); self.setMinimumSize(200, 80)
        base = QFont(); base.setFamilies(self.FAMILIES); base.setPointSizeF(self.SIZE); base.setStyleHint(QFont.StyleHint.Monospace)
        self.fonts = {}
        for b in (0, 1):
            for i in (0, 1):
                f = QFont(base); f.setBold(bool(b)); f.setItalic(bool(i)); self.fonts[b, i] = f
        fm = QFontMetricsF(base)
        self.cw, self.ch = fm.horizontalAdvance("M"), math.ceil(fm.lineSpacing())
        self.asc = fm.ascent() + (self.ch - fm.height()) / 2
        self.cols, self.rows = 80, 24
        self.screen = Screen(self.cols, self.rows, history=2000); self.screen.on_reply = self.write
        self.stream = pyte.ByteStream(self.screen)
        self.fd = self.pid = self.notifier = None
        self.scroll_off = 0; self._sel = None; self._btn = None; self._colors = {}
        self._timer = QTimer(self, singleShot=True, interval=16, timeout=self.update)
        if sys.platform == "win32": return
        self._spawn(argv, cwd, env or os.environ.copy())

    # --- processo ------------------------------------------------------
    def _spawn(self, argv, cwd, env):
        pid, fd = pty.fork()
        if pid == 0:
            try:
                if cwd: os.chdir(cwd)
                os.execvpe(argv[0], argv, env)
            except Exception as e:
                os.write(2, f"Falha ao executar '{argv[0]}': {e}\r\n".encode()); os._exit(127)
        self.pid, self.fd = pid, fd
        fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)
        self.notifier = QSocketNotifier(fd, QSocketNotifier.Type.Read, self); self.notifier.activated.connect(self._read)

    def _read(self):
        try:
            while True:
                data = os.read(self.fd, 65536)
                if not data: raise OSError
                self.stream.feed(STRIP.sub(b"", data)); self._timer.start() if not self._timer.isActive() else None
        except BlockingIOError: pass
        except OSError:
            self.notifier.setEnabled(False); self.stream.feed(b"\r\n\x1b[2m[processo encerrado]\x1b[0m\r\n"); self.update(); self.finished.emit()

    def write(self, data: bytes):
        if self.fd is None: return
        view = memoryview(data)
        while view:
            try: view = view[os.write(self.fd, view):]
            except BlockingIOError: select.select([], [self.fd], [], 1)
            except OSError: return

    def send_text(self, text: str, submit=True):
        self.write(b"\x1b[200~" + text.encode() + b"\x1b[201~")
        if submit: QTimer.singleShot(250, lambda: self.write(b"\r"))

    def terminate(self):
        if self.notifier: self.notifier.setEnabled(False)
        if self.pid:
            try: os.kill(self.pid, signal.SIGHUP); os.waitpid(self.pid, os.WNOHANG)
            except OSError: pass
        if self.fd is not None:
            try: os.close(self.fd)
            except OSError: pass
        self.fd = self.pid = None

    # --- tamanho -------------------------------------------------------
    def resizeEvent(self, e):
        cols, rows = max(20, int((self.width() - 2 * PAD) // self.cw)), max(4, int((self.height() - 2 * PAD) // self.ch))
        if (cols, rows) == (self.cols, self.rows): return
        self.cols, self.rows = cols, rows; self.screen.resize(rows, cols)
        if self.fd is not None:
            try: fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            except OSError: pass
        self.update()

    # --- desenho -------------------------------------------------------
    def _col(self, name, default):
        if name == "default": return default
        c = self._colors.get(name)
        if c is None:
            c = self._colors[name] = QColor(ANSI.get(name) or ("#" + name if re.fullmatch(r"[0-9a-fA-F]{6}", name) else default.name()))
        return c

    def _lines(self):
        s = self.screen; body = [s.buffer[y] for y in range(s.lines)]
        return body if s.alt else list(s.history.top) + body

    def paintEvent(self, ev):
        p = QPainter(self); fg0, bg0 = QColor(FG), QColor(BG); p.fillRect(self.rect(), bg0)
        s = self.screen; allv = self._lines(); n = len(allv)
        off = 0 if s.alt else min(self.scroll_off, max(0, n - s.lines)); base = n - s.lines - off
        sel = self._sel_norm(); focus = self.hasFocus()
        cur = (s.cursor.x, s.cursor.y) if off == 0 and mo.DECTCEM in s.mode else None
        cw, ch = self.cw, self.ch; last = None
        for r in range(min(s.lines, len(allv) - base)):
            line = allv[base + r]; y = PAD + r * ch
            for x in range(s.columns):
                c = line[x]; fg, bg = self._col(c.fg, fg0), self._col(c.bg, bg0)
                if c.reverse: fg, bg = bg, fg
                iscur = cur == (x, r)
                if sel and sel[0] <= (base + r, x) <= sel[1]: bg = QColor("#2f4f7f")
                if iscur and focus: fg, bg = bg0, QColor("#e6edf3")
                px = PAD + x * cw
                if bg != bg0: p.fillRect(QRect(int(px), y, math.ceil(cw) + 1, ch), bg)
                if iscur and not focus: p.setPen(QPen(QColor("#e6edf3"), 1)); p.drawRect(QRect(int(px), y, int(cw) - 1, ch - 1))
                d = c.data
                if d and d != " ":
                    if (fg, c.bold, c.italics) != last: p.setPen(fg); p.setFont(self.fonts[bool(c.bold), bool(c.italics)]); last = (fg, c.bold, c.italics)
                    p.drawText(QPointF(px, y + self.asc), d)
                if c.underscore: p.setPen(fg); last = None; p.drawLine(int(px), y + ch - 2, int(px + cw), y + ch - 2)
                if c.strikethrough: p.setPen(fg); last = None; p.drawLine(int(px), y + ch // 2, int(px + cw), y + ch // 2)
        if off: p.setPen(QColor("#e5c07b")); p.setFont(self.fonts[1, 0]); p.drawText(self.width() - 90, 16, f"↑ {off} linhas")

    # --- seleção / cópia ----------------------------------------------
    def _cell(self, pos): return max(0, int((pos.x() - PAD) // self.cw)), max(0, int((pos.y() - PAD) // self.ch))

    def _abs(self, pos):
        x, y = self._cell(pos); n = len(self._lines()); off = 0 if self.screen.alt else self.scroll_off
        return n - self.screen.lines - off + min(y, self.screen.lines - 1), min(x, self.screen.columns - 1)

    def _sel_norm(self):
        if not self._sel or self._sel[0] == self._sel[1]: return None
        return tuple(sorted(self._sel))

    def selected_text(self):
        sel = self._sel_norm()
        if not sel: return ""
        allv = self._lines(); out = []
        for i in range(sel[0][0], sel[1][0] + 1):
            if i >= len(allv): break
            a = sel[0][1] if i == sel[0][0] else 0; b = sel[1][1] if i == sel[1][0] else self.screen.columns - 1
            out.append("".join(allv[i][x].data for x in range(a, b + 1)).rstrip())
        return "\n".join(out)

    def copy(self):
        t = self.selected_text()
        if t: QApplication.clipboard().setText(t)

    # --- mouse ---------------------------------------------------------
    def _tracking(self, e=None):
        shift = e is not None and bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        return bool(self.screen.priv & {1000, 1002, 1003}) and not shift

    def _report(self, b, pos, press=True):
        x, y = self._cell(pos); self.write(f"\x1b[<{b};{x + 1};{y + 1}{'M' if press else 'm'}".encode())

    def mousePressEvent(self, e):
        self.setFocus(); bm = {Qt.MouseButton.LeftButton: 0, Qt.MouseButton.MiddleButton: 1, Qt.MouseButton.RightButton: 2}.get(e.button())
        if self._tracking(e) and bm is not None: self._btn = bm; self._report(bm, e.position()); return
        if e.button() == Qt.MouseButton.LeftButton: a = self._abs(e.position()); self._sel = (a, a); self._btn = 0; self.update()
        elif e.button() == Qt.MouseButton.MiddleButton:
            self.send_text(QApplication.clipboard().text(QApplication.clipboard().Mode.Selection), submit=False)

    def mouseMoveEvent(self, e):
        if self._btn is None: return
        if self._tracking(e):
            if self.screen.priv & {1002, 1003}: self._report(self._btn + 32, e.position())
        elif self._sel: self._sel = (self._sel[0], self._abs(e.position())); self.update()

    def mouseReleaseEvent(self, e):
        if self._btn is None: return
        if self._tracking(e): self._report(self._btn, e.position(), False)
        else: self.copy()
        self._btn = None

    def wheelEvent(self, e):
        up = e.angleDelta().y() > 0
        if self._tracking(e): self._report(64 if up else 65, e.position())
        elif self.screen.alt: self.write((b"\x1b[A" if up else b"\x1b[B") * 3)
        else: self.scroll_off = max(0, self.scroll_off + (3 if up else -3)); self.update()
        e.accept()

    # --- teclado -------------------------------------------------------
    def focusNextPrevChild(self, nxt): return False

    def focusInEvent(self, e):
        super().focusInEvent(e); self.update()
        if 1004 in self.screen.priv: self.write(b"\x1b[I")

    def focusOutEvent(self, e):
        super().focusOutEvent(e); self.update()
        if 1004 in self.screen.priv: self.write(b"\x1b[O")

    def inputMethodEvent(self, e):
        if e.commitString(): self.write(e.commitString().encode()); self.scroll_off = 0
        e.accept()

    def inputMethodQuery(self, q):
        if q == Qt.InputMethodQuery.ImCursorRectangle:
            return QRect(int(PAD + self.screen.cursor.x * self.cw), PAD + self.screen.cursor.y * self.ch, int(self.cw), self.ch)
        return None

    def keyPressEvent(self, e):
        k, m, t = e.key(), e.modifiers(), e.text()
        ctrl, shift, alt = (bool(m & f) for f in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.AltModifier))
        if ctrl and shift and k == K.Key_V.value: self.send_text(QApplication.clipboard().text(), submit=False); return
        if ctrl and shift and k == K.Key_C.value: self.copy(); return
        self.scroll_off = 0; mod = 1 + shift + 2 * alt + 4 * ctrl; esc = b"\x1b" if alt else b""
        if k in CURS:
            self.write((f"\x1b[1;{mod}{CURS[k]}" if mod > 1 else ("\x1bO" if 1 in self.screen.priv else "\x1b[") + CURS[k]).encode())
        elif k in TILDE: self.write((f"\x1b[{TILDE[k]};{mod}~" if mod > 1 else f"\x1b[{TILDE[k]}~").encode())
        elif k in FKEYS: self.write(FKEYS[k].encode())
        elif k in (K.Key_Return.value, K.Key_Enter.value): self.write(b"\n" if shift else esc + b"\r")  # Shift+Enter = nova linha
        elif k == K.Key_Backspace.value: self.write(esc + b"\x7f")
        elif k == K.Key_Tab.value: self.write(b"\t")
        elif k == K.Key_Backtab.value: self.write(b"\x1b[Z")
        elif k == K.Key_Escape.value: self.write(b"\x1b")
        elif ctrl and k == K.Key_Space.value: self.write(b"\x00")
        elif ctrl and 0x41 <= k <= 0x5A: self.write(esc + bytes([k - 0x40]))
        elif t and t.isprintable(): self.write(esc + t.encode())
