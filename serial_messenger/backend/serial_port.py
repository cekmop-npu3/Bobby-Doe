"""COM-port access and background receiving for laboratory work No. 1."""

from __future__ import annotations

import logging
from typing import Self, override

from codecs import getincrementaldecoder
from collections.abc import Generator
from types import TracebackType
from typing import Final

from PyQt6 import QtCore
from serial import Serial, SerialException
from serial.tools import list_ports


__all__ = ("BAUD_RATES", "SerialConnection")


LOGGER = logging.getLogger(__name__)


BYTE_SIZE: Final[int] = 8
PARITY: Final[str] = "N"
STOP_BITS: Final[int] = 1
TIMEOUT_SECONDS: Final[float] = 0.05
WRITE_TIMEOUT_SECONDS: Final[int] = 1
RECEIVE_POLL_INTERVAL_MS: Final[int] = 20
CONTROL_FRAME_START: Final[str] = "\x1eSM1:"
CONTROL_FRAME_END: Final[str] = "\x1f"
CONTROL_HELLO: Final[str] = "HELLO"
CONTROL_ACK: Final[str] = "ACK"

# Variant 1 selection: deliberately mutable and therefore not Final.
BAUD_RATES: list[int] = [110, 300, 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]


class ReceiverThread(QtCore.QThread):
    """Read a connected serial port in a background Qt thread."""

    data_received = QtCore.pyqtSignal(str)
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, port: Serial) -> None:
        """Create a receiver for an already-open serial port.

        :param port: Open pyserial port to read.
        """
        super().__init__()
        self.port = port
        self._active = True

    @override
    def run(self) -> None:
        """Read available bytes and emit decoded text until stopped.

        :return: ``None``.
        """
        decoder = getincrementaldecoder("utf-8")(errors="replace")
        while self._active:
            try:
                if not self.port.is_open:
                    return
                waiting = self.port.in_waiting
                if waiting:
                    text = decoder.decode(self.port.read(waiting))
                    if text:
                        if "\ufffd" in text:
                            self.error_occurred.emit(
                                "Received corrupted data. Check that both COM ports use the same baud rate."
                            )
                            return
                        self.data_received.emit(text)
                else:
                    self.msleep(RECEIVE_POLL_INTERVAL_MS)
            except (OSError, SerialException) as error:
                if self._active:
                    self.error_occurred.emit(f"Receive error: {error}")
                return
            except Exception as error:
                if self._active:
                    self.error_occurred.emit(f"Unexpected receive error: {error}")
                return

    def stop(self) -> None:
        """Request termination and wait briefly for this thread to finish.

        :return: ``None``.
        """
        self._active = False
        self.wait(1000)


class SerialConnection(QtCore.QObject):
    """Manage one selected serial port through the context-manager protocol."""

    received = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)
    baud_rate_verified = QtCore.pyqtSignal()

    def __init__(self, port_name: str, baud_rate: int) -> None:
        """Store a selected port and baud rate without opening it.

        :param port_name: System COM-port name, such as ``COM10``.
        :param baud_rate: User-selected transfer speed in baud.
        """
        super().__init__()
        self.port_name = port_name
        self.baud_rate = baud_rate
        self.port: Serial | None = None
        self.receiver: ReceiverThread | None = None
        self._control_buffer = ""
        self._baud_rate_confirmed = False

    @staticmethod
    def available_ports() -> Generator[str, None, None]:
        """Yield system names of serial ports visible to pyserial.

        :return: Generator of available COM-port names.
        """
        LOGGER.debug("Enumerating serial ports.")
        return (item.device for item in list_ports.comports())

    def __enter__(self) -> Self:
        """Open the configured port and start its receiving thread.

        :return: This connected controller.
        :raises ConnectionError: If the selected port cannot be opened.
        """
        try:
            LOGGER.info("Opening serial port %s at %s baud.", self.port_name, self.baud_rate)
            self.port = Serial(
                port=self.port_name,
                baudrate=self.baud_rate,
                bytesize=BYTE_SIZE,
                parity=PARITY,
                stopbits=STOP_BITS,
                timeout=TIMEOUT_SECONDS,
                write_timeout=WRITE_TIMEOUT_SECONDS,
            )
        except (SerialException, OSError, ValueError) as error:
            self.port = None
            LOGGER.warning("Could not open serial port %s: %s", self.port_name, error)
            raise ConnectionError(f"Could not open {self.port_name}: {error}") from error

        self.receiver = ReceiverThread(self.port)
        self.receiver.data_received.connect(self._handle_received_text)
        self.receiver.error_occurred.connect(self.error)
        self.receiver.start()
        LOGGER.info("Serial port %s is open.", self.port_name)
        return self

    def start_baud_rate_check(self) -> None:
        """Send the initial control frame used to confirm remote baud rate.

        :return: ``None``.
        """
        self._write_control_frame(f"{CONTROL_HELLO}:{self.baud_rate}")

    def _handle_received_text(self, text: str) -> None:
        """Separate control frames from user text received from the port.

        :param text: Newly decoded text emitted by the receiving thread.
        :return: ``None``.
        """
        self._control_buffer += text
        while self._control_buffer:
            frame_start = self._control_buffer.find(CONTROL_FRAME_START)
            if frame_start == -1:
                prefix_length = min(
                    len(self._control_buffer),
                    len(CONTROL_FRAME_START) - 1,
                )
                while prefix_length and not CONTROL_FRAME_START.startswith(
                    self._control_buffer[-prefix_length:]
                ):
                    prefix_length -= 1

                user_text = self._control_buffer[:-prefix_length] if prefix_length else self._control_buffer
                if user_text:
                    self.received.emit(user_text)
                self._control_buffer = self._control_buffer[-prefix_length:] if prefix_length else ""
                return

            if frame_start:
                self.received.emit(self._control_buffer[:frame_start])
                self._control_buffer = self._control_buffer[frame_start:]

            frame_end = self._control_buffer.find(
                CONTROL_FRAME_END,
                len(CONTROL_FRAME_START),
            )
            if frame_end == -1:
                return

            frame = self._control_buffer[
                len(CONTROL_FRAME_START):frame_end
            ]
            self._control_buffer = self._control_buffer[frame_end + 1:]
            self._handle_control_frame(frame)

    def _handle_control_frame(self, frame: str) -> None:
        """Validate and react to one received control frame.

        :param frame: Frame payload without the protocol delimiters.
        :return: ``None``.
        """
        command, separator, baud_rate_text = frame.partition(":")
        if not separator:
            self.error.emit("Received an invalid baud-rate verification frame.")
            return

        try:
            remote_baud_rate = int(baud_rate_text)
        except ValueError:
            self.error.emit("Received an invalid remote baud-rate value.")
            return

        if remote_baud_rate != self.baud_rate:
            self.error.emit(
                "Baud rate mismatch: "
                f"local {self.baud_rate}, remote {remote_baud_rate}."
            )
            return

        if command == CONTROL_HELLO:
            self._write_control_frame(f"{CONTROL_ACK}:{self.baud_rate}")
            self._confirm_baud_rate()
        elif command == CONTROL_ACK:
            self._confirm_baud_rate()
        else:
            self.error.emit("Received an unknown baud-rate verification frame.")

    def _confirm_baud_rate(self) -> None:
        """Emit successful verification only once for this connection.

        :return: ``None``.
        """
        if self._baud_rate_confirmed:
            return

        self._baud_rate_confirmed = True
        LOGGER.info(
            "Baud rate %s verified on %s.",
            self.baud_rate,
            self.port_name,
        )
        self.baud_rate_verified.emit()

    def _write_control_frame(self, payload: str) -> None:
        """Write a non-user control frame one character at a time.

        :param payload: Control-frame payload without delimiters.
        :return: ``None``.
        """
        if self.port is None or not self.port.is_open:
            self.error.emit("COM port is not open.")
            return
        try:
            frame = f"{CONTROL_FRAME_START}{payload}{CONTROL_FRAME_END}"
            for character in frame:
                self.port.write(character.encode("utf-8"))
        except (OSError, SerialException, UnicodeEncodeError, ValueError) as error:
            LOGGER.error("Could not verify baud rate on %s: %s", self.port_name, error)
            self.error.emit(f"Baud-rate verification error: {error}")

    def send_character(self, character: str) -> bool:
        """Write one user-entered character to the open port.

        :param character: Exactly one character, or the Enter newline character.
        :return: ``True`` if pyserial accepted the bytes; otherwise ``False``.
        """
        if self.port is None or not self.port.is_open:
            self.error.emit("COM port is not open.")
            return False
        try:
            self.port.write(character.encode("utf-8"))
            LOGGER.debug("Sent one character through %s.", self.port_name)
            return True
        except (OSError, SerialException, UnicodeEncodeError, ValueError) as error:
            LOGGER.error("Could not send through %s: %s", self.port_name, error)
            self.error.emit(f"Send error: {error}")
            return False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop receiving and close the port at context-manager exit.

        :param exc_type: Exception type, if the managed block raised one.
        :param exc_value: Exception instance, if the managed block raised one.
        :param traceback: Traceback for the managed-block exception.
        :return: ``None``. Exceptions are not suppressed.
        """
        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None
        if self.port is not None and self.port.is_open:
            self.port.close()
        LOGGER.info("Serial port %s is closed.", self.port_name)
