from nexus.master.decision import decide


def _i(m):
    return decide(m).get("intent")


def test_rechenfrage_ist_kein_systemstatus():
    assert _i("Wie viel ist 2+3? Nur die Zahl.") != "monitor"
    assert _i("Wie viel Uhr ist es in Tokio?") != "monitor"


def test_echte_statusfragen_bleiben_monitor():
    for m in ("Wie ist der Systemstatus?", "Wie viel RAM ist frei?", "Wie hoch ist die CPU-Auslastung?",
              "Wieviel Speicher ist auf der Festplatte frei?", "GPU Auslastung"):
        assert _i(m) == "monitor", m