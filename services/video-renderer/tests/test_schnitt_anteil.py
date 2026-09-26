"""Schnittplanung: die Quelle muss die Haelfte der Laufzeit halten.

Befund aus dem ersten echten Video: Clip-Anteil 71 %, Quelle nur 29 % - der
Beweis kommt zu kurz. Genau der Beweis ist aber das, was den Kanal von einer
TTS-ueber-Stock-Schablone unterscheidet.
"""
from __future__ import annotations

import beweis
from motion3 import Beat, Wort


def _beats(anzahl: int, laenge: float, verdict_ab: int) -> list[Beat]:
    """Baut eine typische 30-s-Spur: erst Fakten, hinten die Einordnung."""
    out = []
    for i in range(anzahl):
        start = i * laenge
        art = "verdict" if i >= verdict_ab else "fakt"
        out.append(Beat([Wort("w", start, start + laenge)], start, start + laenge, art))
    return out


def _anteil(abschnitte, dauer: float) -> float:
    return sum(a.ende - a.start for a in abschnitte if a.art == "broll") / dauer


def test_broll_anteil_bleibt_bei_wenigen_belegstellen_unter_der_haelfte():
    # Arrange - 30 s, 20 Beats, aber nur 2 gefundene Belegstellen (realistisch)
    dauer = 30.0
    beats = _beats(20, 1.5, verdict_ab=15)
    fokusse = [
        beweis.Fokus(zeit=2.0, box=(0, 400, 300, 40), label="$10"),
        beweis.Fokus(zeit=8.0, box=(0, 900, 300, 40), label="$50"),
    ]

    # Act
    abschnitte = beweis.plane_schnitt(beats, fokusse, dauer, True, True)

    # Assert
    anteil = _anteil(abschnitte, dauer)
    assert anteil <= 0.5, (
        "Clip-Anteil liegt bei %.0f %% - die Quelle kommt zu kurz." % (anteil * 100)
    )


def test_ohne_clips_bleibt_alles_auf_der_quelle():
    # Arrange
    beats = _beats(10, 1.5, verdict_ab=8)

    # Act
    abschnitte = beweis.plane_schnitt(beats, [], 15.0, True, False)

    # Assert - Rueckfallweg darf durch die Budget-Logik nicht kaputtgehen
    assert all(a.art == "beleg" for a in abschnitte)


def test_clip_schnitte_liegen_nur_auf_beat_grenzen():
    """Geschnitten wird nur dort, wo die Stimme absetzt.

    Ein Schnitt mitten im Wort ist der sichtbarste Bruch zwischen Bild und
    Stimme - und teile_broll ist die einzige Stelle, die das garantiert.
    """
    # Arrange
    dauer = 30.0
    beats = _beats(20, 1.5, verdict_ab=12)
    fokusse = [beweis.Fokus(zeit=2.0, box=(0, 400, 300, 40), label="$10")]

    # Act
    abschnitte = beweis.teile_broll(
        beweis.plane_schnitt(beats, fokusse, dauer, True, True), beats)

    # Assert
    grenzen = {round(b.start, 3) for b in beats} | {0.0, round(dauer, 3)}
    schnitte = {round(a.start, 3) for a in abschnitte}
    assert schnitte <= grenzen, (
        "Schnitte ausserhalb der Beat-Grenzen: %s" % sorted(schnitte - grenzen)
    )


def test_teilen_veraendert_den_clip_anteil_nicht():
    # Arrange
    dauer = 30.0
    beats = _beats(20, 1.5, verdict_ab=12)
    fokusse = [beweis.Fokus(zeit=2.0, box=(0, 400, 300, 40), label="$10")]
    geplant = beweis.plane_schnitt(beats, fokusse, dauer, True, True)

    # Act
    geteilt = beweis.teile_broll(geplant, beats)

    # Assert - Teilen schneidet nur auf, es darf kein Budget verschieben
    assert abs(_anteil(geteilt, dauer) - _anteil(geplant, dauer)) < 1e-6
    assert _anteil(geteilt, dauer) <= 0.5


def test_geteilte_clips_sind_nie_kuerzer_als_die_flacker_grenze():
    # Arrange
    dauer = 30.0
    beats = _beats(20, 1.5, verdict_ab=12)
    geplant = beweis.plane_schnitt(
        beats, [beweis.Fokus(zeit=2.0, box=(0, 400, 300, 40))], dauer, True, True)

    # Act
    geteilt = beweis.teile_broll(geplant, beats)

    # Assert
    kurz = [(a.start, a.ende) for a in geteilt
            if a.art == "broll" and a.ende - a.start < 1.2]
    assert not kurz, "Clip-Schnipsel unter 1,2 s flackern: %s" % kurz
