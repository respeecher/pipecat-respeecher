FROM dailyco/pipecat-base:latest

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY ./src src

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --locked

COPY ./example.py bot.py
