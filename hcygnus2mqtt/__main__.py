"""Point d'entrée : `hcygnus2mqtt <commande>` ou `python -m hcygnus2mqtt <commande>`."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, explore
from .modbus import Modbus


def _serial_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--port", default="/dev/ttyUSB0", help="port série (défaut /dev/ttyUSB0)")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parity", default="N", choices=["N", "E", "O"])
    p.add_argument("--unit", type=int, default=1, help="adresse esclave Modbus")
    p.add_argument("--timeout", type=float, default=0.5)


def _modbus(a) -> Modbus:
    mb = Modbus({"port": a.port, "baudrate": a.baud, "parity": a.parity, "timeout": a.timeout}, a.unit)
    if not mb.connect():
        sys.exit(f"Impossible d'ouvrir {a.port}")
    return mb


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hcygnus2mqtt", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="lance la passerelle MQTT")
    p.add_argument("-c", "--config", default="config.yaml")

    p = sub.add_parser("probe", help="cherche les paramètres série qui répondent")
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--units", default="1-5", help="plage d'adresses esclaves, ex. 1-10")
    p.add_argument("--bauds", default="9600,19200,38400")
    p.add_argument("--parities", default="N,E")
    p.add_argument("--timeout", type=float, default=0.3)

    p = sub.add_parser("scan", help="balaye des plages d'adresses")
    _serial_args(p)
    p.add_argument("ranges", nargs="+", help="ex. holding:0-511 input:0-255 coil:0-127")
    p.add_argument("--chunk", type=int, default=16)
    p.add_argument("-o", "--out", type=Path, help="fichier CSV de sortie")

    p = sub.add_parser("watch", help="affiche en direct les registres qui changent")
    _serial_args(p)
    p.add_argument("ranges", nargs="+")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--log", type=Path, help="journalise les changements dans un CSV")
    p.add_argument("--noise", type=int, default=0, help="ignore les variations ≤ N (ex. 2 pour les sondes)")

    p = sub.add_parser("diff", help="compare deux scans CSV")
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)

    p = sub.add_parser("read", help="lit quelques registres")
    _serial_args(p)
    p.add_argument("table", choices=["holding", "input", "coil", "discrete"])
    p.add_argument("address", type=lambda s: int(s, 0))
    p.add_argument("count", type=int, nargs="?", default=1)

    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    if not a.verbose:
        logging.getLogger("pymodbus").setLevel(logging.CRITICAL)

    if a.cmd == "run":
        from .bridge import Bridge
        Bridge(a.config).run()
        return 0
    if a.cmd == "probe":
        lo, _, hi = a.units.partition("-")
        units = list(range(int(lo), int(hi or lo) + 1))
        return explore.probe(a.port, units, [int(b) for b in a.bauds.split(",")],
                             a.parities.split(","), a.timeout)
    if a.cmd == "diff":
        return explore.diff(a.before, a.after)

    mb = _modbus(a)
    try:
        if a.cmd == "scan":
            explore.scan(mb, a.ranges, a.chunk, a.out)
        elif a.cmd == "watch":
            explore.watch(mb, a.ranges, a.interval, a.log, a.noise)
        elif a.cmd == "read":
            explore.read(mb, a.table, a.address, a.count)
    finally:
        mb.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
