"""COM-port access and background receiving for laboratory work No. 1."""

from __future__ import annotations

import logging
from codecs import getincrementaldecoder
from collections.abc import Generator
from types import TracebackType
from typing import Final, Self, override

from PyQt6 import QtCore
from serial import Serial, SerialException, SerialTimeoutException
from serial.tools import list_ports

__all__ = ("BAUD_RATES", "SerialConnection")


LOGGER = logging.getLogger(__name__)


BYTE_SIZE: Final[int] = 8
PARITY: Final[str] = "N"
STOP_BITS: Final[int] = 1

TIMEOUT_SECONDS: Final[float] = 0.05

# Keep write blocking reasonably short while the other side is unavailable.
WRITE_TIMEOUT_SECONDS: Final[float] = 0.25

RECEIVE_POLL_INTERVAL_MS: Final[int] = 20


BAUD_RATES: list[int] = [
    110,
    300,
    600,
    1200,
    2400,
    4800,
    9600,
    19200,
    38400,
    57600,
    115200,
]


class ReceiverThread(QtCore.QThread):
    """Read a connected serial port in a background Qt thread."""

    data_received = QtCore.pyqtSignal(str)
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, port: Serial) -> None:
        """Create a receiver for an already-open serial port."""
        super().__init__()

        self.port = port
        self._active = True

    @override
    def run(self) -> None:
        """Read available bytes until the thread is stopped."""
        decoder = getincrementaldecoder("utf-8")(errors="replace")

        while self._active:
            try:
                if not self.port.is_open:
                    return

                waiting = self.port.in_waiting

                if waiting:
                    raw_data = self.port.read(waiting)

                    text = decoder.decode(raw_data)

                    if text:
                        # Do not treat malformed UTF-8 as fatal here.
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
        """Request thread termination."""
        self._active = False
        self.wait(1000)


class SerialConnection(QtCore.QObject):
    """Manage one selected serial port."""

    received = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)

    def __init__(
        self,
        port_name: str,
        baud_rate: int,
    ) -> None:
        """Store the selected COM port and baud rate."""
        super().__init__()

        self.port_name = port_name
        self.baud_rate = baud_rate

        self.port: Serial | None = None
        self.receiver: ReceiverThread | None = None


    @staticmethod
    def available_ports() -> Generator[str, None, None]:
        """Yield system COM-port names."""
        LOGGER.debug("Enumerating serial ports.")

        return (item.device for item in list_ports.comports())

    def __enter__(self) -> Self:
        """Open the selected port and start receiving."""
        try:
            LOGGER.info(
                "Opening serial port %s at %s baud.",
                self.port_name,
                self.baud_rate,
            )

            self.port = Serial(
                port=self.port_name,
                baudrate=self.baud_rate,
                bytesize=BYTE_SIZE,
                parity=PARITY,
                stopbits=STOP_BITS,
                timeout=TIMEOUT_SECONDS,
                write_timeout=WRITE_TIMEOUT_SECONDS,
            )

        except (
            SerialException,
            OSError,
            ValueError,
        ) as error:
            self.port = None

            LOGGER.warning(
                "Could not open serial port %s: %s",
                self.port_name,
                error,
            )

            raise ConnectionError(
                f"Could not open {self.port_name}: {error}"
            ) from error

        self.receiver = ReceiverThread(self.port)

        self.receiver.data_received.connect(self.received.emit)

        self.receiver.error_occurred.connect(self._handle_receiver_error)

        self.receiver.start()

        LOGGER.info(
            "Serial port %s is open.",
            self.port_name,
        )

        return self

    def _handle_receiver_error(
        self,
        message: str,
    ) -> None:
        """Forward a real serial-port failure."""
        self.error.emit(message)

    def send_character(
        self,
        character: str,
    ) -> bool:
        """Write one user-entered character."""
        if self.port is None or not self.port.is_open:
            self.error.emit("COM port is not open.")
            return False

        try:
            encoded = character.encode("utf-8")

            written = self.port.write(encoded)

            if written != len(encoded):
                self.error.emit("Send error: incomplete write.")
                return False

            LOGGER.debug(
                "Sent one character through %s.",
                self.port_name,
            )

            return True

        except SerialTimeoutException:
            self.error.emit("Send error: write timeout.")

            return False

        except (
            OSError,
            SerialException,
            UnicodeEncodeError,
            ValueError,
        ) as error:
            LOGGER.error(
                "Could not send through %s: %s",
                self.port_name,
                error,
            )

            self.error.emit(f"Send error: {error}")

            return False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop receiving and close the serial port."""
        # Mark the connection as inactive before stopping the thread,
        # preventing new verification activity during shutdown.
        self._verification_failed = True

        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None

        if self.port is not None and self.port.is_open:
            try:
                self.port.close()

            except (OSError, SerialException):
                LOGGER.exception(
                    "Error while closing serial port %s.",
                    self.port_name,
                )

        self.port = None

        LOGGER.info(
            "Serial port %s is closed.",
            self.port_name,
        )
