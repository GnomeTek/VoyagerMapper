"""Entry point for the desktop app: ``python -m voymap gui`` or ``voymap-gui``."""
from __future__ import annotations

import sys


def main():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.exit("PySide6 is not installed. Run: pip install 'voymap[gui]'  (or: pip install PySide6)")
    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("voymap")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
