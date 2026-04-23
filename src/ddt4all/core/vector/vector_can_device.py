"""Vector CAN backend for DDT4All.

Provides the same duck-typed interface as ``ddt4all.core.elm.elm.ELM``
so that ``options.elm`` can be set to a :class:`VectorCanDevice` and the
rest of DDT4All works without modification.

Dependencies (optional install group ``[can]``):
  * python-can >= 4.0  (``pip install python-can``)
  * isotp >= 2.0       (``pip install isotp``)
  * Vector XL Driver Library installed on the host OS

Usage::

    from ddt4all.core.vector.vector_can_device import VectorCanDevice
    import ddt4all.options as options

    options.elm = VectorCanDevice(channel=0, bitrate=500000)
    if options.elm_failed:
        ...  # show error
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Optional, Tuple

import ddt4all.options as options
from ddt4all.core.elm.elm import (
    dnat,
    dnat_ext,
    get_can_addr,
    get_can_addr_ext,
    get_can_addr_snat,
    get_can_addr_snat_ext,
    negrsp,
)
from ddt4all.file_manager import get_logs_dir

_ = options.translator("ddt4all")

# ---------------------------------------------------------------------------
# Guard: fail gracefully if python-can / isotp are missing
# ---------------------------------------------------------------------------
try:
    import can  # type: ignore
    import isotp  # type: ignore
    _DEPS_AVAILABLE = True
except ImportError as _import_err:
    _DEPS_AVAILABLE = False
    _IMPORT_ERR_MSG = str(_import_err)


class VectorCanDevice:
    """DDT4All backend for Vector VN CAN hardware via python-can + ISO-TP.

    Public interface mirrors :class:`ddt4all.core.elm.elm.ELM`:

    * ``connectionStatus``   bool
    * ``init_can()``
    * ``set_can_addr(addr, ecu, canline=0)``   → (TXa, RXa)
    * ``start_session_can(start_session)``      → bool
    * ``request(req, positive, cache, serviceDelay)`` → hex-str
    * ``clear_cache()``
    * ``close_protocol()``
    * ``rsp_cache``   dict
    """

    # ------------------------------------------------------------------
    # ELM compatibility attributes
    # ------------------------------------------------------------------
    connectionStatus: bool = False
    adapter_type: str = "VECTOR"
    currentaddress: str = ""
    startSession: str = ""
    rsp_cache: dict
    l1_cache: dict

    # Timing / keep-alive (mirrors ELM defaults)
    keepAlive: float = 4.0        # seconds between keep-alive transmissions
    busLoad: float = 0.0
    srvsDelay: float = 0.0
    lastCMDtime: float = 0.0

    # Error counters
    error_frame: int = 0

    def __init__(
        self,
        channel: int = 0,
        bitrate: int = 500000,
        app_name: str = "DDT4All",
        rx_queue_size: int = 16384,
    ) -> None:
        self.channel = channel
        self.bitrate = bitrate
        self.app_name = app_name
        self.rx_queue_size = rx_queue_size

        self.rsp_cache = {}
        self.l1_cache = {}

        self._bus: Optional["can.BusABC"] = None
        self._stack: Optional["isotp.CanStack"] = None
        self._ka_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

        # Log file handles (same convention as ELM)
        self._lf = 0  # elm_*.txt
        self._vf = 0  # ecu_*.txt

        if not _DEPS_AVAILABLE:
            msg = _(
                "Vector backend requires python-can and isotp.\n"
                "Run: pip install ddt4all[can]\n"
                f"Error: {_IMPORT_ERR_MSG}"
            )
            print(msg)
            options.elm_failed = True
            options.last_error = msg
            return

        try:
            self._bus = can.Bus(
                interface="vector",
                channel=self.channel,
                bitrate=self.bitrate,
                app_name=None,  # None = use global channel index directly, no Vector HW Config registration needed
                rx_queue_size=self.rx_queue_size,
            )
            self.connectionStatus = True
            options.elm_failed = False
            options.last_error = ""
            print(
                _(f"Vector CAN connected: channel={channel}, bitrate={bitrate}")
            )
            self._open_log_files()
        except Exception as exc:
            msg = _("Vector CAN connection failed: ") + str(exc)
            print(msg)
            options.elm_failed = True
            options.last_error = msg
            self.connectionStatus = False

    # ------------------------------------------------------------------
    # Log files (same layout as ELM)
    # ------------------------------------------------------------------

    def _open_log_files(self) -> None:
        log_name = options.log if options.log else "ddt"
        logs_dir = get_logs_dir()
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir, exist_ok=True)
        try:
            self._lf = open(
                os.path.join(logs_dir, f"elm_{log_name}.txt"), "at", encoding="utf-8"
            )
            self._vf = open(
                os.path.join(logs_dir, f"ecu_{log_name}.txt"), "at", encoding="utf-8"
            )
            self._vf.write("# TimeStamp;Address;Command;Response;Error\n")
        except Exception as exc:
            print(f"Warning: could not open Vector log files: {exc}")

    def _log(self, msg: str) -> None:
        if self._lf:
            self._lf.write(msg)
            self._lf.flush()

    # ------------------------------------------------------------------
    # DDT4All device interface
    # ------------------------------------------------------------------

    def init_can(self) -> None:
        """Reset internal state to prepare for CAN communication."""
        self.currentaddress = ""
        self.startSession = ""
        self.lastCMDtime = 0.0
        self.l1_cache = {}
        self._cancel_keepalive()
        if self._stack is not None:
            self._stack = None

        if self._lf:
            tmstr = datetime.now().strftime("%x %H:%M:%S.%f")[:-3]
            self._log(
                "#" * 60 + f"\n# [{tmstr}] Init CAN (Vector CH{self.channel})\n" + "#" * 60 + "\n"
            )

    def set_can_addr(self, addr: str, ecu: dict, canline: int = 0) -> Optional[Tuple[str, str]]:
        """Configure ISO-TP stack TX/RX for the given ECU address.

        Mirrors the address-lookup logic of :meth:`ELM.set_can_addr`.
        Returns ``(TXa, RXa)`` hex strings or *None* if the address is
        unknown.
        """
        if self.currentaddress == addr:
            return None  # already configured

        # Resolve TX / RX CAN IDs (mirrors elm.py logic)
        TXa: Optional[str] = None
        RXa: Optional[str] = None

        if "idTx" in ecu and "idRx" in ecu:
            TXa = ecu["idTx"]
            RXa = ecu["idRx"]
            self.currentaddress = get_can_addr(TXa) or TXa
        elif get_can_addr(addr) is not None and get_can_addr_snat(addr) is not None:
            TXa = get_can_addr(addr)
            RXa = get_can_addr_snat(addr)
            self.currentaddress = TXa
        elif get_can_addr_ext(addr) is not None and get_can_addr_snat_ext(addr) is not None:
            TXa = get_can_addr_ext(addr)
            RXa = get_can_addr_snat_ext(addr)
            self.currentaddress = TXa
        else:
            return None

        extended_can = len(RXa) == 8  # 29-bit if 8 hex chars

        # Convert hex string IDs to integers
        tx_id = int(TXa, 16)
        rx_id = int(RXa, 16)

        addressing_mode = (
            isotp.AddressingMode.Extended_29bits
            if extended_can
            else isotp.AddressingMode.Normal_11bits
        )

        # Build ISO-TP address pair
        isotp_address = isotp.Address(
            addressing_mode=addressing_mode,
            txid=tx_id,
            rxid=rx_id,
        )

        self.startSession = ""
        self.lastCMDtime = 0.0
        self.l1_cache = {}
        self._cancel_keepalive()

        # (Re)create the ISO-TP stack
        self._stack = isotp.CanStack(
            bus=self._bus,
            address=isotp_address,
            params={
                "stmin": 0,
                "blocksize": 0,
                "tx_padding": None,
            },
        )

        if self._lf:
            ecuname = ecu.get("ecuname", addr)
            tmstr = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self._log(
                "#" * 60
                + f"\n# [{tmstr}] Connect to: [{ecuname}] Addr: {addr} "
                + f"TX={TXa} RX={RXa} {'29-bit' if extended_can else '11-bit'}\n"
                + "#" * 60 + "\n"
            )

        return TXa, RXa

    def start_session_can(self, start_session: str) -> bool:
        """Send the start-session UDS command and check for positive response (0x50)."""
        self.startSession = start_session
        response = self._raw_request(start_session)
        result = response.startswith("50")
        if result:
            self._schedule_keepalive()
        return result

    def request(
        self,
        req: str,
        positive: str = "",
        cache: bool = True,
        serviceDelay: str = "0",
    ) -> str:
        """Send a UDS request and return the response as a DDT4All hex string.

        Implements the same L2-cache / logging behaviour as :meth:`ELM.request`.
        """
        if cache and req in self.rsp_cache:
            return self.rsp_cache[req]

        # Keep-alive bookkeeping
        now = time.time()
        if (now - self.lastCMDtime) > self.keepAlive and self.startSession:
            self._raw_request(self.startSession)

        self.srvsDelay = float(serviceDelay) / 1000.0

        rsp = self._raw_request(req)
        self.lastCMDtime = time.time()

        # Populate cache
        self.rsp_cache[req] = rsp

        # Logging
        self._log_response(req, rsp)

        return rsp

    def clear_cache(self) -> None:
        """Invalidate L2 response cache."""
        self.rsp_cache = {}

    def close_protocol(self) -> None:
        """Tear down the ISO-TP stack and cancel keep-alive timer."""
        self._cancel_keepalive()
        self._stack = None
        self.currentaddress = ""
        self.startSession = ""

    def __del__(self) -> None:
        self._cancel_keepalive()
        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:
                pass
        for fh in (self._lf, self._vf):
            if fh and fh != 0:
                try:
                    fh.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raw_request(self, req: str, timeout: float = 4.0) -> str:
        """Send *req* (hex string) via ISO-TP and return the decoded response.

        Returns a space-separated uppercase hex string compatible with the
        rest of DDT4All, e.g. ``"67 F1 8B 00 01"``.
        Returns an empty string on timeout / error.
        """
        if self._stack is None:
            return ""

        req_clean = req.strip().replace(" ", "")
        if not req_clean:
            return ""

        try:
            payload = bytes.fromhex(req_clean)
        except ValueError as exc:
            print(f"Vector: invalid hex request '{req}': {exc}")
            return ""

        deadline = time.time() + timeout

        with self._lock:
            try:
                self._stack.send(payload)
            except Exception as exc:
                print(f"Vector ISO-TP send error: {exc}")
                return ""

            # Pump the stack until a complete PDU is received or we time out
            while time.time() < deadline:
                self._stack.process()
                if self._stack.available():
                    data = self._stack.recv()
                    if data is not None:
                        hex_str = " ".join(f"{b:02X}" for b in data)
                        return hex_str
                time.sleep(0.001)

        print(f"Vector ISO-TP timeout for request: {req}")
        return ""

    def _log_response(self, req: str, rsp: str) -> None:
        if not self._vf:
            return
        errorstr = "Unknown"
        rsp_clean = rsp.replace(" ", "")
        if len(rsp_clean) >= 8 and rsp_clean[:2] == "7F":
            err_code = rsp_clean[4:6].upper()
            errorstr = negrsp.get(err_code, "Unknown")

        tmstr = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        addr_label = ""
        if self.currentaddress in dnat_ext and len(self.currentaddress) == 8:
            addr_label = dnat_ext[self.currentaddress]
        elif self.currentaddress in dnat:
            addr_label = dnat[self.currentaddress]
        else:
            addr_label = self.currentaddress

        line = f"{tmstr};{addr_label};{req.replace(' ', '')};{rsp};{errorstr}\n"
        self._vf.write(line)
        self._vf.flush()

    # ------------------------------------------------------------------
    # Keep-alive
    # ------------------------------------------------------------------

    def _schedule_keepalive(self) -> None:
        """Schedule periodic keep-alive (re-sends startSession)."""
        self._cancel_keepalive()
        if not self.startSession:
            return
        self._ka_timer = threading.Timer(
            self.keepAlive, self._keepalive_tick
        )
        self._ka_timer.daemon = True
        self._ka_timer.start()

    def _keepalive_tick(self) -> None:
        if self.startSession and self.connectionStatus:
            self._raw_request(self.startSession)
            tmstr = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self._log(f"# [{tmstr}] KeepAlive\n")
        self._schedule_keepalive()  # reschedule

    def _cancel_keepalive(self) -> None:
        if self._ka_timer is not None:
            self._ka_timer.cancel()
            self._ka_timer = None
