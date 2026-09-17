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
BAUD_RATE_CHECK_TIMEOUT_MS: Final[int] = 30_000

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

        self.status_note = "Choose a COM port and baud rate."

        self.setWindowTitle("Serial Messenger")

        self.resize(
            880,
            570,
        )

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

        self.input_area = ImmediateInput()

        self.output_area = QtWidgets.QPlainTextEdit()
        self.output_area.setReadOnly(True)

        self.state_label = QtWidgets.QLabel()
        self.state_label.setWordWrap(True)

        self.state_label.setObjectName("stateLabel")

    def _create_layout(self) -> None:
        """Arrange the application widgets."""
        layout = QtWidgets.QGridLayout(self)

        layout.setContentsMargins(
            24,
            22,
            24,
            24,
        )

        layout.setHorizontalSpacing(18)

        layout.setVerticalSpacing(16)

        heading = QtWidgets.QLabel("SERIAL MESSENGER")

        heading.setObjectName("heading")

        subtitle = QtWidgets.QLabel("Variant 1 · direct character transmission")

        subtitle.setObjectName("subtitle")

        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(1)

        title_box.addWidget(heading)

        title_box.addWidget(subtitle)

        layout.addLayout(
            title_box,
            0,
            0,
            1,
            2,
        )

        control = self._section("Connection")

        form = QtWidgets.QFormLayout()
        form.setSpacing(10)

        form.addRow(
            "COM port",
            self.port_choice,
        )

        form.addRow(
            "Baud rate",
            self.baud_rate_choice,
        )

        self._content_layout(control).addLayout(form)

        state = self._section("Activity")

        self._content_layout(state).addWidget(self.state_label)

        layout.addWidget(
            control,
            1,
            0,
        )

        layout.addWidget(
            state,
            1,
            1,
        )

        outgoing = self._section("Send now")

        self._content_layout(outgoing).addWidget(self.input_area)

        incoming = self._section("Received")

        self._content_layout(incoming).addWidget(self.output_area)

        layout.addWidget(
            outgoing,
            2,
            0,
        )

        layout.addWidget(
            incoming,
            2,
            1,
        )

        layout.setRowStretch(
            2,
            1,
        )

        layout.setColumnStretch(
            0,
            1,
        )

        layout.setColumnStretch(
            1,
            1,
        )

    @staticmethod
    def _section(
        title: str,
    ) -> QtWidgets.QFrame:
        """Create a titled visual section."""
        frame = QtWidgets.QFrame()

        frame.setObjectName("section")

        box = QtWidgets.QVBoxLayout(frame)

        box.setContentsMargins(
            16,
            14,
            16,
            16,
        )

        box.setSpacing(10)

        label = QtWidgets.QLabel(title)

        label.setObjectName("sectionTitle")

        box.addWidget(label)

        return frame

    @staticmethod
    def _content_layout(
        frame: QtWidgets.QFrame,
    ) -> QtWidgets.QVBoxLayout:
        """Return the section's vertical content layout."""
        layout = frame.layout()

        if not isinstance(
            layout,
            QtWidgets.QVBoxLayout,
        ):
            raise TypeError("Section frame does not have " "a vertical layout.")

        return layout

    def _connect_signals(self) -> None:
        """Connect UI signals."""
        self.port_choice.activated.connect(self._try_open)

        self.baud_rate_choice.currentIndexChanged.connect(self._change_baud_rate)

        self.input_area.character_entered.connect(self._send_character)

    def _load_ports(self) -> None:
        """Load currently available serial ports."""
        ports = list(SerialConnection.available_ports())

        self.port_choice.addItems(ports)

        self.port_choice.setCurrentIndex(-1)

        if not ports:
            self.status_note = "No COM ports found."

            self._refresh_state()

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

        self._port_opened(port_name)

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

    def _port_opened(
        self,
        port_name: str,
    ) -> None:
        """Lock the port and keep the baud-rate selector available."""
        self.port_choice.setEnabled(False)

        self.input_area.setEnabled(False)

        self.status_note = (
            f"Connected to {port_name}. "
            "Waiting for matching baud rate on the other COM port..."
        )

        self._refresh_state()

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

        self.status_note = f"Connected at " f"{connection.baud_rate} baud."

        self.input_area.setFocus()

        self._refresh_state()

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

            self.status_note = "Sending characters directly."

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
        self.state_label.setText(
            f"Sent characters: " f"{self.sent_characters}\n" f"{self.status_note}"
        )

    def _show_open_error(
        self,
        message: str,
    ) -> None:
        """Show an error that occurred before a connection existed."""
        if self._handling_error:
            return

        self._handling_error = True

        try:
            self.status_note = message
            self._refresh_state()

            QtWidgets.QMessageBox.critical(
                self,
                "Serial Messenger",
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

            self.status_note = message
            self._refresh_state()

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
                "Serial Messenger",
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

        self.baud_rate_choice.setEnabled(True)

        # Clear the port first. Programmatically changing
        # baud_rate_choice emits currentIndexChanged, but _try_open()
        # then sees no selected COM port and does nothing.
        self.port_choice.setCurrentIndex(-1)

        self.baud_rate_choice.setCurrentIndex(-1)

        self.input_area.setEnabled(False)

        self.status_note = "Choose a COM port and baud rate."

        self._refresh_state()

    def _apply_style(self) -> None:
        """Apply the application style."""
        self.setStyleSheet("""
            QWidget {
                background: #f3f6fb;
                color: #172033;
                font: 14px 'Segoe UI';
            }

            QLabel {
                background: transparent;
                border: none;
            }

            QLabel#heading {
                background: transparent;
                color: #263a84;
                font-size: 23px;
                font-weight: 800;
                letter-spacing: 1px;
            }

            QLabel#subtitle {
                background: transparent;
                color: #65708a;
                font-size: 12px;
            }

            QFrame#section {
                background: white;
                border: 1px solid #d9e1f0;
                border-radius: 14px;
            }

            QLabel#sectionTitle {
                background: transparent;
                color: #263a84;
                font-size: 15px;
                font-weight: 700;
            }

            QLabel#stateLabel {
                background: transparent;
                color: #40506d;
            }

            QComboBox {
                background: #f9fbff;
                border: 1px solid #cbd6eb;
                border-radius: 8px;
                padding: 8px;
                min-height: 22px;
            }

            QPlainTextEdit {
                background: transparent;
                border: 1px solid #cbd6eb;
                border-radius: 8px;
                padding: 8px;
                selection-background-color: #9db5ff;
            }

            QComboBox:hover,
            QPlainTextEdit:focus {
                border-color: #5271d7;
            }

            QComboBox:disabled {
                background: #edf1f7;
                color: #71809c;
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
