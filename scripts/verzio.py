"""A program verziója: egyetlen forrás a projekt gyökerében lévő VERSION fájl."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEV = "Állampapír-ajánló"


def verzio() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "ismeretlen"


def cimke() -> str:
    return f"{NEV} v{verzio()}"
