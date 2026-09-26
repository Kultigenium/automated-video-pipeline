"""Sperrliste: das Gedaechtnis, das Wiederholung ueber Videos hinweg verhindert.

Der Existenz-Check in test_broll_vielfalt.py sagt nur, DASS es die Funktionen
gibt. Hier wird geprueft, ob sie ihren Zweck erfuellen - mit gefaelschtem
Pexels, damit kein echter API-Call noetig ist.
"""
from __future__ import annotations

import contextlib
import threading

import broll

VIDEO_A = {"id": 111, "duration": 10, "video_files": [{"height": 1080, "link": "http://x/a.mp4"}]}
VIDEO_B = {"id": 222, "duration": 10, "video_files": [{"height": 1080, "link": "http://x/b.mp4"}]}


def _falscher_client(videos):
    """Minimal-Attrappe von httpx.Client, so wie hole_clips ihn benutzt."""
    class _Antwort:
        def __init__(self, nutzlast=None):
            self._n = nutzlast

        def raise_for_status(self):
            pass

        def json(self):
            return self._n

        def iter_bytes(self, groesse=0):
            yield b"x" * 50_000          # ueber der 40-kB-Schwelle in hole_clips

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Antwort({"videos": list(videos)})

        @contextlib.contextmanager
        def stream(self, methode, link):
            yield _Antwort()

    return _Client


def test_gesperrter_clip_wird_nicht_erneut_geladen(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setattr(broll, "PEXELS_API_KEY", "test-key")
    monkeypatch.setattr(broll.httpx, "Client", _falscher_client([VIDEO_A, VIDEO_B]))
    ledger = str(tmp_path / "ledger.json")

    # Act - 111 lief schon
    pfade = broll.hole_clips("t", "s", str(tmp_path / "clips"), anzahl=5,
                             gesperrt={111}, ledger=ledger)

    # Assert
    assert pfade, "gar kein Clip geladen"
    gemerkt = broll.lade_gesperrte(ledger)
    assert 222 in gemerkt, "der frische Clip wurde nicht vermerkt"
    assert 111 not in gemerkt, "der gesperrte Clip wurde trotzdem verwendet"


def test_uebergebenes_sperr_set_bleibt_unveraendert(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setattr(broll, "PEXELS_API_KEY", "test-key")
    monkeypatch.setattr(broll.httpx, "Client", _falscher_client([VIDEO_A, VIDEO_B]))
    meins = {111}

    # Act
    broll.hole_clips("t", "s", str(tmp_path / "clips"), anzahl=5,
                     gesperrt=meins, ledger=str(tmp_path / "l.json"))

    # Assert - hole_clips darf das Set des Aufrufers nicht mitbenutzen
    assert meins == {111}


def test_parallele_renders_verlieren_keine_eintraege(tmp_path):
    """Der Renderer laeuft als sync FastAPI-Route im Thread-Pool.

    Ohne Dateisperre gingen bei 8 gleichzeitigen Schreibern rund die Haelfte
    der Eintraege verloren - genau die Sperre, die Wiederholung verhindern soll.
    """
    # Arrange
    ledger = str(tmp_path / "ledger.json")
    schreiber, pro_schreiber = 8, 25

    def schreibe(n):
        broll.merke_clips(range(n * 1000, n * 1000 + pro_schreiber), ledger)

    # Act
    faeden = [threading.Thread(target=schreibe, args=(n,)) for n in range(schreiber)]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join()

    # Assert
    assert len(broll.lade_gesperrte(ledger)) == schreiber * pro_schreiber


def test_korrupte_sperrliste_blockiert_den_render_nicht(tmp_path, caplog):
    # Arrange
    ledger = tmp_path / "ledger.json"
    ledger.write_text('{"ids": [1, 2', encoding="utf-8")   # abgeschnittenes JSON

    # Act
    gemerkt = broll.lade_gesperrte(str(ledger))

    # Assert - leer, aber nicht stumm
    assert gemerkt == set()
    assert any("unlesbar" in r.getMessage() for r in caplog.records), \
        "korrupte Sperrliste wurde stumm verschluckt"
