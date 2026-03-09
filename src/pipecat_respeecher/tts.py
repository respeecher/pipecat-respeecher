#
# Copyright (c) 2025, Daily
# Copyright (c) 2025, Respeecher
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Respeecher real-time text-to-speech service implementation."""

import base64
import json
import uuid
from typing import AsyncGenerator, Optional

from loguru import logger
from pydantic import BaseModel, TypeAdapter, ValidationError

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    LLMFullResponseEndFrame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.tts_service import (
    AudioContextTTSService,
    TTSService,
    TextAggregationMode,
)
from pipecat.services.settings import TTSSettings
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


class RespeecherTTSService(AudioContextTTSService, TTSService):
    """Respeecher real-time TTS service with WebSocket streaming and audio contexts.

    Provides text-to-speech using Respeecher's streaming WebSocket API.
    Supports audio context management and voice customization via sampling parameters.
    """

    class InputParams(BaseModel):
        """Input parameters for Respeecher TTS configuration.

        Parameters:
            sampling_params: Sampling parameters used for speech synthesis.
        """

        sampling_params: SamplingParams = {}

    def __init__(
        self,
        *,
        api_key: str,
        voice_id: str,
        model: str = "public/tts/en-rt",
        url: str = "wss://api.respeecher.com/v1",
        sample_rate: Optional[int] = None,
        params: Optional[InputParams] = None,
        **kwargs,
    ):
        """Initialize the Respeecher TTS service.

        Args:
            api_key: Respeecher API key for authentication.
            voice_id: ID of the voice to use for synthesis.
            model: Model path for the Respeecher TTS API.
            url: WebSocket base URL for Respeecher TTS API.
            sample_rate: Audio sample rate. If None, uses default.
            params: Additional input parameters for voice customization.
            **kwargs: Additional arguments passed to TTSService.
        """
        AudioContextTTSService.__init__(self, reconnect_on_error=False)
        TTSService.__init__(
            self,
            pause_frame_processing=True,
            text_aggregation_mode=TextAggregationMode.TOKEN,
            sample_rate=sample_rate,
            settings=TTSSettings(model=model, voice=voice_id),
            **kwargs,
        )

        params = params or RespeecherTTSService.InputParams()

        self._api_key = api_key
        self._url = url
        self._output_format: StreamingOutputFormatParams = {
            "encoding": "pcm_s16le",
            "sample_rate": sample_rate or 0,
        }
        self._respeecher_settings = {"sampling_params": params.sampling_params}

        self._context_id: str | None = None
        self._receive_task = None

    def can_generate_metrics(self) -> bool:
        """Check if this service can generate processing metrics.

        Returns:
            True
        """
        return True

    async def start_processing_metrics(self) -> None:
        # Processing metrics are almost meaningless in our case since run_tts
        # is duplex and we don't do any text preprocessing by default.
        pass

    async def stop_processing_metrics(self) -> None:
        pass

    async def _update_settings(self, delta):
        changed = await super()._update_settings(delta)
        if "model" in changed:
            logger.info(f"Switching TTS model to: [{self._settings.model}]")
            await self._disconnect()
            await self._connect()
        return changed

    def _build_request(self, text: Optional[str] = None):
        assert self._context_id is not None

        request: ContextfulGenerationRequestParams = {
            "transcript": text or "",
            "continue": text is not None,
            "context_id": self._context_id,
            "voice": {
                "id": self._settings.voice,
                "sampling_params": self._respeecher_settings["sampling_params"],
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
                close_timeout=2,
            )

            await self._call_event_handler("on_connected")
        except Exception as e:
            logger.error(f"{self} initialization error: {e}")
            self._context_id = None
            self._websocket = None
            await self._call_event_handler("on_connection_error", f"{e}")

    async def _disconnect_websocket(self):
        try:
            await self.stop_all_metrics()

            if self._websocket:
                logger.debug("Disconnecting from Respeecher")
                await self._websocket.close()
        except Exception as e:
            logger.error(f"{self} error closing websocket: {e}")
        finally:
            self._context_id = None
            self._websocket = None
            await self._call_event_handler("on_disconnected")

    def _get_websocket(self):
        if self._websocket:
            return self._websocket
        raise Exception("Websocket not connected")

    async def on_audio_context_interrupted(self, context_id: str):
        await self.stop_all_metrics()

        if context_id:
            cancel_request = json.dumps(
                {"context_id": context_id, "cancel": True}
            )
            try:
                await self._get_websocket().send(cancel_request)
            except Exception as e:
                logger.warning(f"{self} error sending cancel: {e}")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames with context awareness.

        Args:
            frame: The frame to process.
            direction: The direction of frame processing.
        """
        await super().process_frame(frame, direction)

        if isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
            await self.flush_audio()

    async def flush_audio(self):
        """Flush any pending audio and finalize the current context."""
        if not self._context_id or not self._websocket:
            return
        logger.trace(f"{self}: flushing audio")
        flush_request = self._build_request()
        await self._websocket.send(flush_request)
        self._context_id = None

    async def _receive_messages(self):
        async for message in self._get_websocket():
            try:
                response = TypeAdapter(TTSResponse).validate_json(message)
            except ValidationError as e:
                logger.error(f"{self} cannot parse message: {e}")
                continue

            if response.context_id is not None and not self.audio_context_available(
                response.context_id
            ):
                # We don't need to log an error, getting here is expected
                # and is how interruptions are handled in the superclass
                continue

            if response.type == "error":
                logger.error(f"{self} error: {response}")
                await self.push_frame(TTSStoppedFrame())
                await self.stop_all_metrics()
                await self.push_error(f"{self} error: {response.error}")
                continue

            if response.type == "done":
                await self.push_frame(TTSStoppedFrame())
                await self.stop_ttfb_metrics()
                await self.remove_audio_context(response.context_id)
            elif response.type == "chunk":
                await self.stop_ttfb_metrics()
                frame = TTSAudioRawFrame(
                    audio=base64.b64decode(response.data),
                    sample_rate=self.sample_rate,
                    num_channels=1,
                )
                await self.append_to_audio_context(response.context_id, frame)

    @traced_tts
    async def run_tts(self, text: str, context_id: str = "") -> AsyncGenerator[Frame | None, None]:
        """Generate speech from text using Respeecher's streaming API.

        Args:
            text: The text to synthesize into speech.

        Yields:
            Frame: Audio frames containing the synthesized speech.
        """
        logger.trace(f"{self}: Generating TTS [{text}]")

        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()

            if not self._context_id:
                await self.start_ttfb_metrics()
                yield TTSStartedFrame()
                self._context_id = str(uuid.uuid4())
                await self.create_audio_context(self._context_id)

            generation_request = self._build_request(text)

            try:
                await self._get_websocket().send(generation_request)
                await self.start_tts_usage_metrics(text)
            except Exception as e:
                yield ErrorFrame(error=f"{self} error sending message: {e}")
                yield TTSStoppedFrame()
                await self._disconnect()
                await self._connect()
                return

            yield None
        except Exception as e:
            yield ErrorFrame(error=f"{self} exception: {e}")
