"""投影仪配置接口：适配器列表、遥控器 token 扫描、保存、测试。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from xiaohub.api.dependencies import app, config, log, verification
from xiaohub.services.xgimi.ble import (
    AdvertisementError,
    list_adapters,
    scan_for_tokens,
)

router = APIRouter(dependencies=[Depends(verification)])

MASK = "******"
EDITABLE = {
    "enabled",
    "ip",
    "token",
    "adapter",
    "alive_port",
    "alive_ports",
    "adv_rounds",
    "adv_duration",
    "incremental_token",
    "off_cooldown",
    "off_presses",
    "off_interval",
    "on_guard",
    "on",
    "off",
    "actions",
    "feedback",
    "announce_ready",
    "tts",
}


def _options() -> dict:
    return dict((config.services or {}).get("xgimi") or {})


def _split_words(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


@router.get("/api/xgimi/config")
def get_xgimi_config():
    options = _options()
    safe = dict(options)
    if safe.get("token"):
        safe["token"] = MASK
    return {"success": True, "config": safe}


@router.post("/api/xgimi/config")
async def save_xgimi_config(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")

    old = _options()
    new = dict(old)
    for key in EDITABLE:
        if key in body:
            new[key] = body[key]

    if new.get("token") == MASK:
        new["token"] = old.get("token", "")

    token = str(new.get("token", "")).replace(" ", "").replace(":", "").upper()
    if token.startswith("4C000215") and len(token) == 50:
        token = token[8:]
    elif token.startswith("0215") and len(token) == 46:
        token = token[4:]
    if token:
        if len(token) not in (32, 42) or any(ch not in "0123456789ABCDEF" for ch in token):
            raise HTTPException(
                status_code=400,
                detail="token 必须是 32 位（遥控器/安卓）或 42 位（iPhone iBeacon）十六进制",
            )
    new["token"] = token

    for key in ("on", "off"):
        if key in new:
            new[key] = _split_words(new[key])
    new["enabled"] = bool(new.get("enabled", True))

    services = dict(config.services or {})
    services["xgimi"] = new
    app.update_config_from_setting({"services": services})
    app.save_cur_config()
    log.info("xgimi 配置已更新")
    return {"success": True, "config": {**new, "token": MASK if token else ""}}


@router.get("/api/xgimi/adapters")
async def get_adapters():
    try:
        adapters = await list_adapters()
        return {"success": True, "adapters": adapters}
    except AdvertisementError as err:
        return {"success": False, "message": str(err), "adapters": []}


@router.post("/api/xgimi/scan")
async def scan_token(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    seconds = float(body.get("seconds", 15))
    seconds = min(max(seconds, 5), 30)
    try:
        tokens = await asyncio.wait_for(scan_for_tokens(seconds), timeout=seconds + 8)
    except AdvertisementError as err:
        return {"success": False, "message": str(err), "tokens": []}
    except asyncio.TimeoutError:
        return {"success": False, "message": "扫描超时", "tokens": []}
    message = "" if tokens else "没抓到广播，确认投影仪已关机、遥控器有电且靠近 NAS"
    return {"success": bool(tokens), "tokens": tokens, "message": message}


@router.get("/api/xgimi/status")
async def xgimi_status():
    service = app.service_registry.get("xgimi") if app.service_registry else None
    device = getattr(service, "device", None) if service is not None else None
    if device is None:
        return {"success": False, "message": "投影仪服务未启用或未就绪"}
    try:
        detail = await device.probe_state()
        return {"success": True, **detail}
    except Exception as err:
        return {"success": False, "message": str(err)}


@router.post("/api/xgimi/test")
async def test_xgimi(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = body.get("action", "")
    service = app.service_registry.get("xgimi") if app.service_registry else None
    if service is None or getattr(service, "device", None) is None:
        return {"success": False, "message": "投影仪服务未启用，先保存配置"}
    try:
        if action == "on":
            await service.device.wake()
        elif action == "off":
            await service.device.power_off()
        else:
            raise HTTPException(status_code=400, detail="action 只能是 on 或 off")
    except HTTPException:
        raise
    except Exception as err:
        return {"success": False, "message": str(err)}
    return {"success": True}
