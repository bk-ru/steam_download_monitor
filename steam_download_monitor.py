"""Точка входа для запуска без установки пакета."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from steam_monitor.app import main


if __name__ == "__main__":
    raise SystemExit(main())
