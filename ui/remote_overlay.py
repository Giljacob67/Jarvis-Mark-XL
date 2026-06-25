"""QR overlay for pairing a phone to JARVIS Remote Dashboard."""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class _C:
    PANEL2   = "#010f18"
    BORDER   = "#0d3347"
    BORDER_B = "#1a5c7a"
    PRI      = "#00d4ff"
    PRI_DIM  = "#007a99"
    PRI_GHO  = "#001f2e"
    ACC      = "#ff6b00"
    GREEN    = "#00ff88"
    TEXT_DIM = "#3a8a9a"
    TEXT_MED = "#5ab8cc"
    TEXT     = "#8ffcff"


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(
        self,
        url: str,
        key: str,
        auto_login_url: str = "",
        manual_url: str = "",
        expiry_secs: int = 600,
        parent=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {_C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry         = time.time() + expiry_secs
        self._on_new_key     = None
        self._auto_login_url = auto_login_url
        self._manual_url     = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=_C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs, QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {_C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet("background: white; border-radius: 10px; padding: 4px;")
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)
        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Escaneie com a câmera do celular", 8, color=_C.TEXT_DIM))

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {_C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Ou digite manualmente no navegador:", 7, color=_C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {_C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {_C.ACC};
            background: {_C.PANEL2};
            border: 1px solid {_C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {_C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        new_btn = QPushButton("NOVA CHAVE")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: #010d14; color: {_C.PRI};
                border: 1px solid {_C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {_C.PRI_GHO}; border: 1px solid {_C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("FECHAR")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_C.TEXT_MED};
                border: 1px solid {_C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {_C.TEXT}; border: 1px solid {_C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(box_size=5, border=2, error_correction=_qrmod.constants.ERROR_CORRECT_M)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))

    def _tick(self) -> None:
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Chave expira em  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        self._ctimer.stop()
        self._key_lbl.setText("CONECTADO")
        self._key_lbl.setStyleSheet(f"""
            color: {_C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet("color: #00ff88; background: #001a0d; border-radius: 10px;")
        self._timer_lbl.setText("Celular conectado — JARVIS pronto")
        self._timer_lbl.setStyleSheet(f"color: {_C.GREEN}; background: transparent;")

    def _refresh_key(self) -> None:
        if not self._on_new_key:
            return
        result = self._on_new_key()
        if not result:
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        self._manual_url     = manual or url
        self._url_lbl.setText(self._manual_url)
        self._key_lbl.setText(key)
        self._auto_login_url = auto
        self._update_qr(auto or url)
        self._expiry = time.time() + 600
        self._key_lbl.setStyleSheet(f"""
            color: {_C.ACC};
            background: {_C.PANEL2};
            border: 1px solid {_C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)

    def _do_close(self) -> None:
        self._ctimer.stop()
        self.hide()
        self.closed.emit()