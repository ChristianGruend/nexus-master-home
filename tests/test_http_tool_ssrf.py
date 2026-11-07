import socket
import unittest
from unittest import mock

from nexus.common import net_guard
from nexus.computer import http_tool
from nexus.integrations.aether_supervisor import t_fetch_url


def _make_getaddrinfo(ip: str):
    def getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]
    return getaddrinfo


class TestHttpToolSSRF(unittest.TestCase):
    def test_request_blocks_loopback(self):
        with mock.patch.object(net_guard.socket, "getaddrinfo", side_effect=_make_getaddrinfo("127.0.0.1")):
            res = http_tool.request("GET", "http://127.0.0.1:8299/api/contacts")
        self.assertFalse(res["success"])
        self.assertEqual(res["error"], "http_target_not_allowed")

    def test_request_blocks_cgnat(self):
        with mock.patch.object(net_guard.socket, "getaddrinfo", side_effect=_make_getaddrinfo("100.64.0.7")):
            res = http_tool.request("GET", "http://100.64.0.7/")
        self.assertFalse(res["success"])
        self.assertEqual(res["error"], "http_target_not_allowed")

    def test_t_fetch_url_blocks_private(self):
        with mock.patch.object(net_guard.socket, "getaddrinfo", side_effect=_make_getaddrinfo("192.168.178.1")):
            with self.assertRaises(PermissionError) as ctx:
                t_fetch_url("http://intern.example/")
        self.assertIn("PRIVATE_NET_DENIED", str(ctx.exception))
