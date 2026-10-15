"""Pont Modbus RTU → MQTT avec auto-découverte Home Assistant."""
from __future__ import annotations

import json
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import yaml

from .discovery import config_payload, topics
from .modbus import Modbus, ModbusError
from .registers import Register, build_blocks, load_registers

log = logging.getLogger("hcygnus2mqtt")


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    return str(value)


class Bridge:
    def __init__(self, config_path: str) -> None:
        self.cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        base_dir = Path(config_path).resolve().parent

        self.dev = self.cfg.get("device", {})
        self.dev.setdefault("id", "hcygnus171")
        self.base = self.cfg.get("mqtt", {}).get("base_topic", "hcygnus2mqtt")
        self.availability = f"{self.base}/{self.dev['id']}/availability"

        opts = self.cfg.get("options", {})
        self.poll = float(opts.get("poll_interval", 10))
        self.force_every = int(opts.get("republish_every", 30))   # cycles
        self.allow_writes = bool(opts.get("allow_writes", False))
        self.allow_unverified_writes = bool(opts.get("allow_unverified_writes", False))
        include_unverified = bool(opts.get("include_unverified", True))

        reg_file = Path(self.cfg.get("registers", "registers/hcygnus_tech.yaml"))
        if not reg_file.is_absolute():
            reg_file = base_dir / reg_file
        regs = load_registers(reg_file)
        enabled = set(opts.get("only", []) or [])
        disabled = set(opts.get("exclude", []) or [])
        self.registers: list[Register] = [
            r for r in regs
            if (include_unverified or r.verified)
            and (not enabled or r.key in enabled)
            and r.key not in disabled
        ]
        self.by_key = {r.key: r for r in self.registers}
        self.blocks = build_blocks(
            self.registers, int(opts.get("max_gap", 8)), int(opts.get("max_block", 32))
        )
        self.raw_ranges = self.cfg.get("debug", {}).get("raw_ranges", []) or []

        self.modbus = Modbus(self.cfg.get("modbus", {}), int(self.dev.get("unit", 1)))
        self.last: dict[str, Any] = {}
        self.last_raw: dict[str, list[int]] = {}
        self.failed_blocks: dict[tuple, float] = {}
        self.stop_event = threading.Event()
        self.mqtt = self._make_mqtt()

    # ------------------------------------------------------------------ MQTT
    def _make_mqtt(self) -> mqtt.Client:
        m = self.cfg.get("mqtt", {})
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=m.get("client_id", f"hcygnus2mqtt-{self.dev['id']}")
        )
        if m.get("username"):
            client.username_pw_set(m["username"], m.get("password"))
        if m.get("tls"):
            client.tls_set()
        client.will_set(self.availability, "offline", retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.reconnect_delay_set(1, 60)
        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            log.error("Connexion MQTT refusée : %s", reason_code)
            return
        log.info("Connecté au broker MQTT")
        client.subscribe(f"{self.base}/{self.dev['id']}/+/set")
        client.subscribe("homeassistant/status")
        self.publish_discovery()
        self.last.clear()  # republie tout après une reconnexion

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="replace").strip()
        if msg.topic == "homeassistant/status":
            if payload == "online":
                log.info("Home Assistant redémarré : republication de la découverte")
                self.publish_discovery()
                self.last.clear()
            return
        key = msg.topic.split("/")[-2]
        self.handle_command(key, payload)

    def publish_discovery(self) -> None:
        if not self.cfg.get("options", {}).get("discovery", True):
            return
        for reg in self.registers:
            topic, payload = config_payload(reg, self.base, self.dev, self.availability)
            self.mqtt.publish(topic, json.dumps(payload, ensure_ascii=False), retain=True)
        log.info("Découverte HA publiée pour %d entités", len(self.registers))

    # ------------------------------------------------------------------ écriture
    def handle_command(self, key: str, payload: str) -> None:
        reg = self.by_key.get(key)
        if reg is None:
            log.warning("Commande pour une clé inconnue : %s", key)
            return
        if not (self.allow_writes and reg.writable):
            log.warning("Écriture refusée pour %s (allow_writes=false ou registre non inscriptible)", key)
            return
        if not reg.verified and not self.allow_unverified_writes:
            log.warning("Écriture refusée pour %s : registre non vérifié", key)
            return
        try:
            words = reg.encode(payload)
            log.info("Écriture %s = %s → %s@%d %s", key, payload, reg.table, reg.address, words)
            self.modbus.write(reg.table, reg.address, words)
            # relecture immédiate pour confirmer
            value = reg.decode(self.modbus.read(reg.table, reg.address, reg.width))
            self._publish_value(reg, value, force=True)
        except (ValueError, ModbusError) as exc:
            log.error("Échec d'écriture %s : %s", key, exc)

    # ------------------------------------------------------------------ lecture
    def _publish_value(self, reg: Register, value: Any, force: bool = False) -> None:
        if force or self.last.get(reg.key) != value:
            self.mqtt.publish(topics(self.base, self.dev["id"], reg)["state"], _fmt(value), retain=True)
            self.last[reg.key] = value

    def poll_once(self, force: bool = False) -> int:
        ok = 0
        now = time.monotonic()
        for block in self.blocks:
            bkey = (block.table, block.start)
            # un bloc qui a échoué est ré-essayé au plus toutes les 5 min
            if bkey in self.failed_blocks and now - self.failed_blocks[bkey] < 300:
                continue
            try:
                words = self.modbus.read(block.table, block.start, block.count)
            except ModbusError as exc:
                if len(block.registers) > 1:
                    # on retombe sur une lecture registre par registre pour isoler l'adresse fautive
                    ok += self._poll_individually(block, force)
                else:
                    log.warning("Lecture %s@%d impossible : %s", block.table, block.start, exc)
                    self.failed_blocks[bkey] = now
                continue
            self.failed_blocks.pop(bkey, None)
            for reg in block.registers:
                i = reg.address - block.start
                self._publish_value(reg, reg.decode(words[i:i + reg.width]), force)
                ok += 1
        self._poll_raw()
        return ok

    def _poll_individually(self, block, force: bool) -> int:
        ok = 0
        for reg in block.registers:
            try:
                words = self.modbus.read(reg.table, reg.address, reg.width)
                self._publish_value(reg, reg.decode(words), force)
                ok += 1
            except ModbusError as exc:
                log.warning("Lecture %s (%s@%d) impossible : %s", reg.key, reg.table, reg.address, exc)
        return ok

    def _poll_raw(self) -> None:
        """Publie des plages brutes : pratique pour deviner les registres depuis MQTT Explorer."""
        for rng in self.raw_ranges:
            table, start, count = rng["table"], int(rng["start"]), int(rng.get("count", 16))
            name = f"{table}/{start}"
            try:
                words = self.modbus.read(table, start, count)
            except ModbusError as exc:
                log.debug("Plage brute %s illisible : %s", name, exc)
                continue
            previous = self.last_raw.get(name)
            if previous != words:
                root = f"{self.base}/{self.dev['id']}/raw/{table}"
                block = {start + i: w for i, w in enumerate(words)}
                self.mqtt.publish(f"{root}/{start}", json.dumps(block), retain=True)
                for i, w in enumerate(words):
                    if previous is None or previous[i] != w:
                        self.mqtt.publish(f"{root}/addr/{start + i}", str(w), retain=True)
                self.last_raw[name] = words

    # ------------------------------------------------------------------ boucle
    def run(self) -> None:
        m = self.cfg.get("mqtt", {})
        self.mqtt.connect_async(m.get("host", "localhost"), int(m.get("port", 1883)), keepalive=60)
        self.mqtt.loop_start()

        signal.signal(signal.SIGTERM, lambda *_: self.stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())

        cycle, online = 0, None
        log.info("%d registres en %d blocs, interrogation toutes les %.0fs",
                 len(self.registers), len(self.blocks), self.poll)
        while not self.stop_event.is_set():
            started = time.monotonic()
            ok = self.poll_once(force=(cycle % self.force_every == 0))
            is_online = ok > 0 or not self.registers
            if is_online != online:
                self.mqtt.publish(self.availability, "online" if is_online else "offline", retain=True)
                log.info("PAC %s", "joignable" if is_online else "INJOIGNABLE")
                online = is_online
            cycle += 1
            self.stop_event.wait(max(0.0, self.poll - (time.monotonic() - started)))

        self.mqtt.publish(self.availability, "offline", retain=True)
        self.mqtt.loop_stop()
        self.mqtt.disconnect()
        self.modbus.close()
        log.info("Arrêt propre")
