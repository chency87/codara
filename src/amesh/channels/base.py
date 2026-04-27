from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class ChannelEvent:
    channel: str
    external_user_id: str
    external_chat_id: str
    conversation_key: str
    external_thread_id: str | None
    text: str


class ChannelMessenger(Protocol):
    def send_text(self, chat_id: str, text: str, *, thread_id: str | None = None) -> None: ...
