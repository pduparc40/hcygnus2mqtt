"""Dragino RS485-LB / RS485W-LB : plan d'interrogation, configuration, décodage.

Le Dragino interroge lui-même la PAC à chaque cycle (AT+COMMAND1…F), découpe
les réponses (AT+DATACUT) et envoie les octets utiles bout à bout dans un uplink :

    [batterie mV (2 octets, bit 15 = interruption)] [PAYVER] [données cmd1][données cmd2]…

En mode multi-trames (AT+DATAUP=1), chaque segment porte en plus
[nombre de segments][n° de segment] après PAYVER.

Ce module calcule le plan (quels blocs lire, où tombent leurs octets dans
l'uplink), génère les commandes AT correspondantes, et décode les uplinks.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .frames import data_bytes, hexs, read_frame, response_length, unpack, write_frame
from .registers import Register, build_blocks

MAX_COMMANDS = 15        # AT+COMMAND1 … AT+COMMANDF
MAX_REPLY = 100          # AT+DATACUT : longueur de réponse max
EU868_DATA_BYTES = 46    # 51 octets par uplink − en-tête
MAX_SEGMENTS = 15        # AT+DATAUP=1


class PlanError(Exception):
    pass


@dataclass
class Command:
    slot: int               # 1 … 15
    table: str
    start: int
    count: int
    offset: int             # position des données de ce bloc dans le flux décodé
    keys: list[str]

    @property
    def data_len(self) -> int:
        return data_bytes(self.table, self.count)

    @property
    def reply_len(self) -> int:
        return response_length(self.table, self.count)

    def frame(self, unit: int) -> bytes:
        return read_frame(unit, self.table, self.start, self.count)


@dataclass
class Plan:
    unit: int
    payver: int
    mode: str                      # "single" (AT+DATAUP=0) ou "multi" (AT+DATAUP=1)
    commands: list[Command] = field(default_factory=list)
    segment_bytes: int = EU868_DATA_BYTES

    @property
    def total_bytes(self) -> int:
        return sum(c.data_len for c in self.commands)

    @property
    def segments(self) -> int:
        return max(1, -(-self.total_bytes // self.segment_bytes))

    # ---------------------------------------------------------------- persistance
    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json() + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Plan:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        d["commands"] = [Command(**c) for c in d["commands"]]
        return cls(**d)


# ------------------------------------------------------------------ construction
def build_plan(registers: list[Register], unit: int = 1, payver: int = 1,
               segment_bytes: int = EU868_DATA_BYTES, allow_multi: bool = True) -> Plan:
    """Regroupe les registres en ≤ 15 lectures et vérifie que ça tient dans les uplinks."""
    if not registers:
        raise PlanError("aucun registre à interroger")
    max_regs = (MAX_REPLY - 5) // 2          # 47 registres par lecture

    blocks = None
    for gap in (2, 4, 8, 16, 32):
        candidate = build_blocks(registers, max_gap=gap, max_len=max_regs)
        if len(candidate) <= MAX_COMMANDS:
            # on préfère le plus petit écart qui tient en 15 commandes : moins d'octets inutiles
            blocks = candidate
            break
    if blocks is None:
        raise PlanError(
            f"impossible de tenir en {MAX_COMMANDS} commandes : regroupe les registres "
            "ou réduis la liste (options.only)"
        )

    plan = Plan(unit=unit, payver=payver, mode="single", segment_bytes=segment_bytes)
    offset = 0
    for slot, b in enumerate(blocks, start=1):
        cmd = Command(slot, b.table, b.start, b.count, offset, [r.key for r in b.registers])
        plan.commands.append(cmd)
        offset += cmd.data_len

    if plan.total_bytes > segment_bytes:
        if not allow_multi:
            raise PlanError(f"{plan.total_bytes} octets > {segment_bytes} par uplink")
        if plan.segments > MAX_SEGMENTS:
            raise PlanError(f"{plan.total_bytes} octets : trop pour {MAX_SEGMENTS} segments")
        plan.mode = "multi"
    return plan


def describe(plan: Plan, registers: dict[str, Register]) -> str:
    lines = [
        f"Esclave Modbus {plan.unit}, PAYVER={plan.payver}, {len(plan.commands)} commande(s), "
        f"{plan.total_bytes} octets de données → "
        + ("1 uplink" if plan.mode == "single" else f"{plan.segments} segments (AT+DATAUP=1)"),
        "",
        f"{'cmd':>3}  {'table':8} {'adresses':>11}  {'octets':>7}  registres",
    ]
    for c in plan.commands:
        span = f"{c.start}-{c.start + c.count - 1}"
        lines.append(f"{c.slot:>3}  {c.table:8} {span:>11}  {c.offset:>3}+{c.data_len:<3}  {', '.join(c.keys)}")
    unverified = [k for c in plan.commands for k in c.keys if k in registers and not registers[k].verified]
    if unverified:
        lines += ["", f"⚠️  {len(unverified)} registre(s) non vérifié(s) dans le plan : {', '.join(unverified)}"]
    return "\n".join(lines)


# ------------------------------------------------------------------ configuration
def at_commands(plan: Plan, serial: dict, interval_s: int, reply_timeout_ms: int = 1000) -> list[str]:
    """Script AT à coller dans la console (UART) ou l'appli BLE du Dragino."""
    parity = {"N": 0, "O": 1, "E": 2}[str(serial.get("parity", "N")).upper()]
    out = [
        "AT+MOD=1",                                   # capteur RS485
        f"AT+BAUDR={int(serial.get('baudrate', 9600))}",
        f"AT+PARITY={parity}",
        f"AT+STOPBIT={int(serial.get('stopbits', 1))}",
        f"AT+DATABIT={int(serial.get('bytesize', 8))}",
        "AT+MBFUN=0",
        f"AT+CMDEAR=1,{MAX_COMMANDS}",                # efface les anciennes commandes
        f"AT+PAYVER={plan.payver}",
        "AT+DATAUP=0" if plan.mode == "single" else "AT+DATAUP=1",
    ]
    for c in plan.commands:
        x = f"{c.slot:X}"
        last = 3 + c.data_len
        out += [
            f"AT+COMMAND{x}={hexs(c.frame(plan.unit))},1",
            f"AT+DATACUT{x}={c.reply_len},2,4~{last}",
            f"AT+CMDDL{x}={reply_timeout_ms}",
        ]
    out.append(f"AT+TDC={interval_s * 1000}")
    return out


def downlink_interval(seconds: int) -> bytes:
    """Downlink Dragino générique de changement d'intervalle (0x01 + secondes sur 3 octets)."""
    return bytes((0x01, seconds >> 16 & 0xFF, seconds >> 8 & 0xFF, seconds & 0xFF))


def downlink_raw(frame_without_crc: bytes, reply_bytes: int) -> bytes:
    """Downlink 0xA8 : le Dragino envoie la trame (CRC ajouté) et remonte `reply_bytes` octets."""
    if len(frame_without_crc) > 40:
        raise ValueError("trame trop longue pour 0xA8 (40 octets max)")
    return bytes((0xA8, 0x01, len(frame_without_crc))) + frame_without_crc + bytes((reply_bytes,))


def downlink_write(unit: int, reg: Register, value: Any) -> bytes:
    words = reg.encode(value)
    frame = write_frame(unit, reg.table, reg.address, words)
    return downlink_raw(frame, 8)     # écho FC05/FC16 = 8 octets


def downlink_read(unit: int, table: str, start: int, count: int) -> bytes:
    return downlink_raw(read_frame(unit, table, start, count), response_length(table, count))


# ------------------------------------------------------------------ décodage
@dataclass
class Uplink:
    battery_mv: int
    interrupt: bool
    payver: int
    values: dict[str, Any]
    missing: list[str]


class Decoder:
    """Décode les uplinks d'interrogation périodique selon le plan."""

    def __init__(self, plan: Plan, registers: dict[str, Register], segment_timeout: float = 300) -> None:
        self.plan = plan
        self.registers = registers
        self.segment_timeout = segment_timeout
        self._segments: dict[int, bytes] = {}
        self._segments_t = 0.0

    def feed(self, payload: bytes, now: float | None = None) -> Uplink | None:
        """Renvoie un Uplink complet, ou None (segment intermédiaire / trame étrangère)."""
        if len(payload) < 3:
            return None
        raw_bat = payload[0] << 8 | payload[1]
        battery, interrupt, payver = raw_bat & 0x7FFF, bool(raw_bat & 0x8000), payload[2]
        if payver != self.plan.payver:
            return None

        if self.plan.mode == "single":
            data = payload[3:]
            if len(data) != self.plan.total_bytes:
                return None      # pas un uplink d'interrogation de ce plan (ex. réponse 0xA8)
        else:
            if len(payload) < 5:
                return None
            total, index = payload[3], payload[4]
            now = time.monotonic() if now is None else now
            if not self._segments or now - self._segments_t > self.segment_timeout:
                self._segments = {}
            self._segments_t = now
            self._segments[index] = payload[5:]
            if len(self._segments) < total:
                return None
            data = b"".join(self._segments.get(i, b"") for i in range(total))
            self._segments = {}

        values: dict[str, Any] = {}
        missing: list[str] = []
        for c in self.plan.commands:
            chunk = data[c.offset:c.offset + c.data_len]
            # réponse absente : chunk tronqué, ou rempli de FF / 00 par le Dragino
            if len(chunk) < c.data_len or (c.data_len >= 2 and chunk in (b"\xff" * c.data_len,)):
                missing += c.keys
                continue
            words = unpack(c.table, chunk, c.count)
            for key in c.keys:
                reg = self.registers.get(key)
                if reg is None:
                    continue
                i = reg.address - c.start
                values[key] = reg.decode(words[i:i + reg.width])
        return Uplink(battery, interrupt, payver, values, missing)


# ------------------------------------------------------------------ décodeur JS (TTN / ChirpStack)
def js_decoder(plan: Plan, registers: dict[str, Register]) -> str:
    """Formateur de payload pour la console TTN / ChirpStack (affichage uniquement :
    la passerelle Python décode elle-même les octets bruts)."""
    lines = []
    for c in plan.commands:
        for key in c.keys:
            r = registers.get(key)
            if r is None:
                continue
            i = r.address - c.start
            if c.table in ("coil", "discrete"):
                expr = f"(d[{c.offset + i // 8}] >> {i % 8}) & 1"
            else:
                o = c.offset + 2 * i
                if r.type in ("int32", "uint32"):
                    hi, lo = (o, o + 2) if r.word_order == "big" else (o + 2, o)
                    expr = f"((u16(d,{hi}) * 65536) + u16(d,{lo}))"
                    if r.type == "int32":
                        expr = f"s32({expr})"
                elif r.type == "int16":
                    expr = f"s16(u16(d,{o}))"
                else:
                    expr = f"u16(d,{o})"
                if r.bit is not None:
                    expr = f"(u16(d,{o}) >> {r.bit}) & 1"
                elif not r.options and (r.scale != 1 or r.offset != 0):
                    expr = f"round({expr} * {r.scale} + {r.offset})"
            lines.append(f"  out.{key} = {expr};  // {c.table} {r.address}")
    header = "d = bytes.slice(5);" if plan.mode == "multi" else "d = bytes.slice(3);"
    multi_note = ("  // mode multi-segments : chaque uplink ne porte qu'une partie, ce décodeur\n"
                  "  // n'est fiable que pour le 1er segment. La passerelle réassemble, elle.\n"
                  if plan.mode == "multi" else "")
    return f"""// Généré par hcygnus2mqtt — PAYVER {plan.payver}, {plan.total_bytes} octets de données.
function u16(d, i) {{ return (d[i] << 8) | d[i + 1]; }}
function s16(v) {{ return v & 0x8000 ? v - 0x10000 : v; }}
function s32(v) {{ return v >= 0x80000000 ? v - 0x100000000 : v; }}
function round(v) {{ return Math.round(v * 1000) / 1000; }}

function decodeUplink(input) {{
  var bytes = input.bytes, out = {{}}, d;
  if (bytes.length < 3) return {{ data: {{}}, errors: ["uplink trop court"] }};
  out.battery_v = ((bytes[0] << 8 | bytes[1]) & 0x7FFF) / 1000;
  out.payver = bytes[2];
  if (out.payver !== {plan.payver}) return {{ data: out, warnings: ["PAYVER inattendu"] }};
{multi_note}  {header}
{chr(10).join(lines)}
  return {{ data: out }};
}}
"""
