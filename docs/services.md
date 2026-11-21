# 服务化架构：一套轮询，多路服务

改造目标：**语音监控和分发只做一份**，音乐、投影仪、以后加的任何功能都是"服务"，
按关键词认领，不再各自轮询。小米的对话记录接口只被一个循环拉取，不会因为多装功能
导致重复登录、重复请求、被限流。

## 分层

```
xiaohub/
├── voice/                 # 第一部分：语音监控与分发
│   ├── poller.py          #   ConversationPoller：全项目唯一的轮询
│   └── router.py          #   VoiceRouter：把一句话交给第一个认领的服务
├── services/              # 第二部分：能力
│   ├── base.py            #   Service 接口（name / priority / fallback / handle）
│   ├── registry.py        #   ServiceRegistry：排序、启动、停止
│   ├── music.py           #   MusicService：现有音乐功能，兜底
│   └── xgimi/             #   XgimiService：投影仪开关机
│       ├── service.py     #     关键词 → 动作、TTS、冷却
│       ├── device.py      #     BLE 唤醒 / UDP 关机 / TCP 状态
│       ├── ble.py         #     BlueZ 广播（dbus-fast，延迟导入）
│       └── matcher.py     #     口语模式匹配
└── xiaohub.py           # 组装：poller → router → services
```

数据流：

```
小爱 → 小米云 ASR → ConversationPoller（每秒一次，只有它）
                         │
                    VoiceRouter
                         ├─ XgimiService  「打开投影」→ BLE 广播 / UDP 关机
                         ├─ （新增服务）
                         └─ MusicService   其余全部按原口令表处理
```

## 注册顺序规则

`ServiceRegistry` 按 `(fallback, priority, name)` 排序：

- `fallback=True` 的服务永远排最后，只有前面没人认领才轮到它；
- 其余服务按 `priority` 升序，数字小的先试。

音乐服务的 `priority=1000` 且 `fallback=True`，所以它的行为与改造前完全一致：
所有没有服务认领的句子仍然交给 `CommandHandler`。

## 配置

配置在 `conf/setting.json`（也就是网页后台保存的那份）里，新增 `services` 段：

```json
{
  "services": {
    "xgimi": {
      "enabled": true,
      "ip": "192.168.31.50",
      "token": "001122334455FFFFFF3243524B544D",
      "adapter": "hci0",
      "alive_ports": "554,16735,16750,80,8080,443",
      "adv_rounds": 2,
      "incremental_token": true,
      "off_cooldown": 30,
      "on": ["打开投影", "开投影"],
      "off": ["关闭投影", "关投影"],
      "actions": {"投影静音": "volumemute", "投影对焦": "autofocus"}
    }
  }
}
```

``actions`` 是"关键词 → UDP 键名"，可选键见 `services/xgimi/device.py` 的 `KEY_CODES`
（音量、静音、对焦、方向键、返回、主页、菜单等），加功能不用改代码。

改完保存会走 `update_config_from_setting()` → `reload_services()`，只重建注册表，
**不会**重启轮询、不会重新登录。`enabled: false` 或删掉整段即关闭该服务。

`token` 是开机广播里的数据：遥控器/安卓 App 是 16 字节（32 位 hex），
iPhone 极米 App 是 21 字节 iBeacon body（42 位 hex）。两种都支持，直接在网页
`/static/xgimi.html` 里点「扫描开机包」抓取。抓的时候要把投影仪电源拔掉
（待机状态不会广播），连续短按遥控器电源键 20 次以上；遥控器抓不到时，
用手机极米 App 点一次开机，也能抓到手机发出的包。抓完再插回电源。

安卓 token 的首字节是递增序号，抓到的原始包（`00` 开头）已经用过了，
直接重放会失败；xiaohub 每次开机前会自动加一，并把计数器保存在
`conf/xgimi_token_state.json`，容器重启也不会回到旧序号。填手机抓到的
原始 `00` 包即可，第一次会自动发 `01`。

## 新增一个服务（不用碰轮询）

```python
# xiaohub/services/curtain/service.py
from xiaohub.services.base import Service

class CurtainService(Service):
    name = "curtain"
    priority = 20          # 比音乐早，比别的投影/幕布服务按需排

    def __init__(self, config, log, app=None, options=None):
        super().__init__(config, log, app)
        self.options = options or {}

    async def handle(self, did="", query="", ctrl_panel=False, **kwargs):
        if "幕布" not in query:
            return False            # 不认领，交给下一个服务
        # ... 做你的事
        return True                 # 认领，后面的服务不再看到这句话
```

然后在 `xiaohub/services/__init__.py` 的 `_register_builtin()` 里加三行：

```python
if options.get("curtain"):
    from xiaohub.services.curtain.service import CurtainService
    registry.register(CurtainService(config, log, app, options["curtain"]))
```

配置里加 `services.curtain` 即可。**不要**新建 `ConversationPoller`、不要另起
`aiohttp` 轮询、不要自己登录小米账号——这些都由 `voice/` 层统一提供。

## 设计约束（写新服务时请遵守）

1. `handle()` 返回 `True` 表示"这句话归我"，返回 `False` 表示"我不认识"；
   抛异常会被 router 捕获并继续尝试下一个服务，不会拖垮整个语音链路。
2. 关键词要足够具体。音乐的口令表里有「关机」「关闭」，投影服务默认用
   「关闭投影」「关投影」，避免和音乐抢。
3. 耗时动作放后台任务（例如投影开机后等它上线的 `_watch_ready`），
   `handle()` 本身要尽快返回，否则会阻塞同一轮的其他消息处理。
4. 硬件依赖（dbus-fast、BlueZ）要延迟导入，缺失时只让该服务不可用，
   不能让整个进程起不来。
