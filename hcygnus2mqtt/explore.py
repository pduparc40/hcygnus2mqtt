"""Outils pour deviner la table Modbus d'une machine non documentée.

  probe  : trouve vitesse / parité / adresse esclave qui répondent
  scan   : balaye des plages d'adresses et enregistre tout ce qui répond (CSV)
  watch  : relit en boucle des plages et affiche ce qui CHANGE (on agit sur la PAC en parallèle)
  diff   : compare deux scans (avant / après une action sur la machine)
  read   : lit quelques registres avec plusieurs interprétations
"""
from __future__ import annotations

import csv
import itertools
import sys
import time
from datetime import datetime
from pathlib import Path

from .modbus import Modbus, ModbusError

BIT_TABLES = ("coil", "discrete")


def _signed(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def interpretations(v: int) -> str:
    s = _signed(v)
    parts = [f"{v:5d}", f"0x{v:04X}", f"s16={s:6d}", f"/10={s / 10:7.1f}"]
    bits = [str(b) for b in range(16) if v >> b & 1]
    if 0 < len(bits) <= 6:
        parts.append("bits=" + ",".join(bits))
    hi, lo = v >> 8, v & 0xFF
    if 32 <= hi < 127 and 32 <= lo < 127:
        parts.append(f"ascii='{chr(hi)}{chr(lo)}'")
    return "  ".join(parts)


def parse_range(text: str) -> tuple[str, int, int]:
    """'holding:0-299' → ('holding', 0, 300)"""
    table, _, span = text.partition(":")
    if table not in ("holding", "input", "coil", "discrete"):
        raise SystemExit(f"Table inconnue dans '{text}' (holding/input/coil/discrete)")
    a, _, b = span.partition("-")
    start, end = int(a, 0), int(b or a, 0)
    return table, start, end - start + 1


# --------------------------------------------------------------------- probe
def probe(port: str, units: list[int], bauds: list[int], parities: list[str], timeout: float) -> int:
    targets = [("holding", 0), ("holding", 256), ("holding", 768), ("input", 0), ("input", 256)]
    found = 0
    for baud, parity in itertools.product(bauds, parities):
        print(f"--- {baud} bauds, parité {parity}")
        for unit in units:
            mb = Modbus({"port": port, "baudrate": baud, "parity": parity, "timeout": timeout,
                         "inter_request_delay": 0.02}, unit)
            if not mb.connect():
                raise SystemExit(f"Impossible d'ouvrir {port}")
            for table, addr in targets:
                try:
                    val = mb.read(table, addr, 1)[0]
                    print(f"  ✔ esclave {unit}: {table}[{addr}] = {val}")
                    found += 1
                    break
                except ModbusError as exc:
                    if exc.answered:
                        print(f"  ✔ esclave {unit} répond (exception {exc.exception_code} sur {table}[{addr}])"
                              " → la liaison fonctionne")
                        found += 1
                        break
            mb.close()
    if not found:
        print("\nAucune réponse. Vérifie : A/B inversés, masse commune, option RS485 présente et activée,"
              " adresse série (paramètre 'Adr'/'SEr' du régulateur), terminaison 120 Ω.")
    return 0 if found else 1


# --------------------------------------------------------------------- scan
def _read_chunk(mb: Modbus, table: str, start: int, count: int, results: dict, refused: list) -> None:
    """Lit un bloc ; s'il est refusé, le coupe en deux jusqu'à l'adresse unique."""
    try:
        values = mb.read(table, start, count)
        for i, v in enumerate(values):
            results[start + i] = v
    except ModbusError:
        if count == 1:
            refused.append(start)
            return
        half = count // 2
        _read_chunk(mb, table, start, half, results, refused)
        _read_chunk(mb, table, start + half, count - half, results, refused)


def scan(mb: Modbus, ranges: list[str], chunk: int, out: Path | None) -> dict:
    snapshot: dict[tuple[str, int], int] = {}
    for spec in ranges:
        table, start, count = parse_range(spec)
        results: dict[int, int] = {}
        refused: list[int] = []
        step = chunk if table not in BIT_TABLES else max(chunk, 64)
        for s in range(start, start + count, step):
            n = min(step, start + count - s)
            _read_chunk(mb, table, s, n, results, refused)
            print(f"\r{table} {s + n - 1}/{start + count - 1}  ({len(results)} lisibles)", end="", file=sys.stderr)
        print(file=sys.stderr)
        nonzero = sum(1 for v in results.values() if v)
        print(f"{table}: {len(results)} adresses lisibles, {nonzero} non nulles, {len(refused)} refusées")
        for addr in sorted(results):
            snapshot[(table, addr)] = results[addr]
    if out:
        save_snapshot(snapshot, out)
    return snapshot


def save_snapshot(snapshot: dict[tuple[str, int], int], out: Path) -> None:
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table", "address", "value", "hex", "signed", "div10"])
        for (table, addr), v in sorted(snapshot.items()):
            w.writerow([table, addr, v, f"0x{v:04X}", _signed(v), _signed(v) / 10])
    print(f"→ {len(snapshot)} valeurs écrites dans {out}")


def load_snapshot(path: Path) -> dict[tuple[str, int], int]:
    with path.open(encoding="utf-8") as f:
        return {(r["table"], int(r["address"])): int(r["value"]) for r in csv.DictReader(f)}


def diff(a: Path, b: Path) -> int:
    sa, sb = load_snapshot(a), load_snapshot(b)
    changed = [k for k in sorted(sa.keys() | sb.keys()) if sa.get(k) != sb.get(k)]
    if not changed:
        print("Aucune différence.")
        return 0
    print(f"{len(changed)} adresse(s) différente(s) :")
    for table, addr in changed:
        va, vb = sa.get((table, addr)), sb.get((table, addr))
        print(f"  {table:8s} {addr:5d}: {va!s:>6} → {vb!s:>6}"
              + (f"   (Δ={_signed(vb) - _signed(va):+d})" if va is not None and vb is not None else ""))
    return 0


# --------------------------------------------------------------------- watch
def watch(mb: Modbus, ranges: list[str], interval: float, log_path: Path | None, ignore_noise: int) -> None:
    specs = [parse_range(r) for r in ranges]
    previous: dict[tuple[str, int], int] = {}
    writer = None
    if log_path:
        fh = log_path.open("a", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        writer.writerow(["time", "table", "address", "old", "new"])
    print("Surveillance… agis sur la PAC (consigne, mode, marche/arrêt) et observe. Ctrl+C pour arrêter.")
    first = True
    try:
        while True:
            for table, start, count in specs:
                results: dict[int, int] = {}
                for s in range(start, start + count, 32):
                    _read_chunk(mb, table, s, min(32, start + count - s), results, [])
                for addr, v in results.items():
                    k = (table, addr)
                    old = previous.get(k)
                    if not first and old is not None and old != v:
                        if abs(_signed(v) - _signed(old)) <= ignore_noise and table not in BIT_TABLES:
                            previous[k] = v
                            continue  # ex. une sonde qui bouge de 0,1 °C
                        ts = datetime.now().strftime("%H:%M:%S")
                        print(f"{ts}  {table:8s} {addr:5d}: {old:6d} → {v:6d}   [{interpretations(v)}]")
                        if writer:
                            writer.writerow([ts, table, addr, old, v])
                            fh.flush()
                    previous[k] = v
            if first:
                print(f"État initial capturé ({len(previous)} adresses).")
                first = False
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


def read(mb: Modbus, table: str, address: int, count: int) -> None:
    for i, v in enumerate(mb.read(table, address, count)):
        print(f"{table}[{address + i}] = {interpretations(v)}")
