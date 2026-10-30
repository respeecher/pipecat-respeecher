import json

import pytest

from pipecat_respeecher import RespeecherTTSService


def test_constructs_with_defaults_and_builds_request():
    service = RespeecherTTSService(
        api_key="test-key",
        settings=RespeecherTTSService.Settings(voice="test-voice"),
    )

    assert service._settings.model == "public/tts/en-rt"
    assert service._settings.voice == "test-voice"
    assert service._settings.sampling_params == {}

    request = json.loads(service._build_request("Hello", context_id="ctx"))

    assert request["transcript"] == "Hello"
    assert request["continue"] is True
    assert request["context_id"] == "ctx"
    assert request["voice"] == {"id": "test-voice", "sampling_params": {}}
    assert request["output_format"]["encoding"] == "pcm_s16le"


def test_sampling_params_override_defaults():
    service = RespeecherTTSService(
        api_key="test-key",
        settings=RespeecherTTSService.Settings(
            model="public/tts/ua-rt",
            voice="test-voice",
            sampling_params={"temperature": 0.5},
        ),
        sample_rate=16000,
    )

    assert service._settings.model == "public/tts/ua-rt"
    assert service._settings.sampling_params == {"temperature": 0.5}

    request = json.loads(service._build_request(None, context_id="ctx"))

    assert request["transcript"] == ""
    assert request["continue"] is False
    assert request["voice"]["sampling_params"] == {"temperature": 0.5}
    assert request["output_format"]["sample_rate"] == 16000


def test_requires_voice():
    with pytest.raises(ValueError):
        RespeecherTTSService(
            api_key="test-key",
            settings=RespeecherTTSService.Settings(),
        )
