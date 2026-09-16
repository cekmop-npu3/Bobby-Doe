"""Graphical interface for the variant 3 COM messenger."""

from __future__ import annotations

from contextlib import ExitStack
import logging
from typing import override

from PyQt6 import QtCore, QtGui, QtWidgets

from backend import STOP_BIT_VALUES, SerialConnection


__all__ = ("SerialMessenger",)


LOGGER = logging.getLogger(__name__)


class ImmediateInput(QtWidgets.QPlainTextEdit):
    """Provide a text area that reports every typed printable character."""

    character_entered = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        """Create and label the immediate-transmission input area.

        :return: ``None``.
        """
        super().__init__()
        self.setPlaceholderText("Type here — every character is sent immediately")

    @override
    def keyPressEvent(self, e: QtGui.QKeyEvent | None) -> None:
        """Pass input to Qt and signal printable characters immediately.

        :param e: Keyboard event delivered by Qt, if provided.
        :return: ``None``.
        """
        if e is None:
            return
        text = e.text()
        if e.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            self.character_entered.emit("\n")
            e.accept()
            return
        super().keyPressEvent(e)
        if text and text.isprintable():
            self.character_entered.emit(text)


class SerialMessenger(QtWidgets.QWidget):
    """Present the required control, input, output, and state areas."""

    def __init__(self) -> None:
        """Build the main application window and start its state timer.

        :return: ``None``.
        """
        super().__init__()
        self.connection: SerialConnection | None = None
        self._connection_scope = ExitStack()
        self.sent_characters = 0
        self.status_note = "Choose a COM port and stop bits."

        self.setWindowTitle("Serial Messenger")
        self.resize(880, 570)
        self._create_widgets()
        self._create_layout()
        self._apply_style()
        self._connect_signals()
        self._load_ports()
        self._refresh_state()

        self.state_timer = QtCore.QTimer(self)
        self.state_timer.timeout.connect(self._refresh_state)
        self.state_timer.start(1000)
        self.input_area.setFocus()

    def _create_widgets(self) -> None:
        """Create the controls and the three text/status display widgets.

        :return: ``None``.
        """
        self.port_choice = QtWidgets.QComboBox()
        self.stop_bits_choice = QtWidgets.QComboBox()
        for value in STOP_BIT_VALUES:
            self.stop_bits_choice.addItem(str(value), value)
        self.stop_bits_choice.setCurrentIndex(-1)

        self.input_area = ImmediateInput()
        self.output_area = QtWidgets.QPlainTextEdit()
        self.output_area.setReadOnly(True)
        self.state_label = QtWidgets.QLabel()
        self.state_label.setWordWrap(True)
        self.state_label.setObjectName("stateLabel")

    def _create_layout(self) -> None:
        """Arrange widgets into the four required sections.

        :return: ``None``.
        """
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(16)

        heading = QtWidgets.QLabel("SERIAL MESSENGER")
        heading.setObjectName("heading")
        subtitle = QtWidgets.QLabel("Variant 3 · direct character transmission")
        subtitle.setObjectName("subtitle")
        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(heading)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box, 0, 0, 1, 2)

        control = self._section("Connection")
        form = QtWidgets.QFormLayout()
        form.setSpacing(10)
        form.addRow("COM port", self.port_choice)
        form.addRow("Stop bits", self.stop_bits_choice)
        self._content_layout(control).addLayout(form)

        state = self._section("Activity")
        self._content_layout(state).addWidget(self.state_label)
        layout.addWidget(control, 1, 0)
        layout.addWidget(state, 1, 1)

        outgoing = self._section("Send now")
        self._content_layout(outgoing).addWidget(self.input_area)
        incoming = self._section("Received")
        self._content_layout(incoming).addWidget(self.output_area)
        layout.addWidget(outgoing, 2, 0)
        layout.addWidget(incoming, 2, 1)
        layout.setRowStretch(2, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

    @staticmethod
    def _section(title: str) -> QtWidgets.QFrame:
        """Create a titled visual section.

        :param title: Heading displayed at the top of the section.
        :return: Frame with a vertical layout ready for content.
        """
        frame = QtWidgets.QFrame()
        frame.setObjectName("section")
        box = QtWidgets.QVBoxLayout(frame)
        box.setContentsMargins(16, 14, 16, 16)
        box.setSpacing(10)
        label = QtWidgets.QLabel(title)
        label.setObjectName("sectionTitle")
        box.addWidget(label)
        return frame

    @staticmethod
    def _content_layout(frame: QtWidgets.QFrame) -> QtWidgets.QVBoxLayout:
        """Return the vertical content layout created for a section frame.

        :param frame: Section frame created by :meth:`_section`.
        :return: The frame's vertical layout.
        :raises TypeError: If the frame was not created by :meth:`_section`.
        """
        layout = frame.layout()
        if not isinstance(layout, QtWidgets.QVBoxLayout):
            raise TypeError("Section frame does not have a vertical layout.")
        return layout

    def _connect_signals(self) -> None:
        """Connect widget input and serial events to their handlers.

        :return: ``None``.
        """
        self.port_choice.activated.connect(self._try_open)
        self.stop_bits_choice.currentIndexChanged.connect(self._try_open)
        self.input_area.character_entered.connect(self._send_character)

    def _load_ports(self) -> None:
        """Fill the port selector with COM ports found by pyserial.

        :return: ``None``.
        """
        ports = list(SerialConnection.available_ports())
        self.port_choice.addItems(ports)
        self.port_choice.setCurrentIndex(-1)
        if not ports:
            self.status_note = "No COM ports found."

    def _try_open(self) -> None:
        """Open the selected port when both required choices are present.

        :return: ``None``.
        """
        if self.connection is not None:
            return
        port_name = self.port_choice.currentText().strip()
        stop_bits = self.stop_bits_choice.currentData()
        if not port_name or stop_bits is None:
            return
        try:
            self.connection = self._connection_scope.enter_context(
                SerialConnection(port_name, stop_bits)
            )
        except ConnectionError as error:
            LOGGER.warning("Connection attempt failed: %s", error)
            self._show_error(str(error))
            return

        self.connection.received.connect(self._append_received)
        self.connection.error.connect(self._show_error)
        LOGGER.info("Connected frontend to serial port %s.", port_name)
        self._port_opened(port_name)

    def _port_opened(self, port_name: str) -> None:
        """Lock connection selectors after the port opens successfully.

        :param port_name: Name of the COM port that was opened.
        :return: ``None``.
        """
        self.port_choice.setEnabled(False)
        self.stop_bits_choice.setEnabled(False)
        self.status_note = f"Connected to {port_name}."
        self.input_area.setFocus()
        self._refresh_state()

    def _send_character(self, character: str) -> None:
        """Send one entered character and update the sent-character count.

        :param character: Printable character or the Enter newline character.
        :return: ``None``.
        """
        if self.connection is not None and self.connection.send_character(character):
            self.sent_characters += 1
            self.status_note = "Sending characters directly."
            self._refresh_state()

    def _append_received(self, text: str) -> None:
        """Append received text to the output area.

        :param text: Decoded text delivered by the receiving thread.
        :return: ``None``.
        """
        cursor = self.output_area.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(text.replace("\r\n", "\n").replace("\r", "\n"))
        self.output_area.setTextCursor(cursor)
        self.output_area.ensureCursorVisible()

    def _refresh_state(self) -> None:
        """Display the current sent-character count and last status note.

        :return: ``None``.
        """
        self.state_label.setText(
            f"Sent characters: {self.sent_characters}\n{self.status_note}"
        )

    def _show_error(self, message: str) -> None:
        """Show a serial error and preserve it in the state area.

        :param message: Human-readable explanation of the error.
        :return: ``None``.
        """
        self.status_note = message
        LOGGER.error("User-visible serial error: %s", message)
        self._refresh_state()
        QtWidgets.QMessageBox.critical(self, "Serial Messenger", message)

    def _apply_style(self) -> None:
        """Apply the visual style for the application window.

        :return: ``None``.
        """
        self.setStyleSheet("""
            QWidget { background: #f3f6fb; color: #172033; font: 14px 'Segoe UI'; }
            QLabel#heading { color: #263a84; font-size: 23px; font-weight: 800; letter-spacing: 1px; }
            QLabel#subtitle { color: #65708a; font-size: 12px; }
            QFrame#section { background: white; border: 1px solid #d9e1f0; border-radius: 14px; }
            QLabel#sectionTitle { color: #263a84; font-size: 15px; font-weight: 700; }
            QLabel#stateLabel { color: #40506d; line-height: 1.4; }
            QComboBox, QPlainTextEdit { background: #f9fbff; border: 1px solid #cbd6eb; border-radius: 8px; padding: 8px; }
            QComboBox { min-height: 22px; } QComboBox:hover, QPlainTextEdit:focus { border-color: #5271d7; }
            QComboBox:disabled { background: #edf1f7; color: #71809c; }
            QPlainTextEdit { selection-background-color: #9db5ff; }
        """)

    @override
    def closeEvent(self, a0: QtGui.QCloseEvent | None) -> None:
        """Close the connection before allowing Qt to close the window.

        :param a0: Qt close event to accept after cleanup, if provided.
        :return: ``None``.
        """
        self._connection_scope.close()
        LOGGER.info("Serial Messenger window closed.")
        if a0 is not None:
            a0.accept()
