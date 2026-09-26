"""SSRF-Schutz: Der Renderer darf nur oeffentliche http(s)-Adressen oeffnen.

Die Quelle kommt als URL im Request. Ohne Pruefung liesse sich der Browser auf
interne Container, localhost oder Cloud-Metadaten schicken - und deren Inhalt
kaeme als Video zurueck.
"""
from __future__ import annotations

import pytest

import beweis


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5678/",
    "http://localhost/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://192.168.1.1/",
    "http://[::1]/",
    "file:///etc/passwd",
    "ftp://example.com/",
    "javascript:alert(1)",
    "",
    "not a url",
])
def test_interne_und_fremde_adressen_werden_abgelehnt(url):
    assert beweis.ist_oeffentliche_url(url) is False


def test_oeffentliche_adresse_wird_zugelassen(monkeypatch):
    monkeypatch.setattr(beweis.socket, "getaddrinfo",
                        lambda host, port: [(None, None, None, "", ("2606:4700:4700::1111", 0, 0, 0))])
    beweis._host_oeffentlich.cache_clear()
    assert beweis.ist_oeffentliche_url("https://example.org/pricing") is True


def test_host_mit_einer_internen_adresse_wird_abgelehnt(monkeypatch):
    # DNS liefert eine oeffentliche UND eine interne Adresse -> ablehnen
    monkeypatch.setattr(beweis.socket, "getaddrinfo", lambda host, port: [
        (None, None, None, "", ("2606:4700:4700::1111", 0, 0, 0)),
        (None, None, None, "", ("10.0.0.3", 0)),
    ])
    beweis._host_oeffentlich.cache_clear()
    assert beweis.ist_oeffentliche_url("https://gemischt.example/") is False


def test_unaufloesbarer_host_wird_abgelehnt(monkeypatch):
    def fehler(host, port):
        raise OSError("NXDOMAIN")
    monkeypatch.setattr(beweis.socket, "getaddrinfo", fehler)
    beweis._host_oeffentlich.cache_clear()
    assert beweis.ist_oeffentliche_url("https://gibt-es-nicht.example/") is False


def test_interner_docker_dienstname_wird_abgelehnt(monkeypatch):
    monkeypatch.setattr(beweis.socket, "getaddrinfo",
                        lambda host, port: [(None, None, None, "", ("172.18.0.4", 0))])
    beweis._host_oeffentlich.cache_clear()
    assert beweis.ist_oeffentliche_url("http://n8n:5678/rest/workflows") is False


def test_erfasse_oeffnet_interne_quelle_gar_nicht():
    assert beweis.erfasse("http://127.0.0.1:8788/health", []) is None
