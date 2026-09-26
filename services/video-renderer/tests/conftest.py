"""Gemeinsame Test-Basis: macht die Renderer-Module importierbar.

Die Renderer-Module sind als flache Skripte geschrieben (sie laufen im
Container mit diesem Ordner als Arbeitsverzeichnis), darum wird der Ordner hier
direkt auf den sys.path gelegt statt ein Paket zu erfinden.
"""
from __future__ import annotations

import sys
from pathlib import Path

VIDEO = Path(__file__).resolve().parent.parent
if str(VIDEO) not in sys.path:
    sys.path.insert(0, str(VIDEO))
