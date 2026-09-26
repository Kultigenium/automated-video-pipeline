"""Fehlerpfade der Clip-Beschaffung.

B-Roll ist Kuer, nicht Pflicht: faellt Pexels aus, muss der Renderer ohne
Clips weiterlaufen (beweis.py faellt dann auf die reine Quelle zurueck).
Ein Absturz hier kostet das ganze Video.
"""
from __future__ import annotations

import contextlib
import os
import stat

import broll

from test_broll_sperrliste import _falscher_client

GUT = {"id": 1, "duration": 9, "video_files": [{"height": 1080, "link": "http://x/a.mp4"}]}


def _client_der_wirft(fehler):
    class _C:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            raise fehler

        @contextlib.contextmanager
        def stream(self, methode, link):
            raise fehler
            yield  # pragma: no cover

    return _C


def test_ohne_api_key_gibt_es_keine_clips_aber_auch_keinen_absturz(monkeypatch, tmp_path):
    monkeypatch.setattr(broll, "PEXELS_API_KEY", "")
    assert broll.hole_clips("t", "s", str(tmp_path)) == []


def test_ausgefallene_pexels_suche_liefert_leere_liste(monkeypatch, tmp_path):
    # Arrange
    monkeypatch.setattr(broll, "PEXELS_API_KEY", "k")
    monkeypatch.setattr(broll.httpx, "Client", _client_der_wirft(RuntimeError("Pexels down")))

    # Act / Assert - kein Durchschlagen der Ausnahme
    assert broll.hole_clips("t", "s", str(tmp_path / "c"), ledger=str(tmp_path / "l.json")) == []


def test_treffer_ohne_brauchbare_aufloesung_werden_uebersprungen(monkeypatch, tmp_path):
    # Arrange - 480p ist zu klein fuer eine 1080er Kulisse
    klein = {"id": 2, "duration": 9, "video_files": [{"height": 480, "link": "http://x/s.mp4"}]}
    monkeypatch.setattr(broll, "PEXELS_API_KEY", "k")
    monkeypatch.setattr(broll.httpx, "Client", _falscher_client([klein]))

    # Act / Assert
    assert broll.hole_clips("t", "s", str(tmp_path / "c"), ledger=str(tmp_path / "l.json")) == []


def test_kaputte_pexels_id_kippt_den_render_nicht(monkeypatch, tmp_path):
    # Arrange - id als Unsinn, wie ein API-Ausrutscher es liefern koennte
    kaputt = dict(GUT, id={"nicht": "zahl"})
    monkeypatch.setattr(broll, "PEXELS_API_KEY", "k")
    monkeypatch.setattr(broll.httpx, "Client", _falscher_client([kaputt]))

    # Act - darf keine ValueError/TypeError nach oben werfen
    pfade = broll.hole_clips("t", "s", str(tmp_path / "c"), ledger=str(tmp_path / "l.json"))

    # Assert
    assert isinstance(pfade, list)


def test_nicht_schreibbare_sperrliste_warnt_statt_zu_werfen(tmp_path, caplog):
    # Arrange - Ordner ohne Schreibrecht
    gesperrt_dir = tmp_path / "ro"
    gesperrt_dir.mkdir()
    os.chmod(gesperrt_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        # Act
        broll.merke_clips([7, 8], str(gesperrt_dir / "l.json"))

        # Assert
        assert any("nicht schreibbar" in r.getMessage() for r in caplog.records)
    finally:
        os.chmod(gesperrt_dir, stat.S_IRWXU)


def test_leere_id_liste_schreibt_gar_nichts(tmp_path):
    # Arrange
    ziel = tmp_path / "l.json"

    # Act
    broll.merke_clips([], str(ziel))

    # Assert - keine leere Datei anlegen, die spaeter als "kein Eintrag" gilt
    assert not ziel.exists()
