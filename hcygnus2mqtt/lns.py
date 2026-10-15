"""Adaptateurs pour les serveurs réseau LoRaWAN (MQTT) : TTN v3 et ChirpStack v4."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass


@dataclass
class RxFrame:
    fport: int
    payload: bytes
    rssi: float | None = None
    snr: float | None = None
    fcnt: int | None = None


class TTN:
    """The Things Stack v3 — broker eu1.cloud.thethings.network:8883, user `<app>@ttn`, mot de passe = clé API."""

    def __init__(self, cfg: dict) -> None:
        self.app = cfg["application_id"]
        self.tenant = cfg.get("tenant", "ttn")
        self.dev = cfg["device_id"]

    @property
    def _root(self) -> str:
        return f"v3/{self.app}@{self.tenant}/devices/{self.dev}"

    @property
    def uplink_topic(self) -> str:
        return f"{self._root}/up"

    @property
    def downlink_topic(self) -> str:
        return f"{self._root}/down/push"

    def parse(self, raw: bytes) -> RxFrame | None:
        msg = json.loads(raw)
        up = msg.get("uplink_message") or {}
        if "frm_payload" not in up:
            return None
        meta = (up.get("rx_metadata") or [{}])
        best = max(meta, key=lambda m: m.get("rssi", -999)) if meta else {}
        return RxFrame(
            fport=int(up.get("f_port", 0)),
            payload=base64.b64decode(up["frm_payload"]),
            rssi=best.get("rssi"),
            snr=best.get("snr"),
            fcnt=up.get("f_cnt"),
        )

    def downlink(self, fport: int, payload: bytes, confirmed: bool = False) -> str:
        return json.dumps({"downlinks": [{
            "f_port": fport,
            "frm_payload": base64.b64encode(payload).decode(),
            "priority": "NORMAL",
            "confirmed": confirmed,
        }]})


class ChirpStack:
    """ChirpStack v4 — intégration MQTT, encodage JSON."""

    def __init__(self, cfg: dict) -> None:
        self.app = cfg["application_id"]
        self.dev_eui = cfg["dev_eui"].lower()

    @property
    def uplink_topic(self) -> str:
        return f"application/{self.app}/device/{self.dev_eui}/event/up"

    @property
    def downlink_topic(self) -> str:
        return f"application/{self.app}/device/{self.dev_eui}/command/down"

    def parse(self, raw: bytes) -> RxFrame | None:
        msg = json.loads(raw)
        if "data" not in msg or msg.get("data") is None:
            return None
        meta = msg.get("rxInfo") or [{}]
        best = max(meta, key=lambda m: m.get("rssi", -999)) if meta else {}
        return RxFrame(
            fport=int(msg.get("fPort", 0)),
            payload=base64.b64decode(msg["data"]),
            rssi=best.get("rssi"),
            snr=best.get("snr"),
            fcnt=msg.get("fCnt"),
        )

    def downlink(self, fport: int, payload: bytes, confirmed: bool = False) -> str:
        return json.dumps({
            "devEui": self.dev_eui,
            "confirmed": confirmed,
            "fPort": fport,
            "data": base64.b64encode(payload).decode(),
        })


def make_lns(cfg: dict):
    kind = cfg.get("type", "ttn").lower()
    if kind == "ttn":
        return TTN(cfg)
    if kind == "chirpstack":
        return ChirpStack(cfg)
    raise ValueError(f"type de serveur LoRaWAN inconnu : {kind} (ttn | chirpstack)")
