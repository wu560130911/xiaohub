"""BlueZ BLE 广播：重放极米遥控器的开机包。

不要在本模块使用 ``from __future__ import annotations``：dbus-fast 会把返回
注解（"s"/"as"/"a{qv}"）当成 D-Bus 签名解析。
"""

import asyncio
import logging
from uuid import uuid4

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.constants import BusType, PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_property, method

_LOGGER = logging.getLogger("xiaohub")

BLUEZ = "org.bluez"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
ADVERTISING_MANAGER = "org.bluez.LEAdvertisingManager1"
ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"

LOCAL_NAME = "Bluetooth 4.0 RC"
SERVICE_UUID = "00001812-0000-1000-8000-00805f9b34fb"
MANUFACTURER_ID = 0x0046
APPLE_COMPANY_ID = 0x004C
REMOTE_TAIL = b"0CRKTM"
ANDROID_TAIL = b"2CRKTM"
IBEACON_TAIL = b"3CRKTM"
APPEARANCE = 961
ADV_MIN_INTERVAL_MS = 20
ADV_MAX_INTERVAL_MS = 30


def valid_ibeacon_body(body: bytes) -> bool:
    """iPhone 极米 App 发出的 21 字节 iBeacon body 特征。"""
    return (
        len(body) == 21
        and body[6:9] == b"\xff\xff\xff"
        and body[9:15] == IBEACON_TAIL
        and body[15:21] == b"\x00" * 6
    )


def android_to_ibeacon_body(token: bytes) -> bytes | None:
    """把安卓 App 的 0x46 16 字节包还原成 21 字节 iBeacon body。"""
    if len(token) != 16 or not token.endswith(ANDROID_TAIL):
        return None
    body = token[1:10] + IBEACON_TAIL + b"\x00" * 6
    return body if valid_ibeacon_body(body) else None


class AdvertisementError(RuntimeError):
    pass


class XgimiAdvertisement(ServiceInterface):
    def __init__(
        self,
        payload: bytes,
        manufacturer_id: int = MANUFACTURER_ID,
        local_name: str = LOCAL_NAME,
        service_uuid: str = SERVICE_UUID,
    ) -> None:
        super().__init__(ADVERTISEMENT_IFACE)
        self._payload = payload
        self._manufacturer_id = manufacturer_id
        self._local_name = local_name
        self._service_uuid = service_uuid

    @dbus_property(access=PropertyAccess.READ, name="Type")
    def advertisement_type(self) -> "s":  # noqa: F821
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ, name="LocalName")
    def local_name(self) -> "s":  # noqa: F821
        return self._local_name

    @dbus_property(access=PropertyAccess.READ, name="ServiceUUIDs")
    def service_uuids(self) -> "as":  # noqa: F722
        return [self._service_uuid] if self._service_uuid else []

    @dbus_property(access=PropertyAccess.READ, name="ManufacturerData")
    def manufacturer_data(self) -> "a{qv}":  # noqa: F722
        return {self._manufacturer_id: Variant("ay", self._payload)}

    @dbus_property(access=PropertyAccess.READ, name="Appearance")
    def appearance(self) -> "q":  # noqa: F821
        return APPEARANCE if self._service_uuid else 0

    @dbus_property(access=PropertyAccess.READ, name="TxPower")
    def tx_power(self) -> "n":  # noqa: F821
        return 0

    @dbus_property(access=PropertyAccess.READ, name="MinInterval")
    def min_interval(self) -> "u":  # noqa: F821
        return ADV_MIN_INTERVAL_MS

    @dbus_property(access=PropertyAccess.READ, name="MaxInterval")
    def max_interval(self) -> "u":  # noqa: F821
        return ADV_MAX_INTERVAL_MS

    @method(name="Release")
    def release(self) -> None:
        pass


class RawAdvertisement(ServiceInterface):
    """只带 ManufacturerData 的广播，避免超过 31 字节。"""

    def __init__(self, manufacturer_id: int, payload: bytes) -> None:
        super().__init__(ADVERTISEMENT_IFACE)
        self._manufacturer_id = manufacturer_id
        self._payload = payload

    @dbus_property(access=PropertyAccess.READ, name="Type")
    def advertisement_type(self) -> "s":  # noqa: F821
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ, name="ManufacturerData")
    def manufacturer_data(self) -> "a{qv}":  # noqa: F722
        return {self._manufacturer_id: Variant("ay", self._payload)}

    @dbus_property(access=PropertyAccess.READ, name="TxPower")
    def tx_power(self) -> "n":  # noqa: F821
        return 0

    @dbus_property(access=PropertyAccess.READ, name="MinInterval")
    def min_interval(self) -> "u":  # noqa: F821
        return ADV_MIN_INTERVAL_MS

    @dbus_property(access=PropertyAccess.READ, name="MaxInterval")
    def max_interval(self) -> "u":  # noqa: F821
        return ADV_MAX_INTERVAL_MS

    @method(name="Release")
    def release(self) -> None:
        pass


async def _managed_objects(bus: MessageBus) -> dict:
    reply = await bus.call(
        Message(
            destination=BLUEZ,
            path="/",
            interface=OBJECT_MANAGER,
            member="GetManagedObjects",
        )
    )
    if reply.message_type == MessageType.ERROR or not reply.body:
        raise AdvertisementError("读取 BlueZ 对象失败，bluetoothd 是否在运行？")
    return reply.body[0]


async def probe_adapter(adapter_path: str) -> str:
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except (OSError, FileNotFoundError) as err:
        raise AdvertisementError(
            "连不上系统 D-Bus；确认宿主机 bluetoothd 在运行、进程有权限读 /run/dbus"
        ) from err
    try:
        managed = await _managed_objects(bus)
    finally:
        bus.disconnect()
    if adapter_path not in managed:
        raise AdvertisementError(f"找不到蓝牙适配器 {adapter_path}")
    if ADVERTISING_MANAGER not in managed[adapter_path]:
        raise AdvertisementError(f"{adapter_path} 不支持 BLE 广播")
    return adapter_path


async def advertise(
    adapter_path: str,
    payload: bytes,
    duration: float,
    *,
    manufacturer_id: int = MANUFACTURER_ID,
    local_name: str = LOCAL_NAME,
    service_uuid: str = SERVICE_UUID,
) -> None:
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        managed = await _managed_objects(bus)
        if adapter_path not in managed:
            raise AdvertisementError(f"找不到蓝牙适配器 {adapter_path}")
        if ADVERTISING_MANAGER not in managed[adapter_path]:
            raise AdvertisementError(f"{adapter_path} 不支持 BLE 广播")

        adv = (
            RawAdvertisement(manufacturer_id, payload)
            if not service_uuid
            else XgimiAdvertisement(
                payload,
                manufacturer_id=manufacturer_id,
                local_name=local_name,
                service_uuid=service_uuid,
            )
        )
        adv_path = f"/com/xiaohub/xgimi/adv_{uuid4().hex}"
        bus.export(adv_path, adv)
        registered = False
        try:
            reply = await bus.call(
                Message(
                    destination=BLUEZ,
                    path=adapter_path,
                    interface=ADVERTISING_MANAGER,
                    member="RegisterAdvertisement",
                    signature="oa{sv}",
                    body=[adv_path, {}],
                )
            )
            if reply.message_type == MessageType.ERROR:
                raise AdvertisementError(
                    f"注册广播失败：{reply.error_name} {reply.body}"
                )
            registered = True
            await asyncio.sleep(duration)
        finally:
            if registered:
                try:
                    await bus.call(
                        Message(
                            destination=BLUEZ,
                            path=adapter_path,
                            interface=ADVERTISING_MANAGER,
                            member="UnregisterAdvertisement",
                            signature="o",
                            body=[adv_path],
                        )
                    )
                except Exception:
                    pass
            try:
                bus.unexport(adv_path, adv)
            except Exception:
                pass
    finally:
        bus.disconnect()


async def list_adapters() -> list[dict]:
    """返回可广播的蓝牙适配器，供配置页面选择。"""
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except (OSError, FileNotFoundError) as err:
        raise AdvertisementError(
            "连不上系统 D-Bus；容器需要挂载 /run/dbus 并让 bluetoothd 在宿主机运行"
        ) from err
    try:
        managed = await _managed_objects(bus)
    finally:
        bus.disconnect()

    result = []
    for path, interfaces in managed.items():
        if ADAPTER_INTERFACE not in interfaces or ADVERTISING_MANAGER not in interfaces:
            continue
        props = interfaces[ADAPTER_INTERFACE]
        name = props.get("Alias") or props.get("Name") or path
        if isinstance(name, Variant):
            name = name.value
        result.append({"path": path, "name": str(name)})
    return sorted(result, key=lambda item: item["path"])


def _mask_hex(data: bytes) -> str:
    text = data.hex().upper()
    return f"{text[:4]}...{text[-4:]}" if len(text) > 8 else "***"


def extract_token(adv) -> tuple[bytes | None, str, str]:
    """从一条 BLE 广播里提取极米开机 token，返回 (token, kind, source)。"""
    sources = []
    sources.extend(
        (f"manufacturer 0x{cid:04X}", bytes(payload))
        for cid, payload in (adv.manufacturer_data or {}).items()
    )
    sources.extend(
        (f"service {uuid}", bytes(payload))
        for uuid, payload in (getattr(adv, "service_data", None) or {}).items()
    )

    for source, data in sources:
        # iPhone 极米 App：iBeacon 的 02 15 + 21 字节 body
        body = None
        if data.startswith(b"\x02\x15") and len(data) >= 23:
            body = data[2:23]
        elif data.startswith(b"\x4c\x00\x02\x15") and len(data) >= 25:
            body = data[4:25]
        elif len(data) >= 21 and data.endswith(IBEACON_TAIL + b"\x00" * 6):
            body = data[-21:]
        if body is not None and valid_ibeacon_body(body):
            return body, "ibeacon", source

        # 遥控器 / 安卓 App：0x0046 的 16 字节包
        if len(data) >= 16 and data.endswith(REMOTE_TAIL):
            return data[-16:], "remote", source
        if len(data) >= 16 and data.endswith(ANDROID_TAIL):
            return data[-16:], "android", source
    return None, "", ""


async def scan_for_tokens(duration: float = 15.0) -> list[dict]:
    """扫描极米开机广播，返回候选 token。

    支持三种来源：
    - 遥控器：0x0046 + 16 字节，尾部 ``0CRKTM``
    - 安卓极米 App：0x0046 + 16 字节，尾部 ``2CRKTM``
    - iPhone 极米 App：Apple iBeacon（0x004C），21 字节 body，尾部 ``3CRKTM``
    """
    try:
        from bleak import BleakScanner
    except ImportError as err:
        raise AdvertisementError("缺少 bleak，执行 pip install bleak") from err

    found: dict[tuple[str, bytes], dict] = {}
    seen = 0

    def looks_like_remote(device, adv, md: dict, sd: dict) -> bool:
        name = (adv.local_name or getattr(device, "name", "") or "").upper()
        address = (getattr(device, "address", "") or "").upper()
        if "RC" in name or "XGIMI" in name or address.startswith("1C:F3"):
            return True
        markers = (REMOTE_TAIL, ANDROID_TAIL, IBEACON_TAIL)
        return any(v.endswith(m) for v in (*md.values(), *sd.values()) for m in markers)

    def on_detect(device, adv) -> None:
        nonlocal seen
        seen += 1
        md = {f"0x{k:04X}": bytes(v) for k, v in (adv.manufacturer_data or {}).items()}
        sd = {str(k): bytes(v) for k, v in (getattr(adv, "service_data", None) or {}).items()}
        token, kind, source = extract_token(adv)
        if token is None:
            if looks_like_remote(device, adv, md, sd):
                _LOGGER.info(
                    "BLE 疑似极米但未识别: name=%r addr=%s rssi=%s md=%s sd=%s",
                    adv.local_name or getattr(device, "name", ""),
                    getattr(device, "address", ""),
                    getattr(adv, "rssi", ""),
                    {k: _mask_hex(v) for k, v in md.items()},
                    {k: _mask_hex(v) for k, v in sd.items()},
                )
            return
        _LOGGER.info(
            "BLE 命中 %s token: %s %s rssi=%s data=%s",
            kind,
            source,
            getattr(device, "address", ""),
            getattr(adv, "rssi", ""),
            _mask_hex(token),
        )
        key = (kind, token)
        if key in found:
            found[key]["count"] += 1
            return
        found[key] = {
            "token": token.hex().upper(),
            "kind": kind,
            "bytes": len(token),
            "name": adv.local_name or getattr(device, "name", "") or "",
            "address": getattr(device, "address", ""),
            "rssi": getattr(adv, "rssi", -999),
            "count": 1,
            "source": source,
        }

    scanner = BleakScanner(detection_callback=on_detect)
    try:
        await scanner.start()
    except Exception as err:
        raise AdvertisementError(f"启动蓝牙扫描失败：{err}") from err
    try:
        await asyncio.sleep(duration)
    finally:
        await scanner.stop()
    _LOGGER.info("BLE 扫描结束: 共看到 %s 个广播, 命中 %s 个 token", seen, len(found))
    return sorted(found.values(), key=lambda item: -int(item["rssi"] or -999))
