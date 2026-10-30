from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import pytest

from pipecat_respeecher import RespeecherTTSService


@pytest.mark.parametrize("language", ["en", "ua", "sv", "de", "pt-br", "ar"])
async def test_public_models_authenticate_without_credentials_in_url(language):
    api_key = "test-key-with-special-characters&?=+#"
    service = RespeecherTTSService(
        api_key=api_key,
        url="wss://api.respeecher.com/v1/",
        settings=RespeecherTTSService.Settings(
            model=f"/public/tts/{language}-rt/",
            voice="test-voice",
        ),
    )

    with (
        patch(
            "pipecat_respeecher.tts.websocket_connect",
            new_callable=AsyncMock,
        ) as connect,
        patch.object(service, "_call_event_handler", new_callable=AsyncMock),
        patch.object(service, "push_error", new_callable=AsyncMock) as error,
    ):
        await service._connect_websocket()

    error.assert_not_awaited()
    connect.assert_awaited_once()
    url = connect.call_args.args[0]
    assert url == f"wss://api.respeecher.com/v1/public/tts/{language}-rt/tts/websocket"
    assert urlsplit(url).query == ""
    assert api_key not in url
    assert connect.call_args.kwargs["additional_headers"] == {"X-API-Key": api_key}
    assert service._websocket is connect.return_value
