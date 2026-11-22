"""Notification payloads and delivery adapters."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Notification:
    title: str
    markdown: str
    summary: str = ""
    tags: tuple[str, ...] = ("curve",)


class Notifier(Protocol):
    def send(self, message: Notification) -> None:
        """Deliver a notification or raise NotificationError."""


class NotificationError(RuntimeError):
    """A delivery failure that is safe to show in a monitoring session."""


class ServerChan:
    def __init__(self, sendkey: str) -> None:
        app_key = re.fullmatch(r"sctp([0-9]+)t[A-Za-z0-9_-]+", sendkey)
        self._app = app_key is not None
        if app_key:
            self._url = f"https://{app_key[1]}.push.ft07.com/send/{sendkey}.send"
        elif re.fullmatch(r"SCT[A-Za-z0-9_-]+", sendkey):
            self._url = f"https://sctapi.ftqq.com/{sendkey}.send"
        else:
            raise ValueError("SERVERCHAN_SENDKEY must be a Server 酱³ or Turbo SendKey")

    def send(self, message: Notification) -> None:
        fields = {"title": message.title, "desp": message.markdown}
        if message.summary:
            fields["short"] = message.summary
        if message.tags:
            tags = "|".join(message.tags)
            if self._app:
                fields["tags"] = tags
            else:
                fields["title"] = f"[{tags}] {message.title}"
        request = Request(
            self._url,
            data=json.dumps(fields, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urlopen(request, timeout=10) as response:
                result = json.load(response)
        except HTTPError as error:
            error.close()
            raise NotificationError(f"ServerChan HTTP {error.code}") from None
        except (OSError, ValueError):
            # The request URL contains the SendKey and may appear in library errors.
            raise NotificationError("ServerChan connection timed out, failed, or returned invalid JSON") from None
        code = result.get("code") if isinstance(result, dict) else None
        if type(code) is not int:
            raise NotificationError("ServerChan response must contain an integer code")
        if code != 0:
            raise NotificationError(f"ServerChan rejected the message (code {code})")


def notifier_from_env(env: Mapping[str, str] | None = None) -> Notifier:
    """Read the provider and credentials at startup, or from an explicit mapping."""
    env = os.environ if env is None else env
    if env.get("CURVE_IT_NOTIFY_PROVIDER", "serverchan").strip().lower() != "serverchan":
        raise ValueError("CURVE_IT_NOTIFY_PROVIDER must be serverchan")
    sendkey = env.get("SERVERCHAN_SENDKEY", "").strip()
    if not sendkey:
        raise ValueError("SERVERCHAN_SENDKEY is required to enable ServerChan notifications")
    return ServerChan(sendkey)
