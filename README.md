# Pipecat Respeecher Real-Time TTS Integration

[![PyPI - Version](https://img.shields.io/pypi/v/pipecat-respeecher)](https://pypi.python.org/pypi/pipecat-respeecher)

This is an official Respeecher integration for [Pipecat](https://pipecat.ai).

[Learn more](https://www.respeecher.com/real-time-tts-api) about our real-time TTS API
([Україномовна/Ukrainian TTS](https://www.respeecher.com/uk/real-time-tts-api)).

**Maintainer: [Respeecher](https://www.respeecher.com/)**

## Installation

```
pip install pipecat-respeecher
```

## Running the Example

[`example.py`](./example.py) is a complete Pipecat pipeline with Respeecher TTS.
(See [`example-ukrainian.py`](./example-ukrainian.py) for a Ukrainian language pipeline.)
You can use it as a starting point for your agent,
or you can head over to [Example Snippets](#example-snippets)
if you already have a pipeline and just want to switch TTS.

The complete pipeline example requires a
[Deepgram](https://docs.pipecat.ai/server/services/stt/deepgram) API key for
Speech-to-Text, either a [Google Gemini](https://docs.pipecat.ai/server/services/llm/gemini)
API key or a [Cerebras](https://docs.pipecat.ai/server/services/llm/cerebras) API key for LLM,
and a [Respeecher Space](https://space.respeecher.com/api-keys) API key.
The Speech-to-Text and LLM services are just an example and can generally be swapped for any
other [supported Pipecat service](https://docs.pipecat.ai/server/services/supported-services).

1. Clone this repository.
2. Copy `env.example` to `.env` and fill in your API keys.
3. Assuming you have the [uv](https://docs.astral.sh/uv/getting-started/installation/)
   Python package manager installed, run `uv run example.py`, head over to
   http://localhost:7860, and click _Connect_.
   (The first run of `uv run example.py` may be slow because uv installs packages
   and Pipecat downloads local models.)
   The agent should greet you (both in text and in speech),
   and you can converse with it through the chat interface or with your microphone.
   (Make sure you have granted microphone access to the web page and that the microphone button
   is not in the muted state.)

## Example Snippets

**Note:** we recommend setting `audio_out_sample_rate=22050` in `PipelineParams`
like in [`example.py`](./example.py) for best results.

### Minimal Example

```python
from pipecat_respeecher import RespeecherTTSService

tts = RespeecherTTSService(
    api_key=os.getenv("RESPEECHER_API_KEY"),
    voice_id="samantha",
)
```

### Overriding Sampling Parameters

See the [Sampling Parameters Guide](https://space.respeecher.com/docs/api/tts/sampling-params-guide).

```python
from pipecat_respeecher import RespeecherTTSService

tts = RespeecherTTSService(
    api_key=os.getenv("RESPEECHER_API_KEY"),
    voice_id="samantha",
    params=RespeecherTTSService.InputParams(
        sampling_params={
            "min_p": 0.01,
        },
    ),
)
```

### Ukrainian Language Model

See [Models & Languages](https://space.respeecher.com/docs/models-and-languages).

```python
from pipecat_respeecher import RespeecherTTSService

tts = RespeecherTTSService(
    api_key=os.getenv("RESPEECHER_API_KEY"),
    model="public/tts/ua-rt",
    voice_id="olesia-conversation",
)
```

## Compatibility

This integration requires Pipecat v0.0.99 or newer and has been tested with v0.0.99.
