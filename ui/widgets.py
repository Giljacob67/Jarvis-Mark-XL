"""Reusable UI widgets for MARK XL: Log, History, Memory Editor, File Drop Zone."""
from __future__ import annotations

import json
import math
import platform
import random
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ui.constants import C, _OS, qcol

# ---------------------------------------------------------------------------
# File type icons and extension mapping
# ---------------------------------------------------------------------------

_FILE_ICONS = {
    "image":   ("\U0001f5bc", "#00d4ff"), "video":   ("\U0001f3ac", "#ff6b00"),
    "audio":   ("\U0001f3b5", "#cc44ff"), "pdf":     ("\U0001f4c4", "#ff4444"),
    "word":    ("\U0001f4dd", "#4488ff"), "excel":   ("\U0001f4ca", "#44bb44"),
    "code":    ("\U0001f4bb", "#ffcc00"), "archive": ("\U0001f4e6", "#ff8844"),
    "pptx":    ("\U0001f4ca", "#ff6622"), "text":    ("\U0001f4c3", "#aaaaaa"),
    "data":    ("\U0001f527", "#88ddff"), "unknown": ("\U0001f4ce", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "svg", "ico"], "image"),
    **dict.fromkeys(["mp4", "avi", "mov", "mkv", "wmv", "flv", "webm", "m4v"], "video"),
    **dict.fromkeys(["mp3", "wav", "ogg", "m4a", "aac", "flac", "wma", "opus"], "audio"),
    **dict.fromkeys(["pdf"], "pdf"),
    **dict.fromkeys(["doc", "docx"], "word"),
    **dict.fromkeys(["xls", "xlsx", "ods"], "excel"),
    **dict.fromkeys(["ppt", "pptx"], "pptx"),
    **dict.fromkeys(["py", "js", "ts", "jsx", "tsx", "html", "css", "java", "c", "cpp",
                     "cs", "go", "rs", "rb", "php", "swift", "kt", "sh", "sql", "lua"], "code"),
    **dict.fromkeys(["zip", "rar", "tar", "gz", "7z", "bz2", "xz"], "archive"),
    **dict.fromkeys(["txt", "md", "rst", "log"], "text"),
    **dict.fromkeys(["csv", "tsv", "json", "xml"], "data"),
}


def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024**2:
        return f"{size / 1024:.1f} KB"
    elif size < 1024**3:
        return f"{size / 1024**2:.1f} MB"
    else:
        return f"{size / 1024**3:.1f} GB"


# ---------------------------------------------------------------------------
# LogWidget — streaming typewriter-style log
# ---------------------------------------------------------------------------

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 4px;
                padding: 6px; selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 8px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 4px; min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing = False
        self._text = ""
        self._pos = 0
        self._tag = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text = self._queue.pop(0)
        self._pos = 0
        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)


# ---------------------------------------------------------------------------
# HistoryWidget — conversation history panel
# ---------------------------------------------------------------------------

class HistoryWidget(QTextEdit):
    _sig = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 8))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL}; color: {C.TEXT};
                border: none; padding: 4px;
            }}
            QScrollBar:vertical {{ background: {C.BG}; width: 8px; border: none; }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 4px; min-height: 20px;
            }}
        """)
        self._sig.connect(self._add_turn)

    def add_turn(self, role: str, text: str):
        self._sig.emit(role, text)

    def _add_turn(self, role: str, text: str):
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        lbl_fmt = cur.charFormat()
        lbl_fmt.setForeground(QBrush(qcol(C.TEXT_DIM)))
        txt_fmt = cur.charFormat()
        if role == "user":
            txt_fmt.setForeground(QBrush(qcol(C.WHITE)))
            cur.insertText("You:    ", lbl_fmt)
        else:
            txt_fmt.setForeground(QBrush(qcol(C.PRI)))
            cur.insertText("Jarvis: ", lbl_fmt)
        cur.insertText(text[:400] + "\n", txt_fmt)
        self.setTextCursor(cur)
        self.ensureCursorVisible()


# ---------------------------------------------------------------------------
# MemoryEditorWidget — view/edit/delete long-term memory entries
# ---------------------------------------------------------------------------

class MemoryEditorWidget(QWidget):
    def __init__(self, memory_path: Path, parent=None):
        super().__init__(parent)
        self._path = memory_path
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(3)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(2)
        self._tree.setStyleSheet(f"""
            QTreeWidget {{
                background: {C.PANEL}; color: {C.TEXT};
                border: none; font-family: 'Courier New'; font-size: 8pt;
            }}
            QTreeWidget::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}
            QHeaderView::section {{ background: {C.DARK}; color: {C.TEXT_DIM}; border: none; }}
        """)
        lay.addWidget(self._tree, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self._del_btn = QPushButton("\u2715  Delete")
        self._del_btn.setFixedHeight(22)
        self._del_btn.setFont(QFont("Courier New", 7))
        self._del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._del_btn.setStyleSheet(f"""
            QPushButton {{ background: #140006; color: {C.RED};
                border: 1px solid {C.RED}; border-radius: 3px; }}
            QPushButton:hover {{ background: #280010; }}
        """)
        self._del_btn.clicked.connect(self._delete_selected)
        ref_btn = QPushButton("\u21bb  Refresh")
        ref_btn.setFixedHeight(22)
        ref_btn.setFont(QFont("Courier New", 7))
        ref_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ref_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; }}
        """)
        ref_btn.clicked.connect(self.refresh)
        btn_row.addWidget(self._del_btn)
        btn_row.addWidget(ref_btn)
        lay.addLayout(btn_row)
        self.refresh()

    def refresh(self):
        self._tree.clear()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}
        except Exception:
            data = {}
        for cat, entries in data.items():
            cat_item = QTreeWidgetItem(self._tree, [cat.upper()])
            cat_item.setForeground(0, QBrush(qcol(C.ACC2)))
            cat_item.setFont(0, QFont("Courier New", 7, QFont.Weight.Bold))
            if isinstance(entries, dict):
                for key, val in entries.items():
                    v = val.get("value", str(val)) if isinstance(val, dict) else str(val)
                    child = QTreeWidgetItem(cat_item, [f"  {key}", v[:60]])
                    child.setForeground(0, QBrush(qcol(C.TEXT)))
                    child.setForeground(1, QBrush(qcol(C.TEXT_DIM)))
            cat_item.setExpanded(True)

    def _delete_selected(self):
        from PyQt6.QtWidgets import QMessageBox
        item = self._tree.currentItem()
        if not item or not item.parent():
            return
        cat = item.parent().text(0).lower()
        key = item.text(0).strip()
        box = QMessageBox(self)
        box.setWindowTitle("Confirmar")
        box.setText(f"Deletar mem\u00f3ria '{cat}/{key}'?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() == QMessageBox.StandardButton.Yes:
            from memory.memory_manager import forget
            forget(key, cat)
            self.refresh()


# ---------------------------------------------------------------------------
# FileDropZone + _DropCanvas — drag-and-drop file area
# ---------------------------------------------------------------------------

class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True
            self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False
        self._canvas.update()

    def dropEvent(self, e):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True
        self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False
        self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None
        self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z = self._z
        W, H = self.width(), self.height()
        pad = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:
            border_col = qcol(C.GREEN, 200)
        elif z._drag_over:
            border_col = qcol(C.PRI, 230)
        elif z._hovering:
            border_col = qcol(C.BORDER_B, 200)
        else:
            border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:
            self._paint_file(p, W, H)
        elif z._drag_over:
            self._paint_drag_over(p, W, H)
        else:
            self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QRectF(cx, cy - 14, 0, 18).topLeft(), QRectF(cx, cy + 4).topLeft())
        p.drawLine(QRectF(cx - 8, cy - 6, 8, 8).topLeft(), QRectF(cx, cy - 14).topLeft())
        p.drawLine(QRectF(cx + 8, cy - 6, -8, 8).topLeft(), QRectF(cx, cy - 14).topLeft())
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images \u00b7 Video \u00b7 Audio \u00b7 PDF \u00b7 Docs \u00b7 Code \u00b7 Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "\u2b07")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  \u00b7  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42:
            par = "\u2026" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "\u2715")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)
