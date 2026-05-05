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
        """If Tailscale API returns ExitNodeID, mode should be full_tunnel."""
        status_payload = json.dumps({"ExitNodeID": "abc123"}).encode()

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
    def test_exit_node_id_present_returns_true(self) -> None:
        payload = json.dumps({"ExitNodeID": "nodeid-xyz"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        result = _is_tailscale_full_tunnel(_open=lambda *a, **kw: mock_resp)
        assert result is True

    def test_exit_node_id_empty_returns_false(self) -> None:
        payload = json.dumps({"ExitNodeID": ""}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        result = _is_tailscale_full_tunnel(_open=lambda *a, **kw: mock_resp)
        assert result is False

    def test_connection_refused_returns_false(self) -> None:
        result = _is_tailscale_full_tunnel(
            _open=lambda *a, **kw: (_ for _ in ()).throw(OSError("refused"))
        )
        assert result is False
