from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from queue import Queue
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
DEFAULT_RESOURCE_ID = "volc.seedasr.sauc.duration"

PROTOCOL_VERSION = 0b0001
HEADER_SIZE = 0b0001

FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY_REQUEST = 0b0010
FULL_SERVER_RESPONSE = 0b1001
SERVER_ERROR_RESPONSE = 0b1111

NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
LAST_PACKET = 0b0010

JSON_SERIALIZATION = 0b0001
GZIP_COMPRESSION = 0b0001


@dataclass(frozen=True)
class DoubaoASRConfig:
    app_key: str
    access_key: str
    resource_id: str = DEFAULT_RESOURCE_ID
    endpoint: str = DEFAULT_ENDPOINT
    model_name: str = "bigmodel"
    language: str = "zh-CN"
    chunk_ms: int = 100
    enable_punc: bool = True
    enable_itn: bool = True
    enable_ddc: bool = True
    user_id: str = "local-voice-input"

    @classmethod
    def from_env(cls) -> "DoubaoASRConfig":
        return cls(
            app_key=os.environ.get("DOUBAO_ASR_APP_KEY", ""),
            access_key=os.environ.get("DOUBAO_ASR_ACCESS_KEY", ""),
            resource_id=os.environ.get("DOUBAO_ASR_RESOURCE_ID", DEFAULT_RESOURCE_ID),
            endpoint=os.environ.get("DOUBAO_ASR_ENDPOINT", DEFAULT_ENDPOINT),
            model_name=os.environ.get("DOUBAO_ASR_MODEL", "bigmodel"),
            language=os.environ.get("DOUBAO_ASR_LANGUAGE", "zh-CN"),
            chunk_ms=int(os.environ.get("DOUBAO_ASR_CHUNK_MS", "100")),
            enable_punc=_env_bool("DOUBAO_ASR_ENABLE_PUNC", True),
            enable_itn=_env_bool("DOUBAO_ASR_ENABLE_ITN", True),
            enable_ddc=_env_bool("DOUBAO_ASR_ENABLE_DDC", True),
            user_id=os.environ.get("DOUBAO_ASR_USER_ID", "local-voice-input"),
        )


@dataclass(frozen=True)
class ASRResult:
    ok: bool
    text: str = ""
    duration_seconds: float = 0.0
    latency_seconds: float = 0.0
    request_id: str = ""
    error: str = ""
    raw: dict[str, Any] | None = None


class DoubaoASRClient:
    def __init__(self, config: DoubaoASRConfig) -> None:
        self.config = config

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> ASRResult:
        if not self.config.app_key or not self.config.access_key:
            return ASRResult(
                ok=False,
                error=(
                    "missing credentials: set DOUBAO_ASR_APP_KEY and "
                    "DOUBAO_ASR_ACCESS_KEY"
                ),
            )
        if not pcm_bytes:
            return ASRResult(ok=False, error="empty audio")

        return asyncio.run(self._transcribe_pcm(pcm_bytes, sample_rate))

    def transcribe_pcm_live(
        self,
        audio_queue: Queue[bytes | None],
        sample_rate: int = 16000,
        on_text: Callable[[str, bool], None] | None = None,
    ) -> ASRResult:
        if not self.config.app_key or not self.config.access_key:
            return ASRResult(
                ok=False,
                error=(
                    "missing credentials: set DOUBAO_ASR_APP_KEY and "
                    "DOUBAO_ASR_ACCESS_KEY"
                ),
            )
        return asyncio.run(
            self._transcribe_pcm_live(audio_queue, sample_rate, on_text=on_text)
        )

    async def _transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int) -> ASRResult:
        try:
            import websockets
        except ImportError:
            return ASRResult(
                ok=False,
                error="missing dependency: pip install websockets",
            )

        request_id = str(uuid.uuid4())
        headers = {
            "X-Api-App-Key": self.config.app_key,
            "X-Api-Access-Key": self.config.access_key,
            "X-Api-Resource-Id": self.config.resource_id,
            "X-Api-Request-Id": request_id,
            "X-Api-Connect-Id": request_id,
        }
        init_payload = self._build_init_payload(request_id, sample_rate)
        chunk_bytes = max(1, sample_rate * self.config.chunk_ms // 1000) * 2

        started = time.monotonic()
        full_text = ""
        duration_seconds = 0.0
        last_payload: dict[str, Any] | None = None

        try:
            connect_args = {
                "open_timeout": 15,
                "close_timeout": 10,
                "ping_interval": 20,
            }
            try:
                ws_context = websockets.connect(
                    self.config.endpoint,
                    additional_headers=headers,
                    **connect_args,
                )
            except TypeError:
                ws_context = websockets.connect(
                    self.config.endpoint,
                    extra_headers=headers,
                    **connect_args,
                )

            async with ws_context as ws:
                await ws.send(_build_full_client_request(init_payload, sequence=1))
                init_response = _parse_server_response(
                    await asyncio.wait_for(ws.recv(), timeout=10)
                )
                if init_response.get("message_type") == SERVER_ERROR_RESPONSE:
                    return _error_result("init failed", init_response, request_id, started)

                await self._send_audio(ws, pcm_bytes, chunk_bytes)

                async for message in ws:
                    response = _parse_server_response(message)
                    if response.get("message_type") == SERVER_ERROR_RESPONSE:
                        return _error_result(
                            "server error", response, request_id, started
                        )

                    payload = response.get("payload", {})
                    if isinstance(payload, dict):
                        last_payload = payload
                        text = _extract_text(payload)
                        if text:
                            full_text = text
                        duration_ms = _extract_duration_ms(payload)
                        if duration_ms:
                            duration_seconds = duration_ms / 1000.0

                    if response.get("is_last_package"):
                        break
        except Exception as exc:
            LOGGER.exception("Doubao ASR request failed")
            return ASRResult(
                ok=False,
                error=str(exc),
                latency_seconds=time.monotonic() - started,
                request_id=request_id,
            )

        return ASRResult(
            ok=True,
            text=full_text.strip(),
            duration_seconds=duration_seconds,
            latency_seconds=time.monotonic() - started,
            request_id=request_id,
            raw=last_payload,
        )

    async def _transcribe_pcm_live(
        self,
        audio_queue: Queue[bytes | None],
        sample_rate: int,
        on_text: Callable[[str, bool], None] | None = None,
    ) -> ASRResult:
        try:
            import websockets
        except ImportError:
            return ASRResult(
                ok=False,
                error="missing dependency: pip install websockets",
            )

        request_id = str(uuid.uuid4())
        headers = {
            "X-Api-App-Key": self.config.app_key,
            "X-Api-Access-Key": self.config.access_key,
            "X-Api-Resource-Id": self.config.resource_id,
            "X-Api-Request-Id": request_id,
            "X-Api-Connect-Id": request_id,
        }
        init_payload = self._build_init_payload(request_id, sample_rate)

        started = time.monotonic()
        full_text = ""
        duration_seconds = 0.0
        last_payload: dict[str, Any] | None = None

        try:
            connect_args = {
                "open_timeout": 15,
                "close_timeout": 10,
                "ping_interval": 20,
            }
            try:
                ws_context = websockets.connect(
                    self.config.endpoint,
                    additional_headers=headers,
                    **connect_args,
                )
            except TypeError:
                ws_context = websockets.connect(
                    self.config.endpoint,
                    extra_headers=headers,
                    **connect_args,
                )

            async with ws_context as ws:
                await ws.send(_build_full_client_request(init_payload, sequence=1))
                init_response = _parse_server_response(
                    await asyncio.wait_for(ws.recv(), timeout=10)
                )
                if init_response.get("message_type") == SERVER_ERROR_RESPONSE:
                    return _error_result("init failed", init_response, request_id, started)

                send_task = asyncio.create_task(
                    self._send_audio_from_queue(ws, audio_queue)
                )
                while True:
                    message = await asyncio.wait_for(ws.recv(), timeout=30)
                    response = _parse_server_response(message)
                    if response.get("message_type") == SERVER_ERROR_RESPONSE:
                        await send_task
                        return _error_result(
                            "server error", response, request_id, started
                        )

                    payload = response.get("payload", {})
                    if isinstance(payload, dict):
                        last_payload = payload
                        text = _extract_text(payload)
                        is_final = bool(response.get("is_last_package"))
                        if text:
                            full_text = text
                            if on_text is not None:
                                on_text(text, is_final)
                        duration_ms = _extract_duration_ms(payload)
                        if duration_ms:
                            duration_seconds = duration_ms / 1000.0

                    if response.get("is_last_package"):
                        break

                await send_task
        except Exception as exc:
            LOGGER.exception("Doubao live ASR request failed")
            return ASRResult(
                ok=False,
                error=str(exc),
                latency_seconds=time.monotonic() - started,
                request_id=request_id,
            )

        return ASRResult(
            ok=True,
            text=full_text.strip(),
            duration_seconds=duration_seconds,
            latency_seconds=time.monotonic() - started,
            request_id=request_id,
            raw=last_payload,
        )

    async def _send_audio(self, ws: Any, audio: bytes, chunk_bytes: int) -> None:
        offset = 0
        total = len(audio)
        while offset < total:
            end = min(offset + chunk_bytes, total)
            await ws.send(_build_audio_packet(audio[offset:end], is_last=end >= total))
            offset = end

    async def _send_audio_from_queue(
        self, ws: Any, audio_queue: Queue[bytes | None]
    ) -> None:
        pending: bytes | None = None
        while True:
            chunk = await asyncio.to_thread(audio_queue.get)
            if chunk is None:
                await ws.send(_build_audio_packet(pending or b"", is_last=True))
                return
            if pending is not None:
                await ws.send(_build_audio_packet(pending, is_last=False))
            pending = chunk

    def _build_init_payload(self, request_id: str, sample_rate: int) -> dict[str, Any]:
        return {
            "user": {"uid": self.config.user_id},
            "audio": {
                "format": "pcm",
                "codec": "raw",
                "rate": sample_rate,
                "sample_rate": sample_rate,
                "bits": 16,
                "channel": 1,
                "language": self.config.language,
            },
            "request": {
                "reqid": request_id,
                "model_name": self.config.model_name,
                "enable_punc": self.config.enable_punc,
                "enable_itn": self.config.enable_itn,
                "enable_ddc": self.config.enable_ddc,
                "show_utterances": False,
                "result_type": "single",
            },
        }


def _build_header(message_type: int, flags: int) -> bytes:
    return bytes(
        [
            (PROTOCOL_VERSION << 4) | HEADER_SIZE,
            (message_type << 4) | flags,
            (JSON_SERIALIZATION << 4) | GZIP_COMPRESSION,
            0x00,
        ]
    )


def _build_full_client_request(payload: dict[str, Any], sequence: int = 1) -> bytes:
    body = gzip.compress(json.dumps(payload).encode("utf-8"))
    packet = bytearray(_build_header(FULL_CLIENT_REQUEST, POS_SEQUENCE))
    packet.extend(sequence.to_bytes(4, "big", signed=True))
    packet.extend(len(body).to_bytes(4, "big", signed=False))
    packet.extend(body)
    return bytes(packet)


def _build_audio_packet(audio: bytes, is_last: bool = False) -> bytes:
    body = gzip.compress(audio)
    packet = bytearray(
        _build_header(AUDIO_ONLY_REQUEST, LAST_PACKET if is_last else NO_SEQUENCE)
    )
    packet.extend(len(body).to_bytes(4, "big", signed=False))
    packet.extend(body)
    return bytes(packet)


def _parse_server_response(data: bytes | bytearray | str) -> dict[str, Any]:
    if isinstance(data, str):
        return {"message_type": FULL_SERVER_RESPONSE, "payload": json.loads(data)}

    if len(data) < 4:
        raise ValueError("server response too short")

    header_size = data[0] & 0x0F
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    compression = data[2] & 0x0F
    offset = header_size * 4
    payload = bytes(data[offset:])

    parsed: dict[str, Any] = {
        "message_type": message_type,
        "is_last_package": bool(flags & LAST_PACKET),
    }

    if flags & POS_SEQUENCE:
        parsed["sequence"] = int.from_bytes(payload[:4], "big", signed=True)
        payload = payload[4:]

    if message_type == FULL_SERVER_RESPONSE:
        size = int.from_bytes(payload[:4], "big", signed=False)
        body = payload[4 : 4 + size]
        parsed["payload"] = _decode_payload(body, compression)
        return parsed

    if message_type == SERVER_ERROR_RESPONSE:
        parsed["error_code"] = int.from_bytes(payload[:4], "big", signed=False)
        size = int.from_bytes(payload[4:8], "big", signed=False)
        body = payload[8 : 8 + size]
        parsed["error_msg"] = _decode_payload(body, compression)
        return parsed

    parsed["payload_bytes"] = payload
    return parsed


def _decode_payload(payload: bytes, compression: int) -> Any:
    if compression == GZIP_COMPRESSION:
        payload = gzip.decompress(payload)
    text = payload.decode("utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _extract_text(payload: dict[str, Any]) -> str:
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("text"), str):
        return result["text"]

    payload_msg = payload.get("payload_msg")
    if isinstance(payload_msg, dict):
        result = payload_msg.get("result")
        if isinstance(result, dict) and isinstance(result.get("text"), str):
            return result["text"]

    text = payload.get("text")
    return text if isinstance(text, str) else ""


def _extract_duration_ms(payload: dict[str, Any]) -> int:
    audio_info = payload.get("audio_info")
    if isinstance(audio_info, dict):
        try:
            return int(audio_info.get("duration") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _error_result(
    prefix: str, response: dict[str, Any], request_id: str, started: float
) -> ASRResult:
    message = response.get("error_msg")
    if isinstance(message, dict):
        message = json.dumps(message, ensure_ascii=False)
    elif not isinstance(message, str):
        message = repr(response)
    return ASRResult(
        ok=False,
        error=f"{prefix}: {message}",
        latency_seconds=time.monotonic() - started,
        request_id=request_id,
        raw=response,
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
