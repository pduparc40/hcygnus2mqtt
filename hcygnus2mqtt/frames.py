"""Construction et analyse de trames Modbus RTU brutes.

Utilisé pour programmer le Dragino (AT+COMMANDx, downlinks 0xA8) et pour
reconnaître les réponses Modbus renvoyées dans les uplinks LoRaWAN.
"""
from __future__ import annotations

from dataclasses import dataclass

READ_FC = {"coil": 0x01, "discrete": 0x02, "holding": 0x03, "input": 0x04}
TABLE_BY_FC = {v: k for k, v in READ_FC.items()}


def crc16(data: bytes) -> bytes:
    """CRC-16/MODBUS, octet de poids faible en premier (ordre de la trame)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes((crc & 0xFF, crc >> 8))


def with_crc(frame: bytes) -> bytes:
    return frame + crc16(frame)


def read_frame(unit: int, table: str, start: int, count: int) -> bytes:
    """Requête de lecture SANS CRC (le Dragino l'ajoute lui-même avec ',1')."""
    return bytes((unit, READ_FC[table], start >> 8, start & 0xFF, count >> 8, count & 0xFF))


def write_frame(unit: int, table: str, address: int, words: list[int]) -> bytes:
    """Requête d'écriture SANS CRC : FC05 pour une bobine, FC16 pour des registres."""
    if table == "coil":
        return bytes((unit, 0x05, address >> 8, address & 0xFF, 0xFF if words[0] else 0x00, 0x00))
    if table != "holding":
        raise ValueError(f"table {table} en lecture seule")
    n = len(words)
    body = b"".join(bytes((w >> 8 & 0xFF, w & 0xFF)) for w in words)
    return bytes((unit, 0x10, address >> 8, address & 0xFF, 0, n, 2 * n)) + body


def data_bytes(table: str, count: int) -> int:
    """Nombre d'octets utiles dans la réponse à une lecture."""
    return (count + 7) // 8 if table in ("coil", "discrete") else 2 * count


def response_length(table: str, count: int) -> int:
    """Longueur totale de la réponse : esclave + FC + nb octets + données + CRC."""
    return 3 + data_bytes(table, count) + 2


def unpack(table: str, data: bytes, count: int) -> list[int]:
    """Données brutes → liste de mots 16 bits (ou de bits 0/1)."""
    if table in ("coil", "discrete"):
        return [(data[i // 8] >> (i % 8)) & 1 for i in range(count)]
    return [(data[2 * i] << 8) | data[2 * i + 1] for i in range(count)]


@dataclass
class Response:
    unit: int
    function: int
    payload: bytes          # données utiles (lecture) ou écho (écriture)
    exception: int | None = None

    @property
    def table(self) -> str | None:
        return TABLE_BY_FC.get(self.function & 0x7F)


def find_response(blob: bytes, unit: int) -> Response | None:
    """Cherche une réponse Modbus valide (CRC correct) n'importe où dans `blob`.

    Le format exact des uplinks déclenchés par un downlink 0xA8 n'est pas
    documenté précisément : on balaye donc l'uplink à la recherche d'une trame
    dont le CRC est bon.
    """
    for i in range(len(blob)):
        if blob[i] != unit or i + 1 >= len(blob):
            continue
        fc = blob[i + 1]
        if fc & 0x80 and i + 5 <= len(blob):                     # exception
            frame = blob[i:i + 5]
            if crc16(frame[:3]) == frame[3:]:
                return Response(unit, fc, b"", frame[2])
        if fc in (1, 2, 3, 4) and i + 2 < len(blob):               # réponse de lecture
            n = blob[i + 2]
            frame = blob[i:i + 3 + n + 2]
            if len(frame) == 5 + n and crc16(frame[:-2]) == frame[-2:]:
                return Response(unit, fc, frame[3:3 + n])
        if fc in (5, 6, 0x10) and i + 8 <= len(blob):              # écho d'écriture
            frame = blob[i:i + 8]
            if crc16(frame[:6]) == frame[6:]:
                return Response(unit, fc, frame[2:6])
    return None


def hexs(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)
