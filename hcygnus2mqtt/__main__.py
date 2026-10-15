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


def _cmd_dragino(a) -> int:
    from . import dragino
    from .lora import load_config, load_registers_from

    cfg, base = load_config(a.config)
    lw = cfg.get("lorawan", {})
    regs = load_registers_from(cfg, base)
    by_key = {r.key: r for r in regs}
    try:
        plan = dragino.build_plan(regs, unit=int(cfg.get("device", {}).get("unit", 1)),
                                  payver=int(lw.get("payver", 1)))
    except dragino.PlanError as exc:
        sys.exit(f"Plan impossible : {exc}")

    out = a.out if a.out.is_absolute() else Path.cwd() / a.out
    out.mkdir(parents=True, exist_ok=True)
    plan_path = Path(lw.get("plan", "dragino_plan.json"))
    plan_path = plan_path if plan_path.is_absolute() else base / plan_path
    plan.save(plan_path)

    at = dragino.at_commands(plan, cfg.get("serial", {}), int(lw.get("interval", 600)),
                             int(lw.get("reply_timeout_ms", 1000)))
    (out / "dragino_at_commands.txt").write_text("\n".join(at) + "\n", encoding="utf-8")
    (out / "payload_formatter.js").write_text(dragino.js_decoder(plan, by_key), encoding="utf-8")

    print(dragino.describe(plan, by_key))
    print("\nCommandes AT à envoyer au Dragino (console UART ou appli BLE) :\n")
    print("\n".join(at))
    print(f"\n→ plan enregistré : {plan_path}")
    print(f"→ script AT : {out / 'dragino_at_commands.txt'}")
    print(f"→ formateur TTN/ChirpStack : {out / 'payload_formatter.js'}")
    print("\nSi tu modifies la table de registres, relance cette commande ET reconfigure le Dragino.")
    return 0


def _cmd_lora(a) -> int:
    from . import dragino, explore
    from .lora import load_config, remote_read, remote_scan

    cfg, _ = load_config(a.config)
    interval = float(cfg.get("lorawan", {}).get("interval", 600))
    timeout = getattr(a, "timeout", None) or 2.5 * interval

    if a.cmd == "lora-interval":
        from .lora import LoraLink
        link = LoraLink(cfg)
        link.send(dragino.downlink_interval(a.seconds))
        link.close()
        print(f"Downlink programmé : intervalle {a.seconds}s (appliqué au prochain uplink). "
              "Pense à mettre à jour lorawan.interval dans config.yaml.")
        return 0

    if a.cmd == "lora-read":
        print(f"Downlink programmé, attente de la réponse (jusqu'à {timeout:.0f}s)…")
        values, exc = remote_read(cfg, a.table, a.address, a.count, timeout)
        if exc is not None:
            print(f"La PAC répond par l'exception {exc} (adresse ou fonction refusée)")
            return 1
        if values is None:
            print("Pas de réponse reconnue dans le délai.")
            return 1
        for i, v in enumerate(values):
            print(f"{a.table}[{a.address + i}] = {explore.interpretations(v)}")
        return 0

    ranges = [explore.parse_range(r) for r in a.ranges]
    total = sum(-(-n // a.chunk) for _, _, n in ranges)
    print(f"{total} lecture(s) à faire, ~1 par uplink : compte {total * interval / 60:.0f} min "
          f"avec un intervalle de {interval:.0f}s (réduis-le avec lora-interval).")
    snap = remote_scan(cfg, ranges, a.chunk, timeout,
                       on_result=lambda t, s, n, st: print(f"  {t} {s}-{s + n - 1} : {st}", flush=True),
                       split=not a.no_split)
    print(f"{len(snap)} adresses lues")
    if a.out:
        explore.save_snapshot(snap, a.out)
    return 0


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

    # ---------------------------------------------------------- LoRaWAN / Dragino
    p = sub.add_parser("dragino", help="calcule le plan d'interrogation et la config AT du Dragino")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("--out", type=Path, default=Path("dragino"), help="dossier de sortie (défaut ./dragino)")

    p = sub.add_parser("lora-read", help="lit des registres à distance (downlink 0xA8)")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("table", choices=["holding", "input", "coil", "discrete"])
    p.add_argument("address", type=lambda s: int(s, 0))
    p.add_argument("count", type=int, nargs="?", default=1)
    p.add_argument("--timeout", type=float, help="secondes d'attente (défaut : 2,5 × intervalle)")

    p = sub.add_parser("lora-scan", help="balaye des plages à distance (lent : 1 lecture par uplink)")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("ranges", nargs="+")
    p.add_argument("--chunk", type=int, default=16)
    p.add_argument("--timeout", type=float)
    p.add_argument("--no-split", action="store_true", help="ne pas recouper les blocs refusés")
    p.add_argument("-o", "--out", type=Path)

    p = sub.add_parser("lora-interval", help="change l'intervalle d'uplink du Dragino (downlink)")
    p.add_argument("-c", "--config", default="config.yaml")
    p.add_argument("seconds", type=int)

    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    if not a.verbose:
        logging.getLogger("pymodbus").setLevel(logging.CRITICAL)

    if a.cmd == "run":
        import yaml
        cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8")) or {}
        if cfg.get("transport", "lorawan") == "lorawan":
            from .lora import LoraBridge
            LoraBridge(a.config).run()
        else:
            from .bridge import Bridge
            Bridge(a.config).run()
        return 0
    if a.cmd == "dragino":
        return _cmd_dragino(a)
    if a.cmd.startswith("lora-"):
        return _cmd_lora(a)
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
