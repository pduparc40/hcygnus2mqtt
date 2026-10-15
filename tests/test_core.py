from pathlib import Path

import pytest

from hcygnus2mqtt import explore
from hcygnus2mqtt.discovery import config_payload
from hcygnus2mqtt.modbus import ModbusError
from hcygnus2mqtt.registers import Register, build_blocks, load_registers

ROOT = Path(__file__).resolve().parents[1]


class FakeModbus:
    """Esclave simulé : seules les adresses présentes dans `mem` sont lisibles."""

    def __init__(self, mem):
        self.mem = mem
        self.calls = 0

    def read(self, table, address, count):
        self.calls += 1
        out = []
        for a in range(address, address + count):
            if (table, a) not in self.mem:
                raise ModbusError("illegal address", 2)
            out.append(self.mem[(table, a)])
        return out

    def write(self, table, address, words):
        for i, w in enumerate(words):
            self.mem[(table, address + i)] = w


def test_decode_signed_scaled():
    r = Register(key="t", name="t", type="int16", scale=0.1)
    assert r.decode([215]) == 21.5
    assert r.decode([0xFFF6]) == -1.0


def test_decode_bit_and_enum():
    assert Register(key="b", name="b", bit=3, component="binary_sensor").decode([0b1000]) is True
    r = Register(key="m", name="m", type="uint16", options={0: "Arrêt", 2: "Chaud"})
    assert r.decode([2]) == "Chaud"
    assert r.decode([9]).startswith("inconnu")


def test_int32_word_order():
    r = Register(key="c", name="c", type="uint32")
    assert r.decode([1, 2]) == 65538
    assert Register(key="c", name="c", type="uint32", word_order="little").decode([2, 1]) == 65538


def test_encode_roundtrip_and_limits():
    r = Register(key="sp", name="sp", component="number", scale=0.1, min=25, max=50, writable=True)
    assert r.encode("45.5") == [455]
    assert r.decode(r.encode("45.5")) == 45.5
    with pytest.raises(ValueError):
        r.encode("60")
    sel = Register(key="m", name="m", component="select", options={1: "Froid", 2: "Chaud"}, writable=True)
    assert sel.encode("chaud") == [2]


def test_input_table_not_writable():
    with pytest.raises(ValueError):
        Register(key="x", name="x", table="input", writable=True)


def test_blocks_grouping():
    regs = [Register(key=f"r{a}", name="r", address=a) for a in (256, 257, 260, 300, 301)]
    regs.append(Register(key="d", name="d", type="int32", address=302))
    blocks = build_blocks(regs, max_gap=8)
    assert [(b.start, b.count) for b in blocks] == [(256, 5), (300, 4)]


def test_shipped_register_map_is_valid():
    regs = load_registers(ROOT / "registers" / "hcygnus_tech.yaml")
    assert len(regs) > 5
    for r in regs:
        topic, payload = config_payload(r, "hcygnus2mqtt", {"id": "pac"}, "a/availability")
        assert topic.startswith("homeassistant/")
        assert payload["unique_id"] == f"pac_{r.key}"


def test_readonly_number_becomes_sensor():
    r = Register(key="sp", name="sp", component="number", writable=False)
    topic, payload = config_payload(r, "b", {"id": "pac"}, "av")
    assert topic.startswith("homeassistant/sensor/")
    assert "command_topic" not in payload


def test_scan_bisects_around_holes():
    mem = {("holding", a): a * 10 for a in list(range(5)) + list(range(9, 16))}
    snap = explore.scan(FakeModbus(mem), ["holding:0-15"], chunk=16, out=None)
    assert sorted(a for _, a in snap) == list(range(5)) + list(range(9, 16))
    assert snap[("holding", 12)] == 120


def test_diff(tmp_path, capsys):
    mb = FakeModbus({("holding", 0): 1, ("holding", 1): 450})
    a = tmp_path / "a.csv"
    explore.scan(mb, ["holding:0-1"], 16, a)
    mb.mem[("holding", 1)] = 455
    b = tmp_path / "b.csv"
    explore.scan(mb, ["holding:0-1"], 16, b)
    explore.diff(a, b)
    out = capsys.readouterr().out
    assert "450 →    455" in out and "Δ=+5" in out


def test_bridge_poll_and_write(tmp_path, monkeypatch):
    from hcygnus2mqtt import bridge as bridge_mod

    (tmp_path / "regs.yaml").write_text(
        "registers:\n"
        "  - {key: t_out, name: Départ, address: 257, scale: 0.1}\n"
        "  - {key: comp, name: Compresseur, address: 288, bit: 0, component: binary_sensor}\n"
        "  - {key: sp, name: Consigne, address: 769, scale: 0.1, component: number,"
        " writable: true, verified: true, min: 25, max: 50}\n",
        encoding="utf-8",
    )
    (tmp_path / "config.yaml").write_text(
        "registers: regs.yaml\noptions: {allow_writes: true}\n", encoding="utf-8"
    )
    published = []

    class FakeMqtt:
        def publish(self, topic, payload, retain=False):
            published.append((topic, payload))

    monkeypatch.setattr(bridge_mod.Bridge, "_make_mqtt", lambda self: FakeMqtt())
    b = bridge_mod.Bridge(str(tmp_path / "config.yaml"))
    b.modbus = FakeModbus({("holding", 257): 452, ("holding", 288): 1, ("holding", 769): 450})

    assert b.poll_once() == 3
    states = dict(published)
    assert states["hcygnus2mqtt/hcygnus171/t_out/state"] == "45.2"
    assert states["hcygnus2mqtt/hcygnus171/comp/state"] == "ON"

    b.handle_command("sp", "47")
    assert b.modbus.mem[("holding", 769)] == 470
    assert published[-1] == ("hcygnus2mqtt/hcygnus171/sp/state", "47.0")

    # registre non vérifié : écriture refusée
    b.by_key["sp"].verified = False
    b.handle_command("sp", "30")
    assert b.modbus.mem[("holding", 769)] == 470


def test_find_candidates_prefers_tenths(tmp_path):
    snap = {("holding", 257): 452, ("holding", 258): 0xFFDD, ("holding", 900): 45, ("coil", 1): 1}
    c = explore.find_candidates(snap, 45.2, 0.3)
    assert (c[0][2], c[0][4]) == (257, 0.1)
    assert [x[2] for x in explore.find_candidates(snap, -3.5, 0.1)] == [258]
    # 45 (×1) est aussi à 0,2 de 45,2 : il est proposé, mais après
    assert 900 in [x[2] for x in c]


def test_find_reports_clusters(tmp_path, capsys):
    mb = FakeModbus({("holding", 256): 401, ("holding", 257): 452, ("holding", 258): 0xFFDD})
    out = tmp_path / "s.csv"
    explore.scan(mb, ["holding:256-258"], 16, out)
    explore.find(out, [40.1, 45.2, -3.5], 0.2)
    text = capsys.readouterr().out
    assert "256=40.1, 257=45.2, 258=-3.5" in text
