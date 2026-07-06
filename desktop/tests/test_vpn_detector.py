"""Tests for gatepath.vpn_detector — injectable socket and opener."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from gatepath.vpn_detector import detect_vpn_interfaces, _is_tailscale_full_tunnel


class TestDetectVpnInterfaces:
    def test_empty_interface_list_returns_empty(self) -> None:
        with patch("socket.if_nameindex", return_value=[]):
            result = detect_vpn_interfaces()
        assert result == []

    def test_loopback_only_returns_empty(self) -> None:
        with patch("socket.if_nameindex", return_value=[(1, "lo"), (2, "eth0")]):
            result = detect_vpn_interfaces()
        assert result == []

    def test_tailscale_interface_detected(self) -> None:
        """tailscale0 should be detected as VPN (split_tunnel when API unreachable)."""
        with (
            patch("socket.if_nameindex", return_value=[(1, "lo"), (99, "tailscale0")]),
            patch("urllib.request.urlopen", side_effect=OSError("connection refused")),
        ):
            result = detect_vpn_interfaces()
        assert len(result) == 1
        assert "tailscale0" in result[0]
        assert "split_tunnel" in result[0]

    def test_tun_interface_detected_as_unknown(self) -> None:
        with patch("socket.if_nameindex", return_value=[(1, "lo"), (10, "tun0")]):
            result = detect_vpn_interfaces()
        assert len(result) == 1
        assert "tun0" in result[0]
        assert "unknown" in result[0]

    def test_wg_interface_detected_as_unknown(self) -> None:
        with patch("socket.if_nameindex", return_value=[(5, "wg0")]):
            result = detect_vpn_interfaces()
        assert len(result) == 1
        assert "wg0" in result[0]

    def test_tailscale_full_tunnel_when_exit_node_active(self) -> None:
        """A nested ExitNodeStatus.ID means the interface mode is full_tunnel."""
        status_payload = json.dumps(
            {"BackendState": "Running", "ExitNodeStatus": {"ID": "abc123", "Online": True}}
        ).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = status_payload
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        def fake_open(req, timeout=None):
            return mock_resp

        with patch("socket.if_nameindex", return_value=[(99, "tailscale0")]):
            result = detect_vpn_interfaces(_open=fake_open)

        assert len(result) == 1
        assert "tailscale0 (full_tunnel)" == result[0]

    def test_os_error_on_if_nameindex_returns_empty(self) -> None:
        with patch("socket.if_nameindex", side_effect=OSError("not supported")):
            result = detect_vpn_interfaces()
        assert result == []


class TestIsTailscaleFullTunnel:
    @staticmethod
    def _open_returning(payload: object):
        """An opener whose response yields *payload* as a JSON /v0/status body."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return lambda *a, **kw: mock_resp

    def test_active_exit_node_returns_true(self) -> None:
        opener = self._open_returning(
            {"BackendState": "Running", "ExitNodeStatus": {"ID": "nodeid-xyz", "Online": True}}
        )
        assert _is_tailscale_full_tunnel(_open=opener) is True

    def test_selected_offline_exit_node_returns_true(self) -> None:
        # Selected but unreachable: traffic is still routed through it — warn.
        opener = self._open_returning({"ExitNodeStatus": {"ID": "nodeid-xyz", "Online": False}})
        assert _is_tailscale_full_tunnel(_open=opener) is True

    def test_empty_exit_node_id_returns_false(self) -> None:
        opener = self._open_returning({"ExitNodeStatus": {"ID": ""}})
        assert _is_tailscale_full_tunnel(_open=opener) is False

    def test_no_exit_node_status_returns_false(self) -> None:
        opener = self._open_returning({"BackendState": "Running", "Self": {"ID": "abc"}})
        assert _is_tailscale_full_tunnel(_open=opener) is False

    def test_null_exit_node_status_returns_false(self) -> None:
        opener = self._open_returning({"ExitNodeStatus": None})
        assert _is_tailscale_full_tunnel(_open=opener) is False

    def test_non_object_exit_node_status_returns_false(self) -> None:
        opener = self._open_returning({"ExitNodeStatus": "unexpected"})
        assert _is_tailscale_full_tunnel(_open=opener) is False

    def test_non_string_exit_node_id_returns_false(self) -> None:
        # A StableNodeID is always a string; a non-string value must not be
        # treated as a live exit node (matches Android's primitive-string check).
        opener = self._open_returning({"ExitNodeStatus": {"ID": {"unexpected": "dict"}}})
        assert _is_tailscale_full_tunnel(_open=opener) is False

    def test_missing_exit_node_id_returns_false(self) -> None:
        opener = self._open_returning({"ExitNodeStatus": {"Online": True}})
        assert _is_tailscale_full_tunnel(_open=opener) is False

    def test_null_exit_node_id_returns_false(self) -> None:
        opener = self._open_returning({"ExitNodeStatus": {"ID": None}})
        assert _is_tailscale_full_tunnel(_open=opener) is False

    def test_non_object_toplevel_body_returns_false(self) -> None:
        # A valid-JSON but non-object body (e.g. a bare array) must fail safe
        # rather than crash the caller via AttributeError on data.get(...).
        opener = self._open_returning([1, 2, 3])
        assert _is_tailscale_full_tunnel(_open=opener) is False

    def test_connection_refused_returns_false(self) -> None:
        result = _is_tailscale_full_tunnel(
            _open=lambda *a, **kw: (_ for _ in ()).throw(OSError("refused"))
        )
        assert result is False
