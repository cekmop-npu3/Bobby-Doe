"""Application entry point."""

import sys

from PyQt6 import QtWidgets

from frontend import SerialMessenger
from logging_config import configure_logging


def main() -> None:
    """Create the Qt application and run the serial-messenger event loop.

    :return: ``None``. The function exits the process when the event loop ends.
    """
    configure_logging()
    app = QtWidgets.QApplication(sys.argv)
    window = SerialMessenger()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
