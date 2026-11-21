"""服务注册、分发与投影仪服务的自测（不碰真实硬件）。

直接运行：``python test/test_services.py``
"""

import asyncio
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xiaohub.services.base import Service
from xiaohub.services.music import MusicService
from xiaohub.services.registry import ServiceRegistry
from xiaohub.services.xgimi.matcher import detect_intent
from xiaohub.services.xgimi.device import XgimiDevice
from xiaohub.services.xgimi.service import XgimiService
from xiaohub.voice.router import VoiceRouter

log = logging.getLogger("test")
log.addHandler(logging.NullHandler())


class Dummy(Service):
    def __init__(self, name, priority=100, fallback=False, claim=False, boom=False):
        super().__init__(config=None, log=log)
        self.name = name
        self.priority = priority
        self.fallback = fallback
        self.claim = claim
        self.boom = boom
        self.calls = 0

    async def handle(self, did="", query="", ctrl_panel=False, **kwargs):
        self.calls += 1
        if self.boom:
            raise RuntimeError("boom")
        return self.claim


def test_registry_order():
    registry = ServiceRegistry(log)
    fallback = Dummy("music", priority=1000, fallback=True)
    late = Dummy("late", priority=50)
    early = Dummy("early", priority=10)
    registry.register(fallback)
    registry.register(late)
    registry.register(early)
    assert [s.name for s in registry.ordered()] == ["early", "late", "music"]


def test_router_first_claim_wins():
    async def run():
        registry = ServiceRegistry(log)
        first = Dummy("first", priority=10, claim=True)
        second = Dummy("second", priority=20, claim=True)
        registry.register(first)
        registry.register(second)
        router = VoiceRouter(registry, log)
        assert await router.dispatch(did="d", query="打开投影") is True
        assert first.calls == 1 and second.calls == 0

    asyncio.run(run())


def test_router_skips_and_survives_exception():
    async def run():
        registry = ServiceRegistry(log)
        boom = Dummy("boom", priority=10, boom=True)
        quiet = Dummy("quiet", priority=20, claim=True)
        registry.register(boom)
        registry.register(quiet)
        router = VoiceRouter(registry, log)
        assert await router.dispatch(did="d", query="打开投影") is True
        assert boom.calls == 1 and quiet.calls == 1

    asyncio.run(run())


def test_music_is_fallback():
    class Handler:
        def __init__(self):
            self.calls = []

        async def do_check_cmd(self, did="", query="", ctrl_panel=True, **kwargs):
            self.calls.append((did, query))

    async def run():
        handler = Handler()
        music = MusicService(handler, config=None, log=log)
        registry = ServiceRegistry(log)
        registry.register(music)
        router = VoiceRouter(registry, log)
        assert await router.dispatch(did="d", query="播放歌曲晴天") is True
        assert handler.calls == [("d", "播放歌曲晴天")]

    asyncio.run(run())


def test_matcher_phrases():
    on = ["打开投影"]
    off = ["关闭投影"]
    cases = {
        "打开投影仪": "on",
        "帮我打开投影": "on",
        "把投影打开": "on",
        "投影开机": "on",
        "投影开了吗": None,
        "关闭投影": "off",
        "把投影关了": "off",
        "关掉投影仪": "off",
        "关灯": None,
        "播放歌曲晴天": None,
    }
    for text, want in cases.items():
        got = detect_intent(text, on, off)
        assert got == want, f"{text}: got {got}, want {want}"


class FakeProjector:
    def __init__(self):
        self.on = False
        self.wakes = 0
        self.offs = 0
        self.keys = []

    async def is_on(self):
        return self.on

    async def wake(self):
        self.wakes += 1
        self.on = True

    async def power_off(self):
        self.offs += 1
        self.on = False

    async def send_key(self, name):
        self.keys.append(name)


class FakeApp:
    def __init__(self):
        self.said = []
        self.device_manager = type("DM", (), {"devices": {"d": self._device()}})()

    def _device(self):
        class D:
            async def group_force_stop_xiaoai(self):
                return True

        return D()

    async def do_tts(self, did, text):
        self.said.append(text)


def test_xgimi_service_flow():
    async def run():
        app = FakeApp()
        service = XgimiService(
            config=None,
            log=log,
            app=app,
            options={
                "ip": "192.0.2.1",
                "token": "00" * 16,
                "adv_rounds": 2,
                "actions": {"投影静音": "volumemute"},
            },
        )
        fake = FakeProjector()
        service.device = fake  # type: ignore[assignment]

        assert await service.handle(did="d", query="播放歌曲晴天") is False
        assert await service.handle(did="d", query="帮我把投影打开") is True
        await asyncio.sleep(1.2)  # 广播在后台任务里跑
        assert fake.wakes == 2
        assert app.said[-1] == "开机中"

        assert await service.handle(did="d", query="打开投影") is True
        assert app.said[-1] == "已经开着呢"

        assert await service.handle(did="d", query="关闭投影") is True
        assert fake.offs == 1
        assert app.said[-1] == "关了"

        assert await service.handle(did="d", query="打开投影") is True
        assert app.said[-1] == "刚关掉，等一会儿再开"
        assert fake.wakes == 2

        assert await service.handle(did="d", query="投影静音") is True
        assert fake.keys == ["volumemute"]

        await service.stop()

    asyncio.run(run())


def test_xgimi_token_formats():
    body = bytes.fromhex("001122334455FFFFFF3343524B544D" + "00" * 6)
    assert len(body) == 21

    ibeacon = XgimiDevice("127.0.0.1", body.hex())
    assert ibeacon._kind == "ibeacon"
    assert ibeacon._next_payload() == body

    prefixed = XgimiDevice("127.0.0.1", "4C000215" + body.hex())
    assert prefixed._kind == "ibeacon"
    assert prefixed._next_payload() == body

    android = XgimiDevice(
        "127.0.0.1", (b"\x00" + body[:9] + b"2CRKTM").hex()
    )
    assert android._kind == "android"
    assert android._next_payload()[0] == 1
    assert android._next_payload()[0] == 2

    with tempfile.TemporaryDirectory() as directory:
        state_path = os.path.join(directory, "xgimi_token_state.json")
        first = XgimiDevice(
            "127.0.0.1",
            (b"\x00" + body[:9] + b"2CRKTM").hex(),
            state_path=state_path,
        )
        assert first._next_payload()[0] == 1
        first._save_counter()
        restarted = XgimiDevice(
            "127.0.0.1",
            (b"\x00" + body[:9] + b"2CRKTM").hex(),
            state_path=state_path,
        )
        assert restarted._next_payload()[0] == 2

    remote = XgimiDevice("127.0.0.1", (b"\x00" + body[:9] + b"0CRKTM").hex())
    assert remote._kind == "remote"


if __name__ == "__main__":
    test_registry_order()
    test_router_first_claim_wins()
    test_router_skips_and_survives_exception()
    test_music_is_fallback()
    test_matcher_phrases()
    test_xgimi_service_flow()
    test_xgimi_token_formats()
    print("all service tests passed")
