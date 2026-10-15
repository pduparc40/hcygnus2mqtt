"""Définition des registres et décodage des valeurs Modbus brutes."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TABLES = ("holding", "input", "coil", "discrete")
TYPES = ("int16", "uint16", "int32", "uint32", "bool")
COMPONENTS = ("sensor", "binary_sensor", "number", "select", "switch")


@dataclass
class Register:
    key: str
    name: str
    table: str = "holding"
    address: int = 0
    type: str = "int16"
    scale: float = 1.0
    offset: float = 0.0
    bit: int | None = None              # extraire un seul bit d'un registre 16 bits
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    component: str = "sensor"
    options: dict[int, str] = field(default_factory=dict)   # pour les "select" / énumérations
    writable: bool = False
    min: float | None = None
    max: float | None = None
    step: float | None = None
    icon: str | None = None
    verified: bool = False              # False = registre deviné, non confirmé sur la machine
    note: str | None = None
    word_order: str = "big"             # pour int32/uint32 : "big" (MSW d'abord) ou "little"

    def __post_init__(self) -> None:
        if self.table not in TABLES:
            raise ValueError(f"{self.key}: table inconnue '{self.table}'")
        if self.type not in TYPES:
            raise ValueError(f"{self.key}: type inconnu '{self.type}'")
        if self.component not in COMPONENTS:
            raise ValueError(f"{self.key}: composant inconnu '{self.component}'")
        if self.table in ("coil", "discrete"):
            self.type = "bool"
        if self.bit is not None and not 0 <= self.bit <= 15:
            raise ValueError(f"{self.key}: bit doit être entre 0 et 15")
        if self.writable and self.table in ("input", "discrete"):
            raise ValueError(f"{self.key}: les tables input/discrete sont en lecture seule")
        self.options = {int(k): str(v) for k, v in (self.options or {}).items()}

    @property
    def width(self) -> int:
        """Nombre de registres (ou bits) occupés."""
        return 2 if self.type in ("int32", "uint32") else 1

    @property
    def is_bit_table(self) -> bool:
        return self.table in ("coil", "discrete")

    # ---------------------------------------------------------------- décodage
    def decode(self, words: list[int]) -> Any:
        """Convertit les mots/bits bruts en valeur utilisable."""
        if self.is_bit_table:
            return bool(words[0])
        if self.bit is not None:
            return bool((words[0] >> self.bit) & 1)

        if self.type in ("int32", "uint32"):
            hi, lo = (words[0], words[1]) if self.word_order == "big" else (words[1], words[0])
            raw = (hi << 16) | lo
            if self.type == "int32" and raw & 0x80000000:
                raw -= 1 << 32
        else:
            raw = words[0] & 0xFFFF
            if self.type == "int16" and raw & 0x8000:
                raw -= 1 << 16
            if self.type == "bool":
                return bool(raw)

        if self.options:
            return self.options.get(raw, f"inconnu ({raw})")

        value = raw * self.scale + self.offset
        if self.scale == 1 and self.offset == 0:
            return int(value)
        decimals = max(0, len(repr(self.scale).split(".")[-1])) if "." in repr(self.scale) else 0
        return round(value, decimals)

    # ---------------------------------------------------------------- encodage
    def encode(self, value: Any) -> list[int]:
        """Convertit une valeur venant de MQTT en mots à écrire."""
        if self.is_bit_table or self.type == "bool":
            return [1 if _truthy(value) else 0]

        if self.options:
            reverse = {v.lower(): k for k, v in self.options.items()}
            text = str(value).strip().lower()
            if text not in reverse:
                raise ValueError(f"{self.key}: option inconnue '{value}'")
            raw = reverse[text]
        else:
            num = float(value)
            if self.min is not None and num < self.min:
                raise ValueError(f"{self.key}: {num} < min {self.min}")
            if self.max is not None and num > self.max:
                raise ValueError(f"{self.key}: {num} > max {self.max}")
            raw = round((num - self.offset) / self.scale)

        if self.type in ("int32", "uint32"):
            raw &= 0xFFFFFFFF
            hi, lo = raw >> 16, raw & 0xFFFF
            return [hi, lo] if self.word_order == "big" else [lo, hi]
        return [raw & 0xFFFF]


def _truthy(value: Any) -> bool:
    return str(value).strip().upper() in ("1", "ON", "TRUE", "YES", "OUI")


def load_registers(path: str | Path) -> list[Register]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    items = data.get("registers", data) if isinstance(data, dict) else data
    regs = [Register(**item) for item in items]
    keys = [r.key for r in regs]
    dupes = {k for k in keys if keys.count(k) > 1}
    if dupes:
        raise ValueError(f"Clés dupliquées dans {path}: {sorted(dupes)}")
    return regs


@dataclass
class Block:
    """Plage contiguë lue en une seule requête Modbus."""
    table: str
    start: int
    count: int
    registers: list[Register]


def build_blocks(registers: list[Register], max_gap: int = 8, max_len: int = 64) -> list[Block]:
    """Regroupe les registres proches pour minimiser le nombre de requêtes."""
    blocks: list[Block] = []
    for table in TABLES:
        regs = sorted((r for r in registers if r.table == table), key=lambda r: r.address)
        current: Block | None = None
        for r in regs:
            end = r.address + r.width
            if (
                current is not None
                and r.address - (current.start + current.count) <= max_gap
                and end - current.start <= max_len
            ):
                current.count = max(current.count, end - current.start)
                current.registers.append(r)
            else:
                current = Block(table, r.address, r.width, [r])
                blocks.append(current)
    return blocks
