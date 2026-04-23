"""Utility helpers for Vector XL-Driver-Library hardware discovery.

Uses python-can's Vector bus implementation to enumerate attached
Vector CAN hardware (VN1610, VN1630, VN5610, VN7600, VN16xx …).

Returns an empty list without raising if the Vector driver or
python-can is not installed, so the rest of DDT4All starts cleanly.
"""

from __future__ import annotations

from typing import List, Tuple

# XL Driver Library bus-capability bit flags (from vxlapi.h)
_XL_BUS_COMPATIBLE_CAN = 0x0001   # bit 0: channel supports CAN


def enumerate_vector_channels() -> List[Tuple[str, int]]:
    """Return ``[(display_name, channel_index), …]`` for each available
    Vector CAN channel.

    ``display_name`` is a human-readable string, e.g.
    ``"VN7610 Channel 2 (S/N 7377) CAN"``.
    ``channel_index`` is the zero-based integer passed to
    :class:`can.interfaces.vector.VectorBus` as *channel*.

    Only channels that have CAN bus capability (XL_BUS_COMPATIBLE_CAN)
    are included.  Returns an empty list when python-can, the Vector XL
    Driver Library, or any CAN-capable hardware is absent.
    """
    try:
        # get_channel_configs is a module-level function, NOT a class method
        from can.interfaces.vector import get_channel_configs  # type: ignore
        configs = get_channel_configs()
        result: List[Tuple[str, int]] = []
        for cfg in configs:
            try:
                bus_caps = int(cfg.channel_bus_capabilities)
                # Skip channels without CAN capability
                if not (bus_caps & _XL_BUS_COMPATIBLE_CAN):
                    continue

                name = cfg.name or f"Vector CH{cfg.channel_index}"
                serial = cfg.serial_number
                label = f"{name} (S/N {serial})"
                result.append((label, int(cfg.channel_index)))
            except Exception:
                continue
        return result
    except ImportError:
        return []
    except Exception:
        # Vector driver not installed, hardware not found, etc.
        return []


def is_vector_available() -> bool:
    """Return *True* if at least one Vector CAN channel is accessible."""
    try:
        return len(enumerate_vector_channels()) > 0
    except Exception:
        return False

