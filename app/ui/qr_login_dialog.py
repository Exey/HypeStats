"""QR-code login dialog: authorizes a session without a phone number."""
from __future__ import annotations

import qrcode
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout,
)

from ..worker import QrLoginWorker


def _qr_pixmap(url: str, box_size: int = 6) -> QPixmap:
    img = qrcode.make(url, box_size=box_size, border=2).convert("RGB")
    qimage = QImage(img.tobytes("raw", "RGB"), img.width, img.height,
                     img.width * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimage)


class QrLoginDialog(QDialog):
    def __init__(self, cfg, i18n, parent=None, account: int = 1) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.i18n = i18n
        tr = i18n.tr
        self.setWindowTitle(tr("qr_login_title"))

        layout = QVBoxLayout(self)

        self.hint_label = QLabel(tr("qr_login_hint"))
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(260, 260)
        layout.addWidget(self.qr_label)

        self.status_label = QLabel(tr("qr_login_generating"))
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self.status_label)

        cancel_btn = QPushButton(tr("stop"))
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

        self.worker = QrLoginWorker(
            cfg.get("API_ID"), cfg.get("API_HASH"), cfg.session_path(account), parent=self,
        )
        self.worker.sig_qr.connect(self._on_qr)
        self.worker.sig_status.connect(self._on_status)
        self.worker.sig_ask.connect(self._on_ask)
        self.worker.sig_done.connect(self._on_done)
        self._result_ok = False
        self._result_msg = ""
        self.worker.start()

    # ------------------------------------------------------------ signals
    def _on_qr(self, url: str) -> None:
        self.qr_label.setPixmap(_qr_pixmap(url))

    def _on_status(self, status: str) -> None:
        tr = self.i18n.tr
        self.status_label.setText(
            tr("qr_login_expired") if status == "expired" else tr("qr_login_waiting")
        )

    def _on_ask(self, kind: str, _prompt: str) -> None:
        tr = self.i18n.tr
        text, ok = QInputDialog.getText(
            self, tr("login_title"), tr("login_password_prompt"),
            QLineEdit.EchoMode.Password,
        )
        if ok and text.strip():
            self.worker.provide_answer(text.strip())
        else:
            self.worker.request_cancel()

    def _on_done(self, ok: bool, msg: str) -> None:
        self._result_ok = ok
        self._result_msg = msg
        if ok:
            self.accept()
        else:
            self.reject()

    # -------------------------------------------------------------- close
    def reject(self) -> None:
        if self.worker.isRunning():
            self.worker.request_cancel()
            self.worker.wait()
        super().reject()

    def run_and_report(self) -> None:
        """Show the dialog modally and pop a message box with the outcome."""
        self.exec()
        tr = self.i18n.tr
        if self._result_ok:
            QMessageBox.information(self.parent(), tr("qr_login_title"),
                                     self._result_msg or tr("qr_login_success"))
        elif self._result_msg and self._result_msg != "Cancelled":
            QMessageBox.warning(self.parent(), tr("qr_login_title"), self._result_msg)
