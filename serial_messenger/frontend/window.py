"""Graphical interface for the variant 1 COM messenger."""

from __future__ import annotations

from contextlib import ExitStack
import logging
from typing import Final, override

from PyQt6 import QtCore, QtGui, QtWidgets

from backend import BAUD_RATES, SerialConnection

__all__ = ("SerialMessenger",)


LOGGER = logging.getLogger(__name__)


# Enough time to configure the second application/window.
BAUD_RATE_CHECK_TIMEOUT_MS: Final[int] = 7_000

# Retry once per second while waiting for the other side.
BAUD_RATE_RETRY_INTERVAL_MS: Final[int] = 1_000


class ImmediateInput(QtWidgets.QPlainTextEdit):
    """Text area that sends printable characters immediately."""

    character_entered = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        """Create the immediate-transmission input area."""
        super().__init__()

        self.setPlaceholderText("Type here — every character is sent immediately")

    @override
    def keyPressEvent(
        self,
        event: QtGui.QKeyEvent | None,
    ) -> None:
        """Send entered characters immediately."""
        if event is None:
            return

        text = event.text()

        if event.key() in (
            QtCore.Qt.Key.Key_Return,
            QtCore.Qt.Key.Key_Enter,
        ):
            self.character_entered.emit("\n")

            event.accept()
            return

        super().keyPressEvent(event)

        if text and text.isprintable():
            self.character_entered.emit(text)


class SerialMessenger(QtWidgets.QWidget):
    """Present connection controls and serial messaging UI."""

    def __init__(self) -> None:
        """Build the application window."""
        super().__init__()

        self.connection: SerialConnection | None = None
        self._connection_scope = ExitStack()

        self.sent_characters = 0
        self.baud_rate_verified = False

        # Prevent recursive or repeated QMessageBox calls caused by
        # queued serial signals.
        self._handling_error = False

        self.setWindowTitle("COM Port Messenger")
        self.resize(1200, 900)
        self.setMinimumSize(640, 420)

        self._create_widgets()
        self._create_layout()
        self._apply_style()
        self._connect_signals()
        self._load_ports()
        self._refresh_state()

        # Overall verification timeout.
        self.baud_check_timer = QtCore.QTimer(self)

        self.baud_check_timer.setSingleShot(True)

        self.baud_check_timer.timeout.connect(self._baud_rate_check_timed_out)

        # Periodic HELLO retry.
        self.baud_retry_timer = QtCore.QTimer(self)

        self.baud_retry_timer.timeout.connect(self._retry_baud_rate_check)

        self.input_area.setFocus()

    def _create_widgets(self) -> None:
        """Create controls and text widgets."""
        self.port_choice = QtWidgets.QComboBox()

        self.baud_rate_choice = QtWidgets.QComboBox()

        for value in BAUD_RATES:
            self.baud_rate_choice.addItem(
                str(value),
                value,
            )

        self.baud_rate_choice.setCurrentIndex(-1)
        self.baud_rate_choice.setEnabled(False)

        self.input_area = ImmediateInput()
        self.input_area.setEnabled(False)
        self.input_area.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.output_area = QtWidgets.QPlainTextEdit()
        self.output_area.setReadOnly(True)
        self.output_area.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.state_label = QtWidgets.QLabel()
        self.state_label.setWordWrap(True)

        self.state_label.setObjectName("stateLabel")

    def _create_layout(self) -> None:
        """Arrange the resizable controls and text areas."""
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(22, 18, 22, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(12)

        port_box = self._section("COM port")
        self._content_layout(port_box).addWidget(self.port_choice)

        baud_rate_box = self._section("Baud rate")
        self._content_layout(baud_rate_box).addWidget(self.baud_rate_choice)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(port_box)
        controls.addWidget(baud_rate_box)
        controls.addStretch()
        layout.addLayout(controls, 0, 0, 1, 2)

        outgoing = self._section("Input")
        self._content_layout(outgoing).addWidget(self.input_area)

        incoming = self._section("Output")
        self._content_layout(incoming).addWidget(self.output_area)

        layout.addWidget(outgoing, 1, 0)
        layout.addWidget(incoming, 1, 1)
        layout.addWidget(self.state_label, 2, 0, 1, 2)

        layout.setRowStretch(1, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

    @staticmethod
    def _section(
        title: str,
    ) -> QtWidgets.QGroupBox:
        """Create a titled visual section."""
        section = QtWidgets.QGroupBox(title)
        box = QtWidgets.QVBoxLayout(section)
        box.setContentsMargins(10, 18, 10, 10)
        return section

    @staticmethod
    def _content_layout(
        frame: QtWidgets.QGroupBox,
    ) -> QtWidgets.QVBoxLayout:
        """Return the section's vertical content layout."""
        layout = frame.layout()

        if not isinstance(
            layout,
            QtWidgets.QVBoxLayout,
        ):
            raise TypeError("Section does not have a vertical layout.")

        return layout

    def _connect_signals(self) -> None:
        """Connect UI signals."""
        self.port_choice.currentIndexChanged.connect(self._port_selected)

        self.baud_rate_choice.currentIndexChanged.connect(self._change_baud_rate)

        self.input_area.character_entered.connect(self._send_character)

    def _load_ports(self) -> None:
        """Load currently available serial ports."""
        ports = list(SerialConnection.available_ports())

        self.port_choice.addItems(ports)

        self.port_choice.setCurrentIndex(-1)

    def _port_selected(self) -> None:
        """Enable speed selection only after a COM port is selected.

        :return: ``None``.
        """
        if self._handling_error:
            return

        has_port = bool(self.port_choice.currentText().strip())
        self.baud_rate_choice.setEnabled(has_port)

        if not has_port:
            self.baud_rate_choice.setCurrentIndex(-1)
            return

        self._try_open()

    def _try_open(self) -> None:
        """Open the selected COM port when both choices exist."""
        if self.connection is not None:
            return

        if self._handling_error:
            return

        port_name = self.port_choice.currentText().strip()

        baud_rate = self.baud_rate_choice.currentData()

        if not port_name or baud_rate is None:
            return

        try:
            connection = self._connection_scope.enter_context(
                SerialConnection(
                    port_name,
                    baud_rate,
                )
            )

        except ConnectionError as error:
            LOGGER.warning(
                "Connection attempt failed: %s",
                error,
            )

            self._show_open_error(str(error))

            return

        self.connection = connection

        connection.received.connect(self._append_received)

        connection.error.connect(self._show_error)

        connection.baud_rate_verified.connect(self._baud_rate_verified)

        LOGGER.info(
            "Connected frontend to serial port %s.",
            port_name,
        )

        self._port_opened()

        self._start_baud_rate_check()

    def _change_baud_rate(self) -> None:
        """Reconnect the selected port when the baud rate changes.

        :return: ``None``.
        """
        if self._handling_error:
            return

        baud_rate = self.baud_rate_choice.currentData()
        if baud_rate is None:
            return

        if self.connection is not None:
            if baud_rate == self.connection.baud_rate:
                return
            self._close_connection()

        self._try_open()

    def _port_opened(self) -> None:
        """Lock the port and keep the baud-rate selector available."""
        self.port_choice.setEnabled(False)

        self.input_area.setEnabled(False)

    def _start_baud_rate_check(self) -> None:
        """Start the baud-rate handshake."""
        if self.connection is None:
            return

        if self.connection.verification_finished:
            return

        self.baud_check_timer.start(BAUD_RATE_CHECK_TIMEOUT_MS)

        self.baud_retry_timer.start(BAUD_RATE_RETRY_INTERVAL_MS)

        self.connection.start_baud_rate_check()

    def _retry_baud_rate_check(self) -> None:
        """Retry HELLO while the peer is not ready."""
        connection = self.connection

        if connection is None:
            self.baud_retry_timer.stop()
            return

        if self.baud_rate_verified or connection.verification_finished:
            self.baud_retry_timer.stop()
            return

        LOGGER.debug(
            "Retrying baud verification on %s.",
            connection.port_name,
        )

        connection.start_baud_rate_check()

    def _baud_rate_verified(self) -> None:
        """Enable normal messaging after successful verification."""
        connection = self.connection

        if connection is None:
            return

        if self.baud_rate_verified:
            return

        self.baud_check_timer.stop()
        self.baud_retry_timer.stop()

        self.baud_rate_verified = True

        self.input_area.setEnabled(True)

        self.input_area.setFocus()

    def _baud_rate_check_timed_out(self) -> None:
        """Handle failure to verify within the allowed time."""
        self.baud_retry_timer.stop()

        if self.baud_rate_verified:
            return

        if self.connection is None:
            return

        self._show_error(
            "Baud-rate verification timed out. "
            "Make sure both COM ports are open and "
            "use the same baud rate."
        )

    def _send_character(
        self,
        character: str,
    ) -> None:
        """Send one entered character."""
        connection = self.connection

        if connection is None:
            return

        if not self.baud_rate_verified:
            return

        if connection.send_character(character):
            self.sent_characters += 1

            self._refresh_state()

    def _append_received(
        self,
        text: str,
    ) -> None:
        """Append received user data to the output area."""
        if not self.baud_rate_verified:
            return

        cursor = self.output_area.textCursor()

        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)

        cursor.insertText(
            text.replace(
                "\r\n",
                "\n",
            ).replace(
                "\r",
                "\n",
            )
        )

        self.output_area.setTextCursor(cursor)

        self.output_area.ensureCursorVisible()

    def _refresh_state(self) -> None:
        """Refresh the status display."""
        self.state_label.setText(f"Sent characters: {self.sent_characters}")

    def _show_open_error(
        self,
        message: str,
    ) -> None:
        """Show an error that occurred before a connection existed."""
        if self._handling_error:
            return

        self._handling_error = True

        try:
            QtWidgets.QMessageBox.critical(
                self,
                "COM Port Messenger",
                message,
            )

        finally:
            self._handling_error = False

    def _show_error(
        self,
        message: str,
    ) -> None:
        """Show one error and safely tear down the active connection."""
        # A queued signal belonging to an already-reset connection
        # should simply be ignored.
        if self.connection is None:
            return

        # Prevent recursive QMessageBoxes while Qt is running the
        # modal dialog's nested event loop.
        if self._handling_error:
            return

        self._handling_error = True

        try:
            LOGGER.error(
                "User-visible serial error: %s",
                message,
            )

            # Stop ALL sources of new verification work first.
            self.baud_check_timer.stop()
            self.baud_retry_timer.stop()

            # Critical ordering:
            #
            #   1. stop timers
            #   2. close/reset the serial connection
            #   3. show QMessageBox
            #
            # QMessageBox runs a nested Qt event loop. If the
            # connection is left alive while it is visible, queued
            # serial errors can create an error-dialog storm.
            self._reset_connection_controls()

            QtWidgets.QMessageBox.critical(
                self,
                "COM Port Messenger",
                message,
            )

        finally:
            self._handling_error = False

    def _close_connection(self) -> None:
        """Stop the active connection without changing selector values.

        :return: ``None``.
        """
        self.baud_check_timer.stop()
        self.baud_retry_timer.stop()

        connection = self.connection

        # Clear the reference FIRST so any queued error signal from the
        # old connection is ignored by _show_error().
        self.connection = None

        self.baud_rate_verified = False

        if connection is not None:
            try:
                connection.received.disconnect(self._append_received)
            except TypeError:
                pass

            try:
                connection.error.disconnect(self._show_error)
            except TypeError:
                pass

            try:
                connection.baud_rate_verified.disconnect(self._baud_rate_verified)
            except TypeError:
                pass

        self._connection_scope.close()
        self._connection_scope = ExitStack()

    def _reset_connection_controls(self) -> None:
        """Close the active connection and unlock cleared selectors.

        :return: ``None``.
        """
        self._close_connection()

        self.port_choice.setEnabled(True)

        # Clear the port first. Programmatically changing
        # baud_rate_choice emits currentIndexChanged, but _try_open()
        # then sees no selected COM port and does nothing.
        self.port_choice.setCurrentIndex(-1)

        self.baud_rate_choice.setCurrentIndex(-1)
        self.baud_rate_choice.setEnabled(False)

        self.input_area.setEnabled(False)

    def _apply_style(self) -> None:
        """Apply the application style."""
        self.setStyleSheet("""
            QWidget {
                background: #f0f0f0;
                color: #202020;
                font: 14px 'Segoe UI';
            }

            QGroupBox {
                border: 1px solid #c8c8c8;
                margin-top: 9px;
                padding-top: 7px;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 3px;
            }

            QComboBox {
                background: white;
                border: 1px solid #a8a8a8;
                padding: 5px 8px;
                min-height: 24px;
            }

            QPlainTextEdit {
                background: white;
                border: 1px solid #303030;
                padding: 4px;
                selection-background-color: #9bbcff;
            }
            """)

    @override
    def closeEvent(
        self,
        event: QtGui.QCloseEvent | None,
    ) -> None:
        """Close timers and the serial connection before exiting."""
        self._close_connection()

        LOGGER.info("Serial Messenger window closed.")

        if event is not None:
            event.accept()
