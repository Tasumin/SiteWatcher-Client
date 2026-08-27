from __future__ import annotations

import asyncio
from typing import Any

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    get_cmd,
    walk_cmd,
)


def _normalize_oid(value: str) -> str:
    oid = str(value or "").strip().lstrip(".")
    parts = oid.split(".")
    if len(parts) < 2 or any(not part.isdigit() for part in parts):
        raise ValueError("OID must be numeric, for example 1.3.6.1.2.1")
    return oid


async def _get(host: str, community: str, oid: str, port: int, version: str, timeout_seconds: float, retries: int) -> dict[str, Any]:
    if version not in {"1", "2c"}:
        raise ValueError("SNMP probe supports v1 and v2c")
    engine = SnmpEngine()
    auth = CommunityData(community, mpModel=0 if version == "1" else 1)
    target = await UdpTransportTarget.create((host, int(port)), timeout=float(timeout_seconds), retries=int(retries))
    try:
        error_indication, error_status, error_index, var_binds = await get_cmd(engine, auth, target, ContextData(), ObjectType(ObjectIdentity(_normalize_oid(oid))))
        if error_indication: raise RuntimeError(str(error_indication))
        if error_status: raise RuntimeError(f"{error_status.prettyPrint()} at index {int(error_index or 0)}")
        if not var_binds: raise RuntimeError("SNMP response contained no values")
        oid_obj, value_obj = var_binds[0]
        return {"oid": oid_obj.prettyPrint().lstrip("."), "value": value_obj.prettyPrint(), "type": value_obj.__class__.__name__}
    finally:
        engine.close_dispatcher()


async def _walk(host: str, community: str, root_oid: str, port: int, version: str, timeout_seconds: float, retries: int, max_rows: int) -> list[dict[str, Any]]:
    if version not in {"1", "2c"}: raise ValueError("Stage 1 supports SNMP v1 and v2c")
    engine = SnmpEngine(); auth = CommunityData(community, mpModel=0 if version == "1" else 1)
    target = await UdpTransportTarget.create((host, int(port)), timeout=float(timeout_seconds), retries=int(retries))
    root = _normalize_oid(root_oid); rows: list[dict[str, Any]] = []
    try:
        async for error_indication, error_status, error_index, var_binds in walk_cmd(engine, auth, target, ContextData(), ObjectType(ObjectIdentity(root)), lexicographicMode=False, maxRows=int(max_rows)):
            if error_indication: raise RuntimeError(str(error_indication))
            if error_status: raise RuntimeError(f"{error_status.prettyPrint()} at index {int(error_index or 0)}")
            for var_bind in var_binds:
                oid_obj, value_obj = var_bind
                rows.append({"oid": oid_obj.prettyPrint().lstrip("."), "value": value_obj.prettyPrint(), "type": value_obj.__class__.__name__})
                if len(rows) >= max_rows: return rows
        return rows
    finally:
        engine.close_dispatcher()


def run_snmp_get(host: str, community: str, oid: str, port: int = 161, version: str = "2c", timeout_seconds: float = 3, retries: int = 1) -> dict[str, Any]:
    try:
        row = asyncio.run(_get(host, community, oid, port, version, timeout_seconds, retries))
        return {"status":"success", **row}
    except Exception as exc:
        return {"status":"error", "oid":str(oid), "value":None, "type":None, "message":str(exc)}


def probe_snmp(host: str, communities: list[str] | tuple[str, ...] = ("public",), port: int = 161, timeout_seconds: float = 0.5, retries: int = 0) -> dict[str, Any]:
    """Return detected=True only after receiving a valid SNMP response."""
    for community in communities:
        value = str(community or "").strip()
        if not value: continue
        for version in ("2c", "1"):
            try:
                row = asyncio.run(_get(host, value, "1.3.6.1.2.1.1.1.0", port, version, timeout_seconds, retries))
                return {"detected":True,"port":int(port),"version":version,"community":value,"sysDescr":row.get("value")}
            except Exception: continue
    return {"detected":False,"port":int(port)}


def run_snmp_walk(host: str, community: str, root_oid: str = "1.3.6.1.2.1", port: int = 161, version: str = "2c", timeout_seconds: float = 3, retries: int = 1, max_rows: int = 500) -> dict[str, Any]:
    try:
        rows = asyncio.run(_walk(host, community, root_oid, port, version, timeout_seconds, retries, max_rows))
        return {"status":"success","message":f"SNMP walk returned {len(rows)} rows","rows":rows,"rowCount":len(rows)}
    except Exception as exc:
        return {"status":"error","message":str(exc),"rows":[],"rowCount":0}
