#
# Copyright (c) 2025, Daily
# Copyright (c) 2025, Respeecher
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Respeecher real-time text-to-speech service implementation."""

import base64
import json
from typing import AsyncGenerator, Optional
from dataclasses import dataclass, field

from loguru import logger
from pydantic import TypeAdapter, ValidationError

from pipecat.processors.frame_processor import FrameDirection
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
    LLMFullResponseEndFrame,
)
from pipecat.services.tts_service import (
    WebsocketTTSService,
    TextAggregationMode,
)
from pipecat.services.settings import NOT_GIVEN, TTSSettings, _NotGiven
from pipecat.utils.tracing.service_decorators import traced_tts

from respeecher.tts import (
    ContextfulGenerationRequestParams,
    StreamingOutputFormatParams,
)
from respeecher.tts import Response as TTSResponse
from respeecher.voices import (
    SamplingParamsParams as SamplingParams,  # TypedDict instead of a Pydantic model
)
from websockets.asyncio.client import connect as websocket_connect
from websockets.protocol import State


@dataclass
class RespeecherTTSSettings(TTSSettings):
    """Settings for RespeecherTTSService.

    Parameters:
        sampling_params: Sampling parameters used for speech synthesis.
    """

    sampling_params: SamplingParams | _NotGiven = field(
        default_factory=lambda: NOT_GIVEN
    )


class RespeecherTTSService(WebsocketTTSService):
    """Respeecher real-time TTS service with WebSocket streaming and audio contexts.

    Provides text-to-speech using Respeecher's streaming WebSocket API.
    Supports audio context management and voice customization via sampling parameters.
    """

    Settings = RespeecherTTSSettings
    _settings: Settings

    def __init__(
        self,
        *,
        api_key: str,
        settings: Settings,
        url: str = "wss://api.respeecher.com/v1",
        sample_rate: int | None = None,
        **kwargs,
    ):
        """Initialize the Respeecher TTS service.

        Args:
            api_key: Respeecher API key for authentication.
            settings: Respeecher TTS settings (model, voice, sampling params)
            url: WebSocket base URL for Respeecher TTS API.
            sample_rate: Audio sample rate. If None, uses default.
            **kwargs: Additional arguments passed to WebsocketTTSService.
        """
        merged_settings = self.Settings(
            model="public/tts/en-rt",
            sampling_params={},
            language=None,
        )
        merged_settings.apply_update(settings)

        if merged_settings.model is None:
            raise ValueError("Respeecher TTS requires a model path")

        if merged_settings.voice is None:
            raise ValueError("Respeecher TTS requires a voice ID")

        super().__init__(
            push_start_frame=True,
            sample_rate=sample_rate,
            settings=merged_settings,
            text_aggregation_mode=TextAggregationMode.TOKEN,
            stop_frame_timeout_s=10,
            **kwargs,
        )

        self._api_key = api_key
        self._url = url
        self._output_format: StreamingOutputFormatParams = {
            "encoding": "pcm_s16le",
            "sample_rate": sample_rate or 0,
        }

        self._receive_task = None

    def can_generate_metrics(self) -> bool:
        """Check if this service can generate processing metrics.

        Returns:
            True
        """
        return True

    """
    async def start_processing_metrics(self) -> None:
        # Processing metrics are almost meaningless in our case since run_tts
        # is duplex and we don't do any text preprocessing by default.
        pass

    async def stop_processing_metrics(self) -> None:
        pass
    """

    async def _update_settings(self, delta):
        if delta.model is None or delta.voice is None:
            logger.warning("Respeecher TTS requires model and voice, skipping update")
            return

        changed = await super()._update_settings(delta)

        if "model" in changed:
            await self._disconnect()
            await self._connect()
        elif "voice" in changed and self._turn_context_id:
            if self.audio_context_available(self._turn_context_id):
                await self.flush_audio(context_id=self._turn_context_id)

            self._turn_context_id = None
            self._turn_context_id = self.create_context_id()

        return changed

    def _build_request(self, text: Optional[str] = None, *, context_id: str):
        request: ContextfulGenerationRequestParams = {
            "transcript": text or "",
            "continue": text is not None,
            "context_id": context_id,
            "voice": {
                "id": self._settings.voice,
                "sampling_params": self._settings.sampling_params,
            },
            "output_format": self._output_format,
        }

        return json.dumps(request)

    async def start(self, frame: StartFrame):
        """Start the Respeecher TTS service.

        Args:
            frame: The start frame containing initialization parameters.
        """
        await super().start(frame)
        self._output_format["sample_rate"] = self.sample_rate
        await self._connect()

    async def stop(self, frame: EndFrame):
        """Stop the Respeecher TTS service.

        Args:
            frame: The end frame.
        """
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        """Cancel the Respeecher TTS service.

        Args:
            frame: The cancel frame.
        """
        await super().cancel(frame)
        await self._disconnect()

    async def _connect(self):
        await super()._connect()
        await self._connect_websocket()

        if self._websocket and not self._receive_task:
            self._receive_task = self.create_task(
                self._receive_task_handler(self._report_error)
            )

    async def _disconnect(self):
        await super()._disconnect()

        if self._receive_task:
            await self.cancel_task(self._receive_task)
            self._receive_task = None

        await self._disconnect_websocket()

    async def _connect_websocket(self):
        try:
            if self._websocket and self._websocket.state is State.OPEN:
                return
            logger.debug("Connecting to Respeecher")

            url = self._url.rstrip("/")
            model_name = self._settings.model.strip("/")

            if model_name:
                url += f"/{model_name}"

            url += f"/tts/websocket?api_key={self._api_key}"

            self._websocket = await websocket_connect(
                url,
                compression=None,
                ping_interval=2.5,
                ping_timeout=2.5,
                close_timeout=3,
                open_timeout=3,
            )

            await self._call_event_handler("on_connected")
        except Exception as e:
            await self.push_error(
                error_msg=f"Respeecher TTS initialization error: {e}", exception=e
            )
            self._websocket = None
            await self._call_event_handler("on_connection_error", f"{e}")

    async def _disconnect_websocket(self):
        try:
            await self.stop_all_metrics()

            if self._websocket:
                logger.debug("Disconnecting from Respeecher")
                await self._websocket.close()
        except Exception as e:
            await self.push_error(
                error_msg=f"Respeecher TTS closing error: {e}", exception=e
            )
        finally:
            await self.remove_active_audio_context()
            self._websocket = None
            await self._call_event_handler("on_disconnected")

    def _get_websocket(self):
        if self._websocket:
            return self._websocket
        raise Exception("Websocket not connected")

    async def on_audio_context_interrupted(self, context_id: str):
        await self.stop_all_metrics()

        if context_id:
            cancel_request = json.dumps({"context_id": context_id, "cancel": True})
            try:
                await self._get_websocket().send(cancel_request)
            except Exception as e:
                logger.debug(f"Cannot cancel Respeecher context: {e}")

        await super().on_audio_context_interrupted(context_id)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames with context awareness.

        Args:
            frame: The frame to process.
            direction: The direction of frame processing.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
            await self.flush_audio()

    async def flush_audio(self, context_id: str | None = None):
        """Flush any pending audio and finalize the current context."""
        if not context_id or not self._websocket:
            return

        flush_request = self._build_request(context_id=context_id)
        await self._websocket.send(flush_request)

    async def _process_messages(self):
        async for message in self._get_websocket():
            try:
                response = TypeAdapter(TTSResponse).validate_json(message)
            except ValidationError as e:
                await self.push_error(
                    error_msg=f"Invalid Respeecher TTS message: {e}", exception=e
                )
                continue

            if response.context_id is not None and not self.audio_context_available(
                response.context_id
            ):
                # We don't need to log an error, getting here is expected
                # and is how interruptions are handled in the superclass
                continue

            if response.type == "error":
                await self.stop_all_metrics()
                await self.append_to_audio_context(
                    response.context_id, TTSStoppedFrame(context_id=response.context_id)
                )
                await self.remove_audio_context(response.context_id)
                await self.push_error(
                    error_msg=f"Respeecher TTS error: {response.error}"
                )
                continue

            if response.type == "done":
                await self.stop_ttfb_metrics()
                await self.append_to_audio_context(
                    response.context_id, TTSStoppedFrame(context_id=response.context_id)
                )
                await self.remove_audio_context(response.context_id)
            elif response.type == "chunk":
                await self.stop_ttfb_metrics()
                frame = TTSAudioRawFrame(
                    audio=base64.b64decode(response.data),
                    sample_rate=self.sample_rate,
                    num_channels=1,
                    context_id=response.context_id,
                )
                await self.append_to_audio_context(response.context_id, frame)

    async def _receive_messages(self):
        while True:
            await self._process_messages()
            await self._connect_websocket()

    @traced_tts
    async def run_tts(
        self, text: str, context_id: str
    ) -> AsyncGenerator[Frame | None, None]:
        """Generate speech from text using Respeecher's streaming API.

        Args:
            text: The text to synthesize into speech.
            context_id: The context ID for tracking audio frames.

        Yields:
            Frame: Audio frames containing the synthesized speech.
        """
        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()

            generation_request = self._build_request(text, context_id=context_id)

            try:
                await self._get_websocket().send(generation_request)
                await self.start_tts_usage_metrics(text)
            except Exception as e:
                yield ErrorFrame(error=f"Respeecher TTS error: {e}")
                yield TTSStoppedFrame(context_id=context_id)
                await self._disconnect()
                await self._connect()
                return

            yield None
        except Exception as e:
            yield ErrorFrame(error=f"Respeecher TTS error: {e}")
