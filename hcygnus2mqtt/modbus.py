"""Petite couche au-dessus de pymodbus (compatible 3.6 → 3.x récents)."""
from __future__ import annotations

import inspect
import logging
import threading
import time

from pymodbus.client import ModbusSerialClient, ModbusTcpClient

log = logging.getLogger(__name__)


class ModbusError(Exception):
    """Échec Modbus. `exception_code` est renseigné si l'esclave a répondu
    par une exception (la liaison fonctionne mais l'adresse/fonction est refusée)."""

    def __init__(self, msg: str, exception_code: int | None = None) -> None:
        super().__init__(msg)
        self.exception_code = exception_code

    @property
    def answered(self) -> bool:
        return self.exception_code is not None


class Modbus:
    def __init__(self, cfg: dict, unit: int) -> None:
        self.unit = unit
        self.cfg = cfg
        self.lock = threading.Lock()
        self.min_interval = float(cfg.get("inter_request_delay", 0.05))
        self._last = 0.0
        if cfg.get("type", "rtu") == "tcp":
            # utile avec une passerelle RS485/Ethernet (Waveshare, USR, Elfin…)
            self.client = ModbusTcpClient(
                cfg["host"], port=int(cfg.get("port", 502)), timeout=float(cfg.get("timeout", 1.0))
            )
        else:
            self.client = ModbusSerialClient(
                port=cfg.get("port", "/dev/ttyUSB0"),
                baudrate=int(cfg.get("baudrate", 9600)),
                bytesize=int(cfg.get("bytesize", 8)),
                parity=str(cfg.get("parity", "N")),
                stopbits=int(cfg.get("stopbits", 1)),
                timeout=float(cfg.get("timeout", 1.0)),
            )
        # pymodbus a renommé 'slave' en 'device_id' (3.10+) : on détecte la bonne clé.
        params = inspect.signature(self.client.read_holding_registers).parameters
        self._unit_kw = "device_id" if "device_id" in params else "slave"

    def connect(self) -> bool:
        return bool(self.client.connect())

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------------ internes
    def _call(self, fn, address: int, **kwargs):
        with self.lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                if not self.client.connected:
                    self.client.connect()
                rr = fn(address, **kwargs, **{self._unit_kw: self.unit})
            except Exception as exc:  # erreurs de port, timeouts, CRC…
                raise ModbusError(str(exc)) from exc
            finally:
                self._last = time.monotonic()
        if rr is None or rr.isError():
            code = getattr(rr, "exception_code", None)
            raise ModbusError(f"réponse en erreur @ {address}: {rr}", code)
        return rr

    # ------------------------------------------------------------------ lecture
    def read(self, table: str, address: int, count: int) -> list[int]:
        if table == "holding":
            return list(self._call(self.client.read_holding_registers, address, count=count).registers)[:count]
        if table == "input":
            return list(self._call(self.client.read_input_registers, address, count=count).registers)[:count]
        if table == "coil":
            return [int(b) for b in self._call(self.client.read_coils, address, count=count).bits[:count]]
        if table == "discrete":
            return [int(b) for b in self._call(self.client.read_discrete_inputs, address, count=count).bits[:count]]
        raise ValueError(table)

    # ------------------------------------------------------------------ écriture
    def write(self, table: str, address: int, words: list[int]) -> None:
        if table == "coil":
            self._call(self.client.write_coil, address, value=bool(words[0]))
        elif table == "holding":
            # Les régulateurs Dixell attendent la fonction 16 (write multiple),
            # même pour un seul registre.
            self._call(self.client.write_registers, address, values=list(words))
        else:
            raise ModbusError(f"table {table} en lecture seule")
