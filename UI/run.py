#!/usr/bin/env python3
"""Start Gradio UI (HTTP :7860 by default)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from gradio_app import main as ui_main

    ui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
