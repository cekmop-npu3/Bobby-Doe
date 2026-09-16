"""Unit tests for COM-port lifecycle and transmission behavior."""

from __future__ import annotations

from types import GeneratorType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from backend.serial_port import SerialConnection, SerialException


class FakeSignal:
    """Minimal Qt-signal replacement for a mocked receiver thread."""

    def connect(self, callback) -> None:
        """Accept a signal callback without invoking it.

        :param callback: Callback connected by the serial controller.
        :return: ``None``.
        """


class FakeReceiver:
    """Receiver substitute that prevents tests from creating a real thread."""

    def __init__(self, port) -> None:
        """Store the port and expose mockable lifecycle methods.

        :param port: Mock serial port owned by the test.
        """
        self.port = port
        self.data_received = FakeSignal()
        self.error_occurred = FakeSignal()
        self.start = MagicMock()
        self.stop = MagicMock()


class SerialConnectionTests(unittest.TestCase):
    """Verify the serial controller without a physical or virtual COM port."""

    def test_available_ports_returns_generator(self) -> None:
        """Ensure discovered port names are exposed through a generator.

        :return: ``None``.
        """
        fake_ports = [SimpleNamespace(device="COM10"), SimpleNamespace(device="COM11")]
        with patch("backend.serial_port.list_ports.comports", return_value=fake_ports):
            ports = SerialConnection.available_ports()

        self.assertIsInstance(ports, GeneratorType)
        self.assertEqual(list(ports), ["COM10", "COM11"])

    @patch("backend.serial_port.ReceiverThread", FakeReceiver)
    @patch("backend.serial_port.Serial")
    def test_context_manager_opens_and_closes_port(self, serial_class) -> None:
        """Ensure context entry opens the port and exit releases resources.

        :param serial_class: Mocked pyserial constructor supplied by unittest.
        :return: ``None``.
        """
        port = MagicMock(is_open=True)
        serial_class.return_value = port
        connection = SerialConnection("COM10", 1.5)

        with connection as active_connection:
            self.assertIs(active_connection, connection)
            serial_class.assert_called_once()
            self.assertIsNotNone(connection.receiver)
            self.assertTrue(connection.receiver.start.called)

        port.close.assert_called_once()
        self.assertIsNone(connection.receiver)

    @patch("backend.serial_port.Serial", side_effect=SerialException("busy"))
    def test_context_manager_wraps_open_error(self, serial_class) -> None:
        """Ensure a pyserial open error becomes a user-facing ConnectionError.

        :param serial_class: Mocked failing pyserial constructor.
        :return: ``None``.
        """
        with self.assertRaisesRegex(ConnectionError, "Could not open COM10"):
            with SerialConnection("COM10", 1):
                pass
        serial_class.assert_called_once()

    def test_send_character_writes_utf8_bytes(self) -> None:
        """Ensure sending one character writes its UTF-8 byte representation.

        :return: ``None``.
        """
        port = MagicMock(is_open=True)
        connection = SerialConnection("COM10", 1)
        connection.port = port

        self.assertTrue(connection.send_character("Я"))
        port.write.assert_called_once_with("Я".encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
