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

# Keep verification traffic within printable ASCII. Some serial bridges handle
# terminal control characters differently even when ordinary text passes.
CONTROL_FRAME_START: Final[str] = "[SM1:"
CONTROL_FRAME_END: Final[str] = "]"

CONTROL_HELLO: Final[str] = "HELLO"
CONTROL_ACK: Final[str] = "ACK"

MAX_CONTROL_BUFFER_LENGTH: Final[int] = 4096


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
                        #
                        # If baud rates differ, receiving garbage is normal.
                        # SerialConnection decides whether the received text
                        # contains a valid control frame.
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
    baud_rate_verified = QtCore.pyqtSignal()

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

        self._control_buffer = ""

        self._baud_rate_confirmed = False
        self._verification_failed = False

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

        self.receiver.data_received.connect(self._handle_received_text)

        self.receiver.error_occurred.connect(self._handle_receiver_error)

        self.receiver.start()

        LOGGER.info(
            "Serial port %s is open.",
            self.port_name,
        )

        return self

    @property
    def verification_finished(self) -> bool:
        """Return whether verification has succeeded or failed."""
        return self._baud_rate_confirmed or self._verification_failed

    def start_baud_rate_check(self) -> bool:
        """Try to send a HELLO verification frame."""
        if self.verification_finished:
            return False

        return self._write_control_frame(f"{CONTROL_HELLO}:{self.baud_rate}")

    def _handle_receiver_error(
        self,
        message: str,
    ) -> None:
        """Forward a real serial-port failure once."""
        if self._verification_failed:
            return

        self.error.emit(message)

    def _handle_received_text(
        self,
        text: str,
    ) -> None:
        """Extract control frames from received text.

        Before baud-rate verification succeeds, arbitrary bytes are
        discarded. This is important because different baud rates can
        produce garbage bytes that must not be treated as application
        data or as repeated fatal errors.
        """
        if self._verification_failed:
            return

        self._control_buffer += text

        # Prevent malformed/mismatched input from growing indefinitely.
        if len(self._control_buffer) > MAX_CONTROL_BUFFER_LENGTH:
            LOGGER.debug(
                "Discarding oversized control buffer on %s.",
                self.port_name,
            )

            self._control_buffer = ""

            return

        while self._control_buffer:
            frame_start = self._control_buffer.find(CONTROL_FRAME_START)

            if frame_start == -1:
                self._handle_text_without_control_frame()
                return

            if frame_start > 0:
                prefix = self._control_buffer[:frame_start]

                # User text is only delivered after successful
                # verification.
                if self._baud_rate_confirmed:
                    self.received.emit(prefix)

                self._control_buffer = self._control_buffer[frame_start:]

            frame_end = self._control_buffer.find(
                CONTROL_FRAME_END,
                len(CONTROL_FRAME_START),
            )

            if frame_end == -1:
                # We may have received only part of the frame.
                return

            frame = self._control_buffer[len(CONTROL_FRAME_START) : frame_end]

            self._control_buffer = self._control_buffer[frame_end + 1 :]

            self._handle_control_frame(frame)

            if self._verification_failed:
                self._control_buffer = ""
                return

    def _handle_text_without_control_frame(
        self,
    ) -> None:
        """Handle buffered text that contains no complete control frame."""
        # Preserve a suffix that might be the beginning of
        # CONTROL_FRAME_START split across multiple reads.
        prefix_length = min(
            len(self._control_buffer),
            len(CONTROL_FRAME_START) - 1,
        )

        while prefix_length and not CONTROL_FRAME_START.startswith(
            self._control_buffer[-prefix_length:]
        ):
            prefix_length -= 1

        if prefix_length:
            normal_text = self._control_buffer[:-prefix_length]

            remainder = self._control_buffer[-prefix_length:]
        else:
            normal_text = self._control_buffer
            remainder = ""

        # Before verification, arbitrary incoming characters are
        # discarded because mismatched baud rates may create garbage.
        if self._baud_rate_confirmed and normal_text:
            self.received.emit(normal_text)

        self._control_buffer = remainder

    def _handle_control_frame(
        self,
        frame: str,
    ) -> None:
        """Handle one extracted protocol control frame."""
        if self._verification_failed:
            return

        command, separator, baud_rate_text = frame.partition(":")

        # A malformed frame may simply be garbage caused by a baud
        # mismatch. Ignore it instead of creating an error storm.
        if not separator:
            LOGGER.debug(
                "Ignoring malformed control frame on %s.",
                self.port_name,
            )
            return

        try:
            remote_baud_rate = int(baud_rate_text)

        except ValueError:
            LOGGER.debug(
                "Ignoring control frame with invalid baud value on %s.",
                self.port_name,
            )
            return

        # A valid protocol frame declaring a different baud rate is a
        # reliable mismatch. Report it exactly once.
        if remote_baud_rate != self.baud_rate:
            self._fail_verification(
                "Baud rate mismatch: "
                f"local {self.baud_rate}, "
                f"remote {remote_baud_rate}."
            )
            return

        if command == CONTROL_HELLO:
            LOGGER.debug(
                "Received HELLO at %s baud on %s.",
                self.baud_rate,
                self.port_name,
            )

            # Receiving a valid HELLO containing our baud rate already
            # proves that the incoming configuration is compatible.
            self._confirm_baud_rate()

            # Try to tell the other side as well.
            self._write_control_frame(f"{CONTROL_ACK}:{self.baud_rate}")

            return

        if command == CONTROL_ACK:
            LOGGER.debug(
                "Received ACK at %s baud on %s.",
                self.baud_rate,
                self.port_name,
            )

            self._confirm_baud_rate()
            return

        # Unknown commands may also be corrupted traffic.
        # Do not make them fatal during negotiation.
        LOGGER.debug(
            "Ignoring unknown control command %r on %s.",
            command,
            self.port_name,
        )

    def _confirm_baud_rate(self) -> None:
        """Mark verification as successful exactly once."""
        if self._baud_rate_confirmed:
            return

        if self._verification_failed:
            return

        self._baud_rate_confirmed = True

        LOGGER.info(
            "Baud rate %s verified on %s.",
            self.baud_rate,
            self.port_name,
        )

        self.baud_rate_verified.emit()

    def _fail_verification(
        self,
        message: str,
    ) -> None:
        """Report one terminal verification failure."""
        if self._verification_failed:
            return

        if self._baud_rate_confirmed:
            return

        self._verification_failed = True

        LOGGER.warning(
            "Baud-rate verification failed on %s: %s",
            self.port_name,
            message,
        )

        self.error.emit(message)

    def _write_control_frame(
        self,
        payload: str,
    ) -> bool:
        """Try to write one protocol control frame.

        A timeout is not fatal during baud-rate negotiation because
        the other virtual COM endpoint may simply not have been opened
        yet.
        """
        if self.verification_finished:
            # ACK is still allowed after successful verification.
            if not self._baud_rate_confirmed or not payload.startswith(CONTROL_ACK):
                return False

        if self.port is None or not self.port.is_open:
            return False

        frame = f"{CONTROL_FRAME_START}{payload}{CONTROL_FRAME_END}"

        try:
            for character in frame:
                encoded = character.encode("utf-8")
                if self.port.write(encoded) != len(encoded):
                    LOGGER.debug(
                        "Partial control-frame write on %s.",
                        self.port_name,
                    )
                    return False

            LOGGER.debug(
                "Sent control frame %r through %s.",
                payload,
                self.port_name,
            )

            return True

        except SerialTimeoutException:
            # Normal while the paired virtual COM endpoint is closed
            # or unavailable.
            LOGGER.debug(
                "Control-frame write timed out on %s.",
                self.port_name,
            )

            return False

        except (
            OSError,
            SerialException,
            UnicodeEncodeError,
            ValueError,
        ) as error:
            LOGGER.error(
                "Could not write control frame through %s: %s",
                self.port_name,
                error,
            )

            self._fail_verification(f"Baud-rate verification error: {error}")

            return False

    def send_character(
        self,
        character: str,
    ) -> bool:
        """Write one user-entered character."""
        if not self._baud_rate_confirmed:
            return False

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
