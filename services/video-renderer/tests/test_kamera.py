"""Kamerafahrt: die Ueberschrift muss lesbar stehen bleiben.

Befund aus dem ersten echten Video: "Bei schneller Stimme verlaesst die Kamera
die Ueberschrift schon nach 0,1 s." Die Stimme spricht 191 wpm - die erste Zahl faellt
dann nach gut einer halben Sekunde, und die Anfahrt startet davor.
"""
from __future__ import annotations

from PIL import Image

import beweis

MINDESTHALT = 0.8   # Sekunden, die der Titel ungestoert stehen soll


def _erfassung() -> beweis.Erfassung:
    return beweis.Erfassung(
        bild=Image.new("RGB", (beweis.BREITE, 4000), (10, 10, 12)),
        domain="openai.com",
        titel=(40, 300, 1000, 120),
        treffer={},
    )


def test_kamera_haelt_die_ueberschrift_auch_bei_schneller_stimme():
    # Arrange - erste Belegstelle faellt nach 0,6 s (191 wpm)
    erf = _erfassung()
    fokusse = [beweis.Fokus(zeit=0.6, box=(40, 2200, 400, 50), label="$10")]

    # Act
    kam = beweis.kamerafahrt(erf, 30.0, fokusse)

    # Assert - der erste echte Ortswechsel darf nicht vor MINDESTHALT beginnen
    start_y = kam.k[0][1]
    abfahrt = next((t for t, cy, _z in kam.k if t > 0 and abs(cy - start_y) > 50), None)
    assert abfahrt is None or abfahrt >= MINDESTHALT, (
        "Kamera verlaesst die Ueberschrift schon bei %.2f s (gefordert: >= %.1f s)."
        % (abfahrt, MINDESTHALT)
    )


def test_kamera_steht_zur_sprechzeit_auf_der_belegstelle():
    """Der Halt am Titel darf die Ankunft am Beleg nicht verschlucken.

    (Der vorherige Test pruefte nur, ob kam.k sortiert ist - das erledigt
    Kamera.__init__ ohnehin selbst und waere auch bei kaputter Fahrt gruen.)
    """
    # Arrange
    erf = _erfassung()
    f = beweis.Fokus(zeit=6.0, box=(40, 2200, 400, 50), label="$10")

    # Act
    kam = beweis.kamerafahrt(erf, 30.0, [f])
    cy, zoom = kam.an(f.zeit)

    # Assert
    soll = beweis._cy_fuer(f.box[1] + f.box[3] / 2, beweis.FOKUS_Y, beweis.ZOOM_ZAHL)
    assert abs(cy - soll) < 30, (
        "Kamera steht bei %.1f statt %.1f, wenn die Zahl faellt." % (cy, soll)
    )
    assert abs(zoom - beweis.ZOOM_ZAHL) < 0.01


def test_titel_halt_verzoegert_eine_sehr_frueh_gesprochene_zahl_nur_minimal():
    # Arrange - Zahl faellt bei 0,6 s, der Titel soll 0,9 s stehen
    erf = _erfassung()
    f = beweis.Fokus(zeit=0.6, box=(40, 2200, 400, 50), label="$10")

    # Act
    kam = beweis.kamerafahrt(erf, 30.0, [f])
    ankunft = next(t for t, cy, _z in kam.k if abs(cy - kam.k[0][1]) > 50)

    # Assert - spaeter als die Stimme, aber nicht beliebig spaet
    assert MINDESTHALT <= ankunft <= 1.4, "Ankunft bei %.2f s" % ankunft
