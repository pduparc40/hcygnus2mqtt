import base64
import json

import pytest

from hcygnus2mqtt import dragino, frames, lora
from hcygnus2mqtt.lns import RxFrame, make_lns
from hcygnus2mqtt.registers import Register


# ------------------------------------------------------------------ trames
def test_crc_known_vector():
    # requête de référence classique : 01 03 00 00 00 01 → CRC 84 0A
    assert frames.crc16(bytes.fromhex("010300000001")) == bytes.fromhex("840A")


def test_write_frame_fc16():
    f = frames.write_frame(1, "holding", 769, [455])
    assert frames.hexs(f) == "01 10 03 01 00 01 02 01 C7"


def test_find_response_in_noise():
    resp = frames.with_crc(bytes.fromhex("0103040001FFF6"))
    blob = bytes.fromhex("0E1001") + resp + b"\x00\x00"
    r = frames.find_response(blob, 1)
    assert r.function == 3 and r.payload == bytes.fromhex("0001FFF6")
    exc = frames.with_crc(bytes.fromhex("018302"))
    assert frames.find_response(b"\x0e" + exc, 1).exception == 2
    echo = frames.with_crc(bytes.fromhex("011003010001"))
    assert frames.find_response(echo, 1).function == 0x10


# ------------------------------------------------------------------ plan
def regs_sample():
    return [
        Register(key="t_in", name="in", address=256, scale=0.1),
        Register(key="t_out", name="out", address=257, scale=0.1),
        Register(key="comp", name="c", address=288, bit=0, component="binary_sensor"),
        Register(key="mode", name="m", address=300, type="uint16", options={1: "Froid", 2: "Chaud"}),
        Register(key="sp", name="sp", address=769, scale=0.1, component="number", writable=True,
                 verified=True, min=25, max=50),
        Register(key="run", name="r", table="coil", address=5, component="switch", writable=True),
    ]


def payload_for(plan, mem, battery=3600, payver=1):
    data = b""
    for c in plan.commands:
        if c.table in ("coil", "discrete"):
            bits = [mem.get((c.table, c.start + i), 0) for i in range(c.count)]
            data += bytes(sum(b << j for j, b in enumerate(bits[k:k + 8])) for k in range(0, c.count, 8))
        else:
            for a in range(c.start, c.start + c.count):
                data += mem.get((c.table, a), 0).to_bytes(2, "big")
    return battery.to_bytes(2, "big") + bytes((payver,)) + data


def test_plan_and_at_commands(tmp_path):
    plan = dragino.build_plan(regs_sample())
    assert plan.mode == "single"
    assert [(c.table, c.start, c.count) for c in plan.commands] == [
        ("holding", 256, 2), ("holding", 288, 1), ("holding", 300, 1), ("holding", 769, 1), ("coil", 5, 1)]
    at = dragino.at_commands(plan, {"baudrate": 9600, "parity": "E"}, 600)
    assert "AT+PARITY=2" in at
    assert "AT+COMMAND1=01 03 01 00 00 02,1" in at
    assert "AT+DATACUT1=9,2,4~7" in at
    assert "AT+COMMAND5=01 01 00 05 00 01,1" in at and "AT+DATACUT5=6,2,4~4" in at
    p = tmp_path / "plan.json"
    plan.save(p)
    assert dragino.Plan.load(p) == plan


def test_plan_switches_to_multi_when_too_big():
    regs = [Register(key=f"r{i}", name="r", address=1000 + i) for i in range(40)]
    plan = dragino.build_plan(regs)
    assert plan.mode == "multi" and plan.segments == 2


def test_plan_too_many_commands():
    regs = [Register(key=f"r{i}", name="r", address=i * 100) for i in range(16)]
    with pytest.raises(dragino.PlanError):
        dragino.build_plan(regs)


def test_decode_single_uplink():
    regs = regs_sample()
    plan = dragino.build_plan(regs)
    mem = {("holding", 256): 401, ("holding", 257): 0xFFF6, ("holding", 288): 1,
           ("holding", 300): 2, ("holding", 769): 450, ("coil", 5): 1}
    up = dragino.Decoder(plan, {r.key: r for r in regs}).feed(payload_for(plan, mem, battery=0x8E10))
    assert up.battery_mv == 0x0E10 and up.interrupt
    assert up.values == {"t_in": 40.1, "t_out": -1.0, "comp": True, "mode": "Chaud", "sp": 45.0, "run": True}
    # mauvaise longueur ou PAYVER → ignoré
    assert dragino.Decoder(plan, {}).feed(payload_for(plan, mem)[:-1]) is None
    assert dragino.Decoder(plan, {}).feed(payload_for(plan, mem, payver=2)) is None


def test_decode_missing_block_filled_with_ff():
    regs = regs_sample()
    plan = dragino.build_plan(regs)
    raw = bytearray(payload_for(plan, {("holding", 256): 401}))
    c = plan.commands[0]
    raw[3 + c.offset:3 + c.offset + c.data_len] = b"\xff" * c.data_len
    up = dragino.Decoder(plan, {r.key: r for r in regs}).feed(bytes(raw))
    assert "t_in" in up.missing and "t_out" in up.missing and "sp" in up.values


def test_decode_multi_segments():
    regs = [Register(key=f"r{i}", name="r", address=1000 + i) for i in range(40)]
    plan = dragino.build_plan(regs)
    data = b"".join(i.to_bytes(2, "big") for i in range(40))
    dec = dragino.Decoder(plan, {r.key: r for r in regs})
    head = bytes.fromhex("0E10") + bytes((plan.payver, 2))
    assert dec.feed(head + b"\x01" + data[46:], now=0) is None      # segments dans le désordre
    up = dec.feed(head + b"\x00" + data[:46], now=1)
    assert up.values["r0"] == 0 and up.values["r39"] == 39


def test_downlinks():
    reg = regs_sample()[4]
    dl = dragino.downlink_write(1, reg, "45.5")
    assert frames.hexs(dl) == "A8 01 09 01 10 03 01 00 01 02 01 C7 08"
    assert frames.hexs(dragino.downlink_read(1, "holding", 256, 4)) == "A8 01 06 01 03 01 00 00 04 0D"
    assert frames.hexs(dragino.downlink_interval(600)) == "01 00 02 58"


def test_js_decoder_mentions_every_register():
    regs = regs_sample()
    js = dragino.js_decoder(dragino.build_plan(regs), {r.key: r for r in regs})
    for r in regs:
        assert f"out.{r.key} =" in js


# ------------------------------------------------------------------ serveurs LoRaWAN
def test_ttn_parse_and_downlink():
    lns = make_lns({"type": "ttn", "application_id": "app", "device_id": "dev"})
    assert lns.uplink_topic == "v3/app@ttn/devices/dev/up"
    msg = {"uplink_message": {"f_port": 2, "f_cnt": 7, "frm_payload": base64.b64encode(b"\x0e\x10\x01").decode(),
                              "rx_metadata": [{"rssi": -110, "snr": 2.5}, {"rssi": -90, "snr": 8}]}}
    f = lns.parse(json.dumps(msg).encode())
    assert (f.fport, f.payload, f.rssi, f.snr) == (2, b"\x0e\x10\x01", -90, 8)
    dl = json.loads(lns.downlink(2, b"\xa8"))
    assert dl["downlinks"][0]["frm_payload"] == "qA==" and dl["downlinks"][0]["f_port"] == 2


def test_chirpstack_parse_and_downlink():
    lns = make_lns({"type": "chirpstack", "application_id": "abc", "dev_eui": "A84041FFFF000001"})
    assert lns.uplink_topic == "application/abc/device/a84041ffff000001/event/up"
    f = lns.parse(json.dumps({"fPort": 2, "data": "DhAB", "rxInfo": [{"rssi": -100, "snr": 5}]}).encode())
    assert f.payload == b"\x0e\x10\x01" and f.rssi == -100
    assert json.loads(lns.downlink(2, b"\x01"))["devEui"] == "a84041ffff000001"


# ------------------------------------------------------------------ passerelle
class FakeMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload))

    def __getattr__(self, name):          # will_set, tls_set, … : sans effet
        return lambda *a, **k: None


def make_bridge(tmp_path, monkeypatch, allow_writes=True):
    (tmp_path / "regs.yaml").write_text(
        "registers:\n"
        "  - {key: t_out, name: Départ, address: 257, scale: 0.1}\n"
        "  - {key: comp, name: Compresseur, address: 288, bit: 0, component: binary_sensor}\n"
        "  - {key: sp, name: Consigne, address: 769, scale: 0.1, component: number,"
        " writable: true, verified: true, min: 25, max: 50}\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        "transport: lorawan\nregisters: regs.yaml\n"
        "lorawan: {type: ttn, application_id: app, device_id: dev, interval: 600}\n"
        f"options: {{allow_writes: {str(allow_writes).lower()}}}\n",
        encoding="utf-8",
    )
    clients = []
    monkeypatch.setattr(lora, "_mqtt_client", lambda cfg, cid: clients.append(FakeMqtt()) or clients[-1])
    b = lora.LoraBridge(str(tmp_path / "config.yaml"))
    return b, clients[0], clients[1]          # pont, broker HA, broker LoRaWAN


def test_bridge_uplink_to_home_assistant(tmp_path, monkeypatch):
    b, ha, _ = make_bridge(tmp_path, monkeypatch)
    raw = payload_for(b.plan, {("holding", 257): 452, ("holding", 288): 1, ("holding", 769): 450})
    b.handle_uplink(RxFrame(2, raw, rssi=-95, snr=6.5))
    states = dict(ha.published)
    assert states["hcygnus2mqtt/hcygnus171/t_out/state"] == "45.2"
    assert states["hcygnus2mqtt/hcygnus171/comp/state"] == "ON"
    assert states["hcygnus2mqtt/hcygnus171/sp/state"] == "45.0"
    assert states["hcygnus2mqtt/hcygnus171/battery/state"] == "3.6"
    assert states["hcygnus2mqtt/hcygnus171/rssi/state"] == "-95"
    assert states["hcygnus2mqtt/hcygnus171/availability"] == "online"


def test_bridge_write_goes_out_as_downlink_and_is_acked(tmp_path, monkeypatch):
    b, ha, net = make_bridge(tmp_path, monkeypatch)
    b.handle_command("sp", "47")
    topic, body = net.published[-1]
    assert topic == "v3/app@ttn/devices/dev/down/push"
    sent = base64.b64decode(json.loads(body)["downlinks"][0]["frm_payload"])
    assert frames.hexs(sent) == "A8 01 09 01 10 03 01 00 01 02 01 D6 08"
    assert "sp" in b.pending
    # le Dragino remonte l'écho FC16 de la PAC
    echo = frames.with_crc(bytes.fromhex("011003010001"))
    b.handle_uplink(RxFrame(2, bytes.fromhex("0E1001") + echo))
    assert "sp" not in b.pending
    assert ("hcygnus2mqtt/hcygnus171/sp/state", "47.0") in ha.published


def test_bridge_refuses_writes_by_default(tmp_path, monkeypatch):
    b, _, net = make_bridge(tmp_path, monkeypatch, allow_writes=False)
    b.handle_command("sp", "47")
    assert not net.published and not b.pending
