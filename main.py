"""
Application bootstrap: logging setup and main window launch.
"""

import logging
import sys


def configure_logging() -> None:
    """Configure root logger for console output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main() -> int:
    """
    Entry point for ByteBack.

    Returns:
        Process exit code (0 on success).
    """
    configure_logging()

    try:
        from ui.main_window import MainWindow

        app = MainWindow()
        app.run()
        return 0
    except Exception as exc:
        logging.exception("Fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
