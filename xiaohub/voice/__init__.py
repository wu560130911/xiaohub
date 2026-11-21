"""语音层：一套轮询 + 一个分发器。

- ``poller``：从唯一一条小米对话记录流里取新消息（全项目只有这一处轮询）；
- ``router``：把消息交给已注册的服务，谁认领谁处理。

新增功能时只写服务，不要再起第二个轮询。
"""

from xiaohub.voice.poller import ConversationPoller
from xiaohub.voice.router import VoiceRouter

__all__ = ["ConversationPoller", "VoiceRouter"]
