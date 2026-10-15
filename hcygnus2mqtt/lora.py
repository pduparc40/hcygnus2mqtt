"""Pont LoRaWAN : uplinks du Dragino (via TTN / ChirpStack) → MQTT Home Assistant."""
from __future__ import annotations

import json
import logging
import queue
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import yaml

from .discovery import config_payload, topics
from .dragino import Decoder, Plan, build_plan, downlink_read, downlink_write
from .frames import READ_FC, data_bytes, find_response, hexs, unpack
from .lns import RxFrame, make_lns
from .registers import Register, load_registers

log = logging.getLogger("hcygnus2mqtt.lora")


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    return str(value)


def _mqtt_client(cfg: dict, client_id: str) -> mqtt.Client:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if cfg.get("username"):
        c.username_pw_set(cfg["username"], cfg.get("password"))
    if cfg.get("tls") or int(cfg.get("port", 1883)) == 8883:
        c.tls_set()
    c.reconnect_delay_set(1, 120)
    return c


def load_config(path: str) -> tuple[dict, Path]:
    p = Path(path).resolve()
    return yaml.safe_load(p.read_text(encoding="utf-8")), p.parent


def resolve(base: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else base / p


def load_registers_from(cfg: dict, base: Path) -> list[Register]:
    regs = load_registers(resolve(base, cfg.get("registers", "registers/hcygnus_tech.yaml")))
    opts = cfg.get("options", {})
    only = set(opts.get("only", []) or [])
    exclude = set(opts.get("exclude", []) or [])
    include_unverified = bool(opts.get("include_unverified", True))
    return [
        r for r in regs
        if (include_unverified or r.verified) and (not only or r.key in only) and r.key not in exclude
    ]


def plan_for(cfg: dict, base: Path, regs: list[Register]) -> tuple[Plan, Path]:
    lw = cfg.get("lorawan", {})
    path = resolve(base, lw.get("plan", "dragino_plan.json"))
    if path.exists():
        return Plan.load(path), path
    plan = build_plan(regs, unit=int(cfg.get("device", {}).get("unit", 1)), payver=int(lw.get("payver", 1)))
    return plan, path


# =====================================================================  lien LNS
class LoraLink:
    """Accès « requête / réponse » au Dragino via downlinks 0xA8 (outils en ligne de commande)."""

    def __init__(self, cfg: dict) -> None:
        lw = cfg["lorawan"]
        self.lns = make_lns(lw)
        self.fport = int(lw.get("downlink_port", 2))
        self.unit = int(cfg.get("device", {}).get("unit", 1))
        self.frames: queue.Queue[RxFrame] = queue.Queue()
        self.seen_fcnt: set[int] = set()
        self.subscribed = threading.Event()
        self.client = _mqtt_client(lw.get("mqtt", {}), "hcygnus2mqtt-cli")
        self.client.on_connect = lambda c, *_: c.subscribe(self.lns.uplink_topic)
        self.client.on_subscribe = lambda *_: self.subscribed.set()
        self.client.on_message = self._on_message
        m = lw.get("mqtt", {})
        self.client.connect(m.get("host", "localhost"), int(m.get("port", 1883)))
        self.client.loop_start()
        # sans ça, la réponse au 1er downlink peut arriver avant l'abonnement
        if not self.subscribed.wait(15):
            raise ConnectionError("pas d'abonnement aux uplinks du serveur LoRaWAN")

    def _on_message(self, client, userdata, msg) -> None:
        try:
            frame = self.lns.parse(msg.payload)
        except (ValueError, KeyError) as exc:
            log.debug("uplink illisible : %s", exc)
            return
        if frame:
            if frame.fcnt is not None:       # doublon (plusieurs passerelles, rejeu)
                if frame.fcnt in self.seen_fcnt:
                    return
                self.seen_fcnt.add(frame.fcnt)
            self.frames.put(frame)

    def send(self, payload: bytes) -> None:
        self.client.publish(self.lns.downlink_topic, self.lns.downlink(self.fport, payload))

    def request(self, downlink: bytes, timeout: float, expect_fc: int | None = None,
                expect_len: int | None = None):
        """Programme un downlink puis attend une réponse Modbus valide dans les uplinks.

        `expect_fc` / `expect_len` écartent les réponses qui ne correspondent pas à la
        requête (même fonction, même nombre d'octets de données) ; une exception
        Modbus sur la même fonction est acceptée."""
        while not self.frames.empty():
            self.frames.get_nowait()
        self.send(downlink)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                frame = self.frames.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            resp = find_response(frame.payload, self.unit)
            log.debug("uplink port %s : %s", frame.fport, hexs(frame.payload))
            if resp is None:
                continue
            if expect_fc is not None and resp.function & 0x7F != expect_fc:
                continue
            if expect_len is not None and resp.exception is None and len(resp.payload) != expect_len:
                continue
            return resp
        return None

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def remote_read(cfg: dict, table: str, start: int, count: int, timeout: float):
    """Lit une plage via LoRaWAN. Renvoie (valeurs | None, exception | None)."""
    link = LoraLink(cfg)
    try:
        resp = link.request(downlink_read(link.unit, table, start, count), timeout,
                            READ_FC[table], data_bytes(table, count))
    finally:
        link.close()
    if resp is None:
        return None, None
    if resp.exception is not None:
        return None, resp.exception
    return unpack(table, resp.payload, count), None


def remote_scan(cfg: dict, ranges: list[tuple[str, int, int]], chunk: int, timeout: float,
                on_result=None, split: bool = True) -> dict[tuple[str, int], int]:
    """Balaye des plages. Un bloc refusé (exception) est recoupé en deux si `split`,
    comme le scan local — au prix d'un uplink par requête supplémentaire."""
    link = LoraLink(cfg)
    snapshot: dict[tuple[str, int], int] = {}
    todo: list[tuple[str, int, int]] = []
    for table, start, count in ranges:
        todo += [(table, s, min(chunk, start + count - s)) for s in range(start, start + count, chunk)]
    try:
        while todo:
            table, s, n = todo.pop(0)
            resp = link.request(downlink_read(link.unit, table, s, n), timeout,
                                READ_FC[table], data_bytes(table, n))
            if resp is None:
                status = "pas de réponse"
            elif resp.exception is not None:
                status = f"exception {resp.exception}"
                if split and n > 1:
                    half = n // 2
                    todo[:0] = [(table, s, half), (table, s + half, n - half)]
                    status += " → recoupé"
            else:
                for i, v in enumerate(unpack(table, resp.payload, n)):
                    snapshot[(table, s + i)] = v
                status = "ok"
            if on_result:
                on_result(table, s, n, status)
    finally:
        link.close()
    return snapshot


# =====================================================================  passerelle
class LoraBridge:
    META = [
        Register(key="battery", name="Batterie Dragino", unit="V", device_class="voltage",
                 state_class="measurement", verified=True),
        Register(key="rssi", name="RSSI LoRa", unit="dBm", device_class="signal_strength",
                 state_class="measurement", verified=True),
        Register(key="snr", name="SNR LoRa", unit="dB", state_class="measurement", verified=True),
        Register(key="last_uplink", name="Dernier uplink", device_class="timestamp", verified=True),
    ]

    def __init__(self, config_path: str) -> None:
        self.cfg, base = load_config(config_path)
        self.dev = self.cfg.setdefault("device", {})
        self.dev.setdefault("id", "hcygnus171")
        self.unit = int(self.dev.get("unit", 1))
        self.base = self.cfg.get("mqtt", {}).get("base_topic", "hcygnus2mqtt")
        self.availability = f"{self.base}/{self.dev['id']}/availability"

        lw = self.cfg["lorawan"]
        self.lns = make_lns(lw)
        self.uplink_port = int(lw.get("uplink_port", 2))
        self.downlink_port = int(lw.get("downlink_port", 2))
        self.interval = float(lw.get("interval", 600))

        opts = self.cfg.get("options", {})
        self.allow_writes = bool(opts.get("allow_writes", False))
        self.allow_unverified_writes = bool(opts.get("allow_unverified_writes", False))

        regs = load_registers_from(self.cfg, base)
        self.plan, plan_path = plan_for(self.cfg, base, regs)
        if not plan_path.exists():
            log.warning("Pas de %s : plan calculé à la volée. Lance `hcygnus2mqtt dragino` et "
                        "configure le Dragino avec, sinon le décodage sera faux.", plan_path.name)
        by_key = {r.key: r for r in regs}
        planned = {k for c in self.plan.commands for k in c.keys}
        unknown = planned - by_key.keys()
        if unknown:
            log.warning("Clés du plan absentes de la table de registres (ignorées) : %s", sorted(unknown))
        self.registers = [r for r in regs if r.key in planned]
        self.by_key = {r.key: r for r in self.registers}
        self.decoder = Decoder(self.plan, self.by_key)

        self.pending: dict[str, tuple[Register, Any, float]] = {}
        self.last_uplink = 0.0
        self.started = time.time()
        self.online: bool | None = None
        self.stop_event = threading.Event()

        self.ha = _mqtt_client(self.cfg.get("mqtt", {}), f"hcygnus2mqtt-{self.dev['id']}")
        self.ha.will_set(self.availability, "offline", retain=True)
        self.ha.on_connect = self._ha_connect
        self.ha.on_message = self._ha_message

        self.net = _mqtt_client(lw.get("mqtt", {}), f"hcygnus2mqtt-lns-{self.dev['id']}")
        self.net.on_connect = self._lns_connect
        self.net.on_message = self._lns_message

    # ------------------------------------------------------------------ HA
    def _ha_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            log.error("Connexion au broker Home Assistant refusée : %s", reason_code)
            return
        client.subscribe(f"{self.base}/{self.dev['id']}/+/set")
        client.subscribe("homeassistant/status")
        self.publish_discovery()
        self._set_online(self._is_fresh(), force=True)

    def _ha_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="replace").strip()
        if msg.topic == "homeassistant/status":
            if payload == "online":
                self.publish_discovery()
                self._set_online(self._is_fresh(), force=True)
            return
        self.handle_command(msg.topic.split("/")[-2], payload)

    def publish_discovery(self) -> None:
        for reg in self.registers + self.META:
            topic, payload = config_payload(reg, self.base, self.dev, self.availability)
            if any(reg is m for m in self.META):
                payload["entity_category"] = "diagnostic"
            if reg.writable and reg.component in ("number", "select", "switch"):
                # la commande n'arrive qu'au prochain créneau LoRaWAN : pas d'état optimiste
                payload["optimistic"] = False
            self.ha.publish(topic, json.dumps(payload, ensure_ascii=False), retain=True)
        log.info("Découverte HA publiée (%d entités)", len(self.registers) + len(self.META))

    def _state(self, reg: Register, value: Any) -> None:
        self.ha.publish(topics(self.base, self.dev["id"], reg)["state"], _fmt(value), retain=True)

    def _is_fresh(self) -> bool:
        # au démarrage, on laisse 2,5 intervalles avant de déclarer la PAC muette :
        # les dernières valeurs (retenues sur le broker) restent affichées dans HA
        ref = self.last_uplink or self.started
        return time.time() - ref < 2.5 * self.interval

    def _set_online(self, online: bool, force: bool = False) -> None:
        if online != self.online or force:
            self.ha.publish(self.availability, "online" if online else "offline", retain=True)
            if online != self.online:
                log.info("PAC %s", "joignable" if online else "sans nouvelles (pas d'uplink récent)")
            self.online = online

    # ------------------------------------------------------------------ commandes
    def handle_command(self, key: str, payload: str) -> None:
        reg = self.by_key.get(key)
        if reg is None:
            log.warning("Commande pour une clé inconnue : %s", key)
            return
        if not (self.allow_writes and reg.writable):
            log.warning("Écriture refusée pour %s (allow_writes=false ou non inscriptible)", key)
            return
        if not reg.verified and not self.allow_unverified_writes:
            log.warning("Écriture refusée pour %s : registre non vérifié", key)
            return
        try:
            dl = downlink_write(self.unit, reg, payload)
        except ValueError as exc:
            log.error("Valeur refusée pour %s : %s", key, exc)
            return
        self.net.publish(self.lns.downlink_topic, self.lns.downlink(self.downlink_port, dl))
        self.pending[key] = (reg, payload, time.time())
        log.info("Écriture %s=%s programmée (downlink %s) — appliquée au prochain uplink",
                 key, payload, hexs(dl))

    # ------------------------------------------------------------------ uplinks
    def _lns_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            log.error("Connexion au serveur LoRaWAN refusée : %s", reason_code)
            return
        client.subscribe(self.lns.uplink_topic)
        log.info("Abonné aux uplinks : %s", self.lns.uplink_topic)

    def _lns_message(self, client, userdata, msg):
        try:
            frame = self.lns.parse(msg.payload)
        except (ValueError, KeyError) as exc:
            log.warning("Message LoRaWAN illisible : %s", exc)
            return
        if frame is not None:
            self.handle_uplink(frame)

    def handle_uplink(self, frame: RxFrame) -> None:
        log.debug("Uplink port %d : %s", frame.fport, hexs(frame.payload))
        # 1) réponse à un downlink 0xA8 (écriture) ? On la reconnaît à son CRC Modbus.
        if self.pending:
            resp = find_response(frame.payload, self.unit)
            if resp is not None and resp.function & 0x7F in (0x05, 0x10):
                self._ack_write(resp)
                self._touch(frame)
                return
        if frame.fport != self.uplink_port:
            return
        # 2) interrogation périodique
        up = self.decoder.feed(frame.payload)
        if up is None:
            if self.plan.mode == "single":
                log.info("Uplink ignoré (PAYVER ou longueur inattendue, %d octets) : %s — "
                         "le Dragino est-il configuré avec ce plan ?", len(frame.payload), hexs(frame.payload))
            return
        self._touch(frame, battery_mv=up.battery_mv)
        for key, value in up.values.items():
            self._state(self.by_key[key], value)
        if up.missing:
            log.warning("Pas de réponse de la PAC pour : %s", ", ".join(up.missing))
        log.info("Uplink décodé : %d valeurs%s", len(up.values),
                 f", {len(up.missing)} manquantes" if up.missing else "")

    def _touch(self, frame: RxFrame, battery_mv: int | None = None) -> None:
        self.last_uplink = time.time()
        meta = {r.key: r for r in self.META}
        if battery_mv is not None:
            self._state(meta["battery"], round(battery_mv / 1000, 3))
        if frame.rssi is not None:
            self._state(meta["rssi"], frame.rssi)
        if frame.snr is not None:
            self._state(meta["snr"], frame.snr)
        self._state(meta["last_uplink"], datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._set_online(True)

    def _ack_write(self, resp) -> None:
        addr = resp.payload[0] << 8 | resp.payload[1] if len(resp.payload) >= 2 else -1
        for key, (reg, value, _) in list(self.pending.items()):
            if reg.address == addr:
                if resp.exception is not None:
                    log.error("La PAC a refusé l'écriture de %s (exception %s)", key, resp.exception)
                else:
                    log.info("Écriture %s=%s confirmée par la PAC", key, value)
                    self._state(reg, reg.decode(reg.encode(value)))
                del self.pending[key]
                return
        log.info("Écho d'écriture reçu pour l'adresse %d", addr)

    # ------------------------------------------------------------------ boucle
    def run(self) -> None:
        h = self.cfg.get("mqtt", {})
        n = self.cfg["lorawan"].get("mqtt", {})
        self.ha.connect_async(h.get("host", "localhost"), int(h.get("port", 1883)), keepalive=60)
        self.net.connect_async(n.get("host", "localhost"), int(n.get("port", 1883)), keepalive=60)
        self.ha.loop_start()
        self.net.loop_start()
        signal.signal(signal.SIGTERM, lambda *_: self.stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())
        log.info("En attente des uplinks (%d commandes, %d octets, intervalle %.0fs)",
                 len(self.plan.commands), self.plan.total_bytes, self.interval)
        while not self.stop_event.wait(30):
            self._set_online(self._is_fresh())
            # une écriture sans écho après 3 intervalles est considérée perdue
            for key, (_, value, t) in list(self.pending.items()):
                if time.time() - t > 3 * self.interval:
                    log.warning("Aucune confirmation pour l'écriture %s=%s", key, value)
                    del self.pending[key]
        self.ha.publish(self.availability, "offline", retain=True)
        for c in (self.ha, self.net):
            c.loop_stop()
            c.disconnect()
        log.info("Arrêt propre")
