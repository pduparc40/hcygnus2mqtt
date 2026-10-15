"""Messages d'auto-découverte MQTT pour Home Assistant."""
from __future__ import annotations

from . import __version__
from .registers import Register


def device_block(dev: dict) -> dict:
    return {
        "identifiers": [dev["id"]],
        "name": dev.get("name", "PAC MTA HCygnus"),
        "manufacturer": "MTA",
        "model": dev.get("model", "HCygnus Tech 171"),
        "sw_version": f"hcygnus2mqtt {__version__}",
    }


def topics(base: str, dev_id: str, reg: Register) -> dict:
    root = f"{base}/{dev_id}/{reg.key}"
    return {"state": f"{root}/state", "command": f"{root}/set"}


def config_payload(reg: Register, base: str, dev: dict, availability: str) -> tuple[str, dict]:
    """Renvoie (topic de config, payload) pour une entité."""
    dev_id = dev["id"]
    component = reg.component
    # Un registre non inscriptible ne peut pas devenir number/select/switch.
    if not reg.writable and component in ("number", "select", "switch"):
        component = "binary_sensor" if component == "switch" else "sensor"

    t = topics(base, dev_id, reg)
    payload: dict = {
        "name": reg.name,
        "unique_id": f"{dev_id}_{reg.key}",
        "object_id": f"{dev_id}_{reg.key}",
        "state_topic": t["state"],
        "availability_topic": availability,
        "device": device_block(dev),
    }
    if reg.icon:
        payload["icon"] = reg.icon
    if not reg.verified:
        # visible dans HA : rappelle que la correspondance est encore une hypothèse
        payload["entity_category"] = "diagnostic"

    if component == "sensor":
        if reg.unit:
            payload["unit_of_measurement"] = reg.unit
        if reg.device_class:
            payload["device_class"] = reg.device_class
        if reg.state_class:
            payload["state_class"] = reg.state_class
        if reg.options:
            payload["device_class"] = "enum"
            payload["options"] = list(reg.options.values())
            payload.pop("unit_of_measurement", None)
            payload.pop("state_class", None)
    elif component == "binary_sensor":
        payload.update(payload_on="ON", payload_off="OFF")
        if reg.device_class:
            payload["device_class"] = reg.device_class
    elif component == "switch":
        payload.update(command_topic=t["command"], payload_on="ON", payload_off="OFF")
    elif component == "number":
        payload.update(
            command_topic=t["command"],
            min=reg.min if reg.min is not None else -100,
            max=reg.max if reg.max is not None else 100,
            step=reg.step if reg.step is not None else (reg.scale or 1),
            mode="box",
        )
        if reg.unit:
            payload["unit_of_measurement"] = reg.unit
        if reg.device_class:
            payload["device_class"] = reg.device_class
        payload["entity_category"] = "config"
    elif component == "select":
        payload.update(command_topic=t["command"], options=list(reg.options.values()))

    return f"homeassistant/{component}/{dev_id}/{reg.key}/config", payload
