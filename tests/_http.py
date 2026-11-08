"""Gemeinsamer HTTP-Testclient (Stufe 4.6): echte Starlette-App ohne Serverstart.

``nexus_client(ip=…)`` setzt die Peer-Adresse (Loopback-Prüfungen), ``engine=…`` ersetzt die Engine in allen
Routenmodulen für die Dauer des Blocks.
"""
from __future__ import annotations

import sys
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from starlette.testclient import TestClient

LOCAL = "127.0.0.1"
REMOTE = "100.64.0.5"


@contextmanager
def nexus_client(ip: str = LOCAL, engine=None):
    from nexus.chat import server
    app = server.create_app()
    with ExitStack() as stack:
        if engine is not None:
            for name, mod in list(sys.modules.items()):
                if name.startswith("nexus.chat.routes") and hasattr(mod, "ENGINE"):
                    stack.enter_context(patch.object(mod, "ENGINE", engine))
        yield stack.enter_context(TestClient(app, base_url="http://localhost", client=(ip, 12345)))
