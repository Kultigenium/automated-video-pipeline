"""Clip-Auswahl: zwei Videos zum selben Thema duerfen nicht dasselbe Material zeigen.

Befund aus dem ersten echten Video (2026-09-17): bei jedem Preis-Thema kamen
dieselben "finance data screen"-Clips, 3 von 4 wie im Test zuvor. Kanalweit
gleiches Stock-Material ist genau das Muster, das YouTube als "inauthentic
content" abstraft.
"""
from __future__ import annotations

import broll

PREIS_STORY_A = (
    "OpenAI just published pricing for GPT-6 Astra. Input costs ten dollars "
    "per million tokens, output fifty. Batch processing halves it."
)
PREIS_STORY_B = (
    "Anthropic cut Claude Opus pricing by forty percent. The cheap tier now "
    "bills three dollars per million tokens for long context windows."
)


def test_zwei_preis_storys_liefern_nicht_dieselben_suchbegriffe():
    # Arrange / Act
    a = broll.suchbegriffe("GPT-6 Astra pricing", PREIS_STORY_A)
    b = broll.suchbegriffe("Claude Opus price cut", PREIS_STORY_B)

    # Assert - identische Begriffslisten heissen identisches Material
    assert a != b, (
        "Beide Preis-Storys erzeugen exakt dieselben Suchbegriffe %r - "
        "damit liefert Pexels zwangslaeufig dieselben Clips." % (a,)
    )


def test_suchbegriffe_greifen_inhalte_aus_dem_script_auf():
    # Arrange / Act
    begriffe = " ".join(broll.suchbegriffe("GPT-6 Astra pricing", PREIS_STORY_A)).lower()

    # Assert - irgendetwas aus der konkreten Story muss durchschlagen
    assert any(w in begriffe for w in ("batch", "token", "openai", "gpt")), (
        "Suchbegriffe %r stammen ausschliesslich aus der festen Themen-Tabelle; "
        "die Story selbst faerbt nicht ab." % (begriffe,)
    )


def test_bereits_genutzte_clips_lassen_sich_sperren():
    # Assert - ohne Gedaechtnis ueber Videos hinweg gibt es keine Vielfalt
    assert hasattr(broll, "lade_gesperrte") and hasattr(broll, "merke_clips"), (
        "broll kennt keine Sperrliste fuer schon verwendete Clips - "
        "jedes Video darf dasselbe Material erneut ziehen."
    )
