from __future__ import annotations

import json
import mimetypes
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import get_channel_config
from .runner import UploadedInput, run_agent

_STREAM_ELEMENT_ID = "aperio_streaming_md"


class FeishuGatewayError(RuntimeError):
    pass


@dataclass
class _StreamState:
    card_id: str = ""
    sequence: int = 0
    last_update: float = 0.0
    events: list[str] = field(default_factory=list)


def run_feishu_gateway(*, approval_mode: str = "approve", timeout_seconds: int = 900) -> int:
    config = get_channel_config("feishu")
    gateway = FeishuGateway(config, approval_mode=approval_mode, timeout_seconds=timeout_seconds)
    gateway.start()
    return 0


class FeishuGateway:
    def __init__(self, config: dict[str, Any], *, approval_mode: str, timeout_seconds: int) -> None:
        self.config = _normalize_feishu_config(config)
        self.approval_mode = approval_mode if approval_mode in {"approve", "reject"} else "approve"
        self.timeout_seconds = timeout_seconds
        self._client: Any = None
        self._bot_open_id = ""
        self._seen_message_ids: OrderedDict[str, None] = OrderedDict()
        self._seen_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="aperio-feishu")

    def start(self) -> None:
        if not self.config["enabled"]:
            raise FeishuGatewayError("channels.feishu.enabled is false in ~/.aperio/config.json.")
        if not self.config["app_id"] or not self.config["app_secret"]:
            raise FeishuGatewayError("Feishu appId and appSecret are required in channels.feishu.")

        try:
            import lark_oapi as lark
            from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN
        except ImportError as exc:
            raise FeishuGatewayError('Feishu gateway requires lark-oapi. Install with `pip install "aperio-agent[integrations]"`.') from exc

        domain = LARK_DOMAIN if self.config["domain"] == "lark" else FEISHU_DOMAIN
        self._client = (
            lark.Client.builder()
            .app_id(self.config["app_id"])
            .app_secret(self.config["app_secret"])
            .domain(domain)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
        self._bot_open_id = self._get_bot_open_id()

        dispatcher = (
            lark.EventDispatcherHandler.builder(
                self.config["encrypt_key"],
                self.config["verification_token"],
            )
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .build()
        )
        ws_client = lark.ws.Client(
            self.config["app_id"],
            self.config["app_secret"],
            domain=domain,
            event_handler=dispatcher,
            log_level=lark.LogLevel.INFO,
        )
        print("Aperio Feishu gateway started.")
        print(f"Domain: {self.config['domain']} | groupPolicy: {self.config['group_policy']} | approval: {self.approval_mode}")
        print("Press Ctrl+C to stop.")
        ws_client.start()

    def _on_message_sync(self, data: Any) -> None:
        self._executor.submit(self._handle_message, data)

    def _handle_message(self, data: Any) -> None:
        try:
            event = data.event
            message = event.message
            sender = event.sender
            if getattr(sender, "sender_type", "") == "bot":
                return

            message_id = getattr(message, "message_id", "") or ""
            if not self._mark_seen(message_id):
                return

            sender_id_obj = getattr(sender, "sender_id", None)
            sender_id = getattr(sender_id_obj, "open_id", "") or getattr(sender_id_obj, "user_id", "") or "unknown"
            if not self._sender_allowed(sender_id):
                print(f"Feishu message skipped: sender {sender_id} is not in allowFrom.")
                return

            chat_type = getattr(message, "chat_type", "") or ""
            if chat_type == "group" and not self._group_message_allowed(message):
                return

            content, uploaded_inputs = self._message_payload(message)
            content = content.strip()
            if not content and not uploaded_inputs:
                return

            reaction_id = self._add_reaction(message_id, self.config["react_emoji"])
            attachment_note = ""
            if uploaded_inputs:
                names = ", ".join(item.relative_path for item in uploaded_inputs[:8])
                suffix = "" if len(uploaded_inputs) <= 8 else f", and {len(uploaded_inputs) - 8} more"
                attachment_note = f"\n\nattachments: {names}{suffix}"
            prompt = f"[Feishu]\nsender: {sender_id}\nchatType: {chat_type or 'unknown'}\n\n{content}"
            if attachment_note:
                prompt += attachment_note
            print(f"Feishu message from {sender_id}: {content[:120]}")

            stream_state = self._start_stream_card(message, sender_id) if self.config["streaming"] else None

            def on_event(event: dict[str, Any]) -> None:
                if stream_state:
                    self._stream_event(stream_state, event)

            result = run_agent(
                prompt,
                approval_mode=self.approval_mode,
                timeout_seconds=self.timeout_seconds,
                uploaded_inputs=uploaded_inputs,
                event_callback=on_event,
            )
            if reaction_id:
                self._remove_reaction(message_id, reaction_id)
            answer = result.answer or "运行完成。"
            if not (stream_state and self._finish_stream_card(stream_state, answer)):
                self._send_answer(message, sender_id, answer)
            if self.config["done_emoji"]:
                self._add_reaction(message_id, self.config["done_emoji"])
        except Exception as exc:
            print(f"Feishu gateway error: {exc}")

    def _mark_seen(self, message_id: str) -> bool:
        if not message_id:
            return True
        with self._seen_lock:
            if message_id in self._seen_message_ids:
                return False
            self._seen_message_ids[message_id] = None
            while len(self._seen_message_ids) > 1000:
                self._seen_message_ids.popitem(last=False)
        return True

    def _sender_allowed(self, sender_id: str) -> bool:
        allow_from = self.config["allow_from"]
        return "*" in allow_from or sender_id in allow_from

    def _group_message_allowed(self, message: Any) -> bool:
        if self.config["group_policy"] == "open":
            return True
        raw_content = getattr(message, "content", "") or ""
        if "@_all" in raw_content:
            return True
        for mention in getattr(message, "mentions", None) or []:
            mention_id = getattr(mention, "id", None)
            mention_open_id = getattr(mention_id, "open_id", "") if mention_id else ""
            if mention_open_id and mention_open_id == self._bot_open_id:
                return True
        return False

    def _message_payload(self, message: Any) -> tuple[str, list[UploadedInput]]:
        msg_type = getattr(message, "message_type", "") or ""
        message_id = getattr(message, "message_id", "") or ""
        try:
            content = json.loads(getattr(message, "content", "") or "{}")
        except json.JSONDecodeError:
            content = {}

        if msg_type == "text":
            return _resolve_mentions(str(content.get("text", "")), getattr(message, "mentions", None)), []
        if msg_type == "post":
            text, image_keys = _extract_post_content(content)
            uploads = [
                upload
                for upload in (self._download_image(message_id, image_key) for image_key in image_keys)
                if upload is not None
            ]
            return text, uploads
        if msg_type == "interactive":
            return _extract_interactive_text(content), []
        if msg_type in {"image", "audio", "file", "media"}:
            upload = self._download_media(message_id, msg_type, content)
            if upload:
                return f"[{msg_type}: {upload.relative_path}]", [upload]
            return f"[{msg_type}: download failed]", []
        return str(content.get("text") or content.get("title") or f"[{msg_type}]"), []

    def _send_answer(self, message: Any, sender_id: str, answer: str) -> None:
        receive_id_type, receive_id = self._reply_target(message, sender_id)
        content = json.dumps({"text": _trim(answer, 5900)}, ensure_ascii=False)
        self._send_message(receive_id_type, receive_id, "text", content)

    def _reply_target(self, message: Any, sender_id: str) -> tuple[str, str]:
        chat_type = getattr(message, "chat_type", "") or ""
        chat_id = getattr(message, "chat_id", "") or ""
        receive_id = chat_id if chat_type == "group" and chat_id else sender_id
        receive_id_type = "chat_id" if chat_type == "group" and chat_id else "open_id"
        return receive_id_type, receive_id

    def _send_message(self, receive_id_type: str, receive_id: str, msg_type: str, content: str) -> bool:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )
        response = self._client.im.v1.message.create(request)
        if not response.success():
            print(f"Failed to send Feishu message: code={response.code}, msg={response.msg}, log_id={response.get_log_id()}")
            return False
        return True

    def _start_stream_card(self, message: Any, sender_id: str) -> _StreamState | None:
        receive_id_type, receive_id = self._reply_target(message, sender_id)
        card_id = self._create_stream_card(receive_id_type, receive_id)
        if not card_id:
            return None
        state = _StreamState(card_id=card_id, sequence=0)
        if not self._update_stream_card(state, "**Aperio 正在处理...**\n\n- 已收到飞书消息"):
            return None
        return state

    def _stream_event(self, state: _StreamState, event: dict[str, Any]) -> None:
        message = str(event.get("message") or event.get("phase") or event.get("type") or "").strip()
        if not message:
            return
        state.events.append(message)
        state.events = state.events[-12:]
        now = time.monotonic()
        if now - state.last_update < 0.35:
            return
        lines = "\n".join(f"- {item}" for item in state.events)
        self._update_stream_card(state, f"**Aperio 正在处理...**\n\n{lines}")
        state.last_update = now

    def _finish_stream_card(self, state: _StreamState, answer: str) -> bool:
        if not state.card_id:
            return False
        content = _trim(answer, 12000)
        ok = self._update_stream_card(state, content)
        if ok:
            self._close_stream_card(state)
        return ok

    def _create_stream_card(self, receive_id_type: str, receive_id: str) -> str:
        try:
            from lark_oapi.api.cardkit.v1 import CreateCardRequest, CreateCardRequestBody

            card_json = {
                "schema": "2.0",
                "config": {"wide_screen_mode": True, "update_multi": True, "streaming_mode": True},
                "body": {
                    "elements": [
                        {"tag": "markdown", "content": "", "element_id": _STREAM_ELEMENT_ID}
                    ]
                },
            }
            request = (
                CreateCardRequest.builder()
                .request_body(
                    CreateCardRequestBody.builder()
                    .type("card_json")
                    .data(json.dumps(card_json, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            response = self._client.cardkit.v1.card.create(request)
            if not response.success():
                print(f"Failed to create Feishu streaming card: code={response.code}, msg={response.msg}")
                return ""
            card_id = getattr(response.data, "card_id", "") if response.data else ""
            if not card_id:
                return ""
            if not self._send_message(
                receive_id_type,
                receive_id,
                "interactive",
                json.dumps({"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False),
            ):
                return ""
            return card_id
        except Exception as exc:
            print(f"Feishu streaming card unavailable: {exc}")
            return ""

    def _update_stream_card(self, state: _StreamState, content: str) -> bool:
        try:
            from lark_oapi.api.cardkit.v1 import ContentCardElementRequest, ContentCardElementRequestBody

            state.sequence += 1
            request = (
                ContentCardElementRequest.builder()
                .card_id(state.card_id)
                .element_id(_STREAM_ELEMENT_ID)
                .request_body(
                    ContentCardElementRequestBody.builder()
                    .content(content)
                    .sequence(state.sequence)
                    .build()
                )
                .build()
            )
            response = self._client.cardkit.v1.card_element.content(request)
            if not response.success():
                print(f"Failed to update Feishu streaming card: code={response.code}, msg={response.msg}")
                return False
            return True
        except Exception as exc:
            print(f"Error updating Feishu streaming card: {exc}")
            return False

    def _close_stream_card(self, state: _StreamState) -> bool:
        try:
            from lark_oapi.api.cardkit.v1 import SettingsCardRequest, SettingsCardRequestBody

            state.sequence += 1
            payload = json.dumps({"config": {"streaming_mode": False}}, ensure_ascii=False)
            request = (
                SettingsCardRequest.builder()
                .card_id(state.card_id)
                .request_body(
                    SettingsCardRequestBody.builder()
                    .settings(payload)
                    .sequence(state.sequence)
                    .uuid(str(uuid.uuid4()))
                    .build()
                )
                .build()
            )
            response = self._client.cardkit.v1.card.settings(request)
            if not response.success():
                print(f"Failed to close Feishu streaming card: code={response.code}, msg={response.msg}")
                return False
            return True
        except Exception as exc:
            print(f"Error closing Feishu streaming card: {exc}")
            return False

    def _download_image(self, message_id: str, image_key: str) -> UploadedInput | None:
        if not message_id or not image_key:
            return None
        return self._download_resource(
            message_id=message_id,
            file_key=image_key,
            resource_type="image",
            fallback_filename=f"{image_key[:16]}.jpg",
        )

    def _download_media(self, message_id: str, msg_type: str, content: dict[str, Any]) -> UploadedInput | None:
        if msg_type == "image":
            return self._download_image(message_id, str(content.get("image_key") or ""))
        file_key = str(content.get("file_key") or "")
        if not message_id or not file_key:
            return None
        fallback = file_key[:16] or msg_type
        if msg_type == "audio" and not Path(fallback).suffix:
            fallback = f"{fallback}.ogg"
        return self._download_resource(
            message_id=message_id,
            file_key=file_key,
            resource_type="file",
            fallback_filename=fallback,
        )

    def _download_resource(self, *, message_id: str, file_key: str, resource_type: str, fallback_filename: str) -> UploadedInput | None:
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type(resource_type)
                .build()
            )
            response = self._client.im.v1.message_resource.get(request)
            if not response.success():
                print(f"Failed to download Feishu {resource_type}: code={response.code}, msg={response.msg}")
                return None
            data = response.file
            if hasattr(data, "read"):
                data = data.read()
            if not isinstance(data, bytes):
                return None
            if len(data) > self.config["max_media_bytes"]:
                print(f"Skipped Feishu {resource_type}: file exceeds maxMediaBytes ({len(data)} bytes).")
                return None
            filename = _safe_filename(getattr(response, "file_name", "") or fallback_filename)
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            return UploadedInput(
                filename=filename,
                relative_path=f"feishu/{filename}",
                content_type=content_type,
                content=data,
            )
        except Exception as exc:
            print(f"Error downloading Feishu {resource_type}: {exc}")
            return None

    def _add_reaction(self, message_id: str, emoji_type: str) -> str:
        if not message_id or not emoji_type:
            return ""
        try:
            from lark_oapi.api.im.v1 import CreateMessageReactionRequest, CreateMessageReactionRequestBody, Emoji

            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.message_reaction.create(request)
            return getattr(response.data, "reaction_id", "") if response.success() and response.data else ""
        except Exception:
            return ""

    def _remove_reaction(self, message_id: str, reaction_id: str) -> None:
        if not message_id or not reaction_id:
            return
        try:
            from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

            request = DeleteMessageReactionRequest.builder().message_id(message_id).reaction_id(reaction_id).build()
            self._client.im.v1.message_reaction.delete(request)
        except Exception:
            return

    def _get_bot_open_id(self) -> str:
        try:
            import lark_oapi as lark

            request = (
                lark.BaseRequest.builder()
                .http_method(lark.HttpMethod.GET)
                .uri("/open-apis/bot/v3/info")
                .token_types({lark.AccessTokenType.APP})
                .build()
            )
            response = self._client.request(request)
            if not response.success():
                return ""
            data = json.loads(response.raw.content)
            bot = (data.get("data") or {}).get("bot") or data.get("bot") or {}
            return str(bot.get("open_id") or "")
        except Exception:
            return ""


def _normalize_feishu_config(config: dict[str, Any]) -> dict[str, Any]:
    group_policy = (_cfg(config, "groupPolicy", "group_policy") or "mention").lower()
    if group_policy not in {"open", "mention"}:
        group_policy = "mention"
    domain = (_cfg(config, "domain") or "feishu").lower()
    if domain not in {"feishu", "lark"}:
        domain = "feishu"
    return {
        "enabled": bool(config.get("enabled")),
        "app_id": _cfg(config, "appId", "app_id"),
        "app_secret": _cfg(config, "appSecret", "app_secret"),
        "encrypt_key": _cfg(config, "encryptKey", "encrypt_key"),
        "verification_token": _cfg(config, "verificationToken", "verification_token"),
        "allow_from": list(config.get("allowFrom") or config.get("allow_from") or []),
        "react_emoji": _cfg(config, "reactEmoji", "react_emoji") or "THUMBSUP",
        "done_emoji": _cfg(config, "doneEmoji", "done_emoji"),
        "group_policy": group_policy,
        "streaming": bool(config.get("streaming", True)),
        "domain": domain,
        "max_media_bytes": _positive_int(config.get("maxMediaBytes") or config.get("max_media_bytes"), 25 * 1024 * 1024),
    }


def _cfg(config: dict[str, Any], *names: str) -> str:
    for name in names:
        value = config.get(name)
        if isinstance(value, str):
            return value.strip()
    return ""


def _resolve_mentions(text: str, mentions: Any) -> str:
    for mention in mentions or []:
        key = getattr(mention, "key", "") or ""
        if not key:
            continue
        name = getattr(mention, "name", "") or key
        text = text.replace(key, f"@{name}")
    return text


def _extract_post_content(content: dict[str, Any]) -> tuple[str, list[str]]:
    root = content.get("post") if isinstance(content.get("post"), dict) else content
    if not isinstance(root, dict):
        return "", []
    for key in ("zh_cn", "en_us", "ja_jp"):
        if isinstance(root.get(key), dict):
            root = root[key]
            break
    parts: list[str] = []
    image_keys: list[str] = []
    title = root.get("title")
    if isinstance(title, str) and title:
        parts.append(title)
    for row in root.get("content", []) if isinstance(root.get("content"), list) else []:
        for item in row if isinstance(row, list) else []:
            if not isinstance(item, dict):
                continue
            tag = item.get("tag")
            if tag in {"text", "a"}:
                parts.append(str(item.get("text", "")))
            elif tag == "at":
                parts.append(f"@{item.get('user_name', 'user')}")
            elif tag == "code_block":
                parts.append(str(item.get("text", "")))
            elif tag == "img":
                image_key = str(item.get("image_key") or "")
                if image_key:
                    image_keys.append(image_key)
                parts.append("[image]")
    return " ".join(part for part in parts if part).strip(), image_keys


def _extract_interactive_text(content: dict[str, Any]) -> str:
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        text = value.get("content") or value.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
        if isinstance(text, dict):
            walk(text)
        for key in ("elements", "columns", "fields", "card", "header", "title"):
            walk(value.get(key))

    walk(content)
    return "\n".join(parts).strip()


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 40].rstrip() + "\n\n[内容过长，已截断]"


def _safe_filename(filename: str) -> str:
    name = Path(filename or "feishu-upload").name.strip().replace("\x00", "")
    return name or "feishu-upload"


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
