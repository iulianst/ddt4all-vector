"""Unit tests for VectorCanDevice.

These tests mock python-can and isotp so they run without Vector hardware
or the Vector XL Driver Library installed.
"""

import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Patch can and isotp at module import time so VectorCanDevice can be imported.
mock_can = MagicMock()
mock_isotp = MagicMock()

# Minimal isotp.AddressingMode enum-like mock
mock_isotp.AddressingMode.Normal_11bits = 0
mock_isotp.AddressingMode.Extended_29bits = 1


@pytest.fixture(autouse=True)
def patch_deps(monkeypatch):
    """Ensure can and isotp are mocked before each test."""
    monkeypatch.setitem(__import__("sys").modules, "can", mock_can)
    monkeypatch.setitem(__import__("sys").modules, "isotp", mock_isotp)
    # Reset per-test so side-effects don't bleed
    mock_can.reset_mock()
    mock_isotp.reset_mock()
    mock_isotp.AddressingMode.Normal_11bits = 0
    mock_isotp.AddressingMode.Extended_29bits = 1
    yield


def _make_device(channel=0, bitrate=500000, **kwargs):
    """Helper: create a VectorCanDevice with mocked bus."""
    mock_bus_instance = MagicMock()
    mock_can.Bus.return_value = mock_bus_instance

    # Re-import after patching to pick up fresh mocks
    import importlib
    import ddt4all.core.vector.vector_can_device as m
    importlib.reload(m)

    dev = m.VectorCanDevice(channel=channel, bitrate=bitrate, **kwargs)
    return dev, mock_bus_instance


class TestVectorCanDeviceInit:
    def test_successful_connection_sets_flags(self):
        dev, bus = _make_device(channel=1, bitrate=250000)
        assert dev.connectionStatus is True
        assert dev.channel == 1
        assert dev.bitrate == 250000
        mock_can.Bus.assert_called_once_with(
            interface="vector",
            channel=1,
            bitrate=250000,
            app_name="DDT4All",
            rx_queue_size=16384,
        )

    def test_bus_exception_marks_failed(self):
        mock_can.Bus.side_effect = Exception("no hardware")
        import importlib, ddt4all.core.vector.vector_can_device as m
        importlib.reload(m)
        dev = m.VectorCanDevice()
        assert dev.connectionStatus is False
        mock_can.Bus.side_effect = None  # reset

    def test_rsp_cache_starts_empty(self):
        dev, _ = _make_device()
        assert dev.rsp_cache == {}


class TestInitCan:
    def test_init_can_resets_state(self):
        dev, _ = _make_device()
        dev.currentaddress = "7E0"
        dev.startSession = "1003"
        dev.l1_cache = {"foo": "bar"}
        dev.init_can()
        assert dev.currentaddress == ""
        assert dev.startSession == ""
        assert dev.l1_cache == {}


class TestSetCanAddr:
    def test_11bit_addressing(self):
        dev, _ = _make_device()

        # Patch elm address resolution helpers
        with patch("ddt4all.core.vector.vector_can_device.get_can_addr", return_value="7E0"), \
             patch("ddt4all.core.vector.vector_can_device.get_can_addr_snat", return_value="7E8"), \
             patch("ddt4all.core.vector.vector_can_device.get_can_addr_ext", return_value=None), \
             patch("ddt4all.core.vector.vector_can_device.get_can_addr_snat_ext", return_value=None):

            result = dev.set_can_addr("04", {})

        assert result is not None
        tx, rx = result
        assert tx == "7E0"
        assert rx == "7E8"
        # Should use Normal_11bits since RXa is 3 chars
        mock_isotp.Address.assert_called()
        call_kwargs = mock_isotp.Address.call_args[1]
        assert call_kwargs["addressing_mode"] == mock_isotp.AddressingMode.Normal_11bits
        assert call_kwargs["txid"] == 0x7E0
        assert call_kwargs["rxid"] == 0x7E8

    def test_29bit_addressing_for_8char_id(self):
        dev, _ = _make_device()

        with patch("ddt4all.core.vector.vector_can_device.get_can_addr", return_value=None), \
             patch("ddt4all.core.vector.vector_can_device.get_can_addr_snat", return_value=None), \
             patch("ddt4all.core.vector.vector_can_device.get_can_addr_ext", return_value="000007E4"), \
             patch("ddt4all.core.vector.vector_can_device.get_can_addr_snat_ext", return_value="000007EC"):

            result = dev.set_can_addr("04", {})

        assert result is not None
        tx, rx = result
        assert tx == "000007E4"
        assert rx == "000007EC"
        call_kwargs = mock_isotp.Address.call_args[1]
        assert call_kwargs["addressing_mode"] == mock_isotp.AddressingMode.Extended_29bits

    def test_unknown_addr_returns_none(self):
        dev, _ = _make_device()

        with patch("ddt4all.core.vector.vector_can_device.get_can_addr", return_value=None), \
             patch("ddt4all.core.vector.vector_can_device.get_can_addr_snat", return_value=None), \
             patch("ddt4all.core.vector.vector_can_device.get_can_addr_ext", return_value=None), \
             patch("ddt4all.core.vector.vector_can_device.get_can_addr_snat_ext", return_value=None):

            result = dev.set_can_addr("FF", {})

        assert result is None

    def test_already_configured_addr_returns_none(self):
        dev, _ = _make_device()
        # currentaddress is compared against the addr parameter (not the resolved CAN ID)
        dev.currentaddress = "04"

        result = dev.set_can_addr("04", {})

        # same address string → short-circuit, no CAN-ID lookups needed
        assert result is None

    def test_ecu_dict_id_override(self):
        dev, _ = _make_device()
        ecu = {"idTx": "7A0", "idRx": "7A8", "ecuname": "TEST"}

        with patch("ddt4all.core.vector.vector_can_device.get_can_addr", return_value="7E0"):
            result = dev.set_can_addr("00", ecu)

        assert result == ("7A0", "7A8")


class TestStartSessionCan:
    def _make_dev_with_stack(self):
        dev, _ = _make_device()
        mock_stack = MagicMock()
        dev._stack = mock_stack
        return dev, mock_stack

    def test_positive_response_returns_true(self):
        dev, mock_stack = self._make_dev_with_stack()
        mock_stack.available.return_value = True
        mock_stack.recv.return_value = bytes.fromhex("5003")

        result = dev.start_session_can("1003")

        assert result is True

    def test_negative_response_returns_false(self):
        dev, mock_stack = self._make_dev_with_stack()
        mock_stack.available.return_value = True
        mock_stack.recv.return_value = bytes.fromhex("7F1031")

        result = dev.start_session_can("1003")

        assert result is False

    def test_timeout_returns_false(self):
        dev, mock_stack = self._make_dev_with_stack()
        mock_stack.available.return_value = False  # never available → timeout

        # Patch time so the test doesn't actually wait
        with patch("ddt4all.core.vector.vector_can_device.time") as mock_time:
            mock_time.time.side_effect = [0.0, 0.0, 5.0]  # start, poll, timeout
            mock_time.sleep = MagicMock()
            result = dev.start_session_can("1003")

        assert result is False


class TestRequest:
    def _make_dev_with_stack(self, recv_bytes=None):
        dev, _ = _make_device()
        mock_stack = MagicMock()
        dev._stack = mock_stack
        if recv_bytes is not None:
            mock_stack.available.return_value = True
            mock_stack.recv.return_value = recv_bytes
        return dev, mock_stack

    def test_cache_hit_skips_send(self):
        dev, mock_stack = self._make_dev_with_stack()
        dev.rsp_cache["2102"] = "62 02 AA"

        result = dev.request("2102", cache=True)

        assert result == "62 02 AA"
        mock_stack.send.assert_not_called()

    def test_response_stored_in_cache(self):
        dev, mock_stack = self._make_dev_with_stack(recv_bytes=bytes([0x62, 0x01, 0xFF]))

        result = dev.request("2101", cache=True)

        assert result == "62 01 FF"
        assert dev.rsp_cache["2101"] == "62 01 FF"

    def test_no_cache_flag_bypasses_cache(self):
        dev, mock_stack = self._make_dev_with_stack(recv_bytes=bytes([0x62, 0x01, 0x00]))
        dev.rsp_cache["2101"] = "OLD"

        result = dev.request("2101", cache=False)

        mock_stack.send.assert_called_once()
        assert result == "62 01 00"


class TestClearCache:
    def test_clear_cache_empties_rsp_cache(self):
        dev, _ = _make_device()
        dev.rsp_cache = {"2101": "FF", "2102": "AA"}
        dev.clear_cache()
        assert dev.rsp_cache == {}


class TestCloseProtocol:
    def test_close_resets_state(self):
        dev, _ = _make_device()
        dev.currentaddress = "7E0"
        dev.startSession = "1003"
        dev._stack = MagicMock()

        dev.close_protocol()

        assert dev.currentaddress == ""
        assert dev.startSession == ""
        assert dev._stack is None
