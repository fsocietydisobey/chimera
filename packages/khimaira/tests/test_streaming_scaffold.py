"""Tests for the streaming scaffold (#55 partial).

NORTH_STAR Phase 4 includes "streaming responses through delegate/auto."
This file covers the FOUNDATION that real per-chunk streaming will
build on:

  - `StreamChunk` dataclass — the unit of a stream
  - `CLIRunner.stream()` Protocol method — uniform API across runners
  - `default_stream_via_run` — degenerate one-chunk impl for runners
    without a native streaming mode

Real per-chunk streaming (parsing Claude's `--output-format stream-json`
line-by-line and yielding text-deltas as they arrive) is a separate
task. It can drop in without changing callers because they already
code against the Protocol.

Tests:
  - StreamChunk default values
  - default_stream_via_run yields exactly one final chunk that mirrors
    the runner's run() return
  - ClaudeRunner.stream() uses the default; yields one final chunk
  - The scaffolded stream() can be drop-in replaced (mock the runner's
    run() to fail; verify stream() surfaces the same failure shape)
"""

from __future__ import annotations

import pytest

from khimaira.dispatch.runners.base import (
    RunnerResult,
    StreamChunk,
    default_stream_via_run,
)


def test_stream_chunk_defaults():
    """A bare StreamChunk has empty text and is not marked final."""
    c = StreamChunk()
    assert c.text == ""
    assert c.is_final is False
    assert c.model == ""
    assert c.input_tokens == 0


def test_stream_chunk_final():
    """Final chunk carries the metadata."""
    c = StreamChunk(
        text="full answer",
        is_final=True,
        model="claude-haiku-4-5",
        input_tokens=100,
        output_tokens=50,
        session_id="sess-1",
    )
    assert c.is_final is True
    assert c.model == "claude-haiku-4-5"
    assert c.input_tokens == 100


async def test_default_stream_via_run_yields_one_final_chunk():
    """Runners without native streaming get a degenerate one-chunk
    stream that mirrors their run() return."""

    class _FakeRunner:
        name = "fake"

        def is_available(self):
            return True

        async def run(self, prompt, **kwargs):
            return RunnerResult(
                text="hello world",
                runner="fake",
                model="fake-model-v1",
                input_tokens=5,
                output_tokens=10,
                latency_s=0.1,
                session_id="s-1",
            )

    chunks: list[StreamChunk] = []
    async for chunk in default_stream_via_run(_FakeRunner(), "prompt"):
        chunks.append(chunk)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.is_final is True
    assert chunk.text == "hello world"
    assert chunk.model == "fake-model-v1"
    assert chunk.input_tokens == 5
    assert chunk.output_tokens == 10
    assert chunk.session_id == "s-1"


async def test_claude_runner_stream_yields_via_default(monkeypatch):
    """ClaudeRunner.stream() is scaffolded via default_stream_via_run.
    Verify the integration: it yields one final chunk with the same data
    as run() would have returned."""
    from khimaira.dispatch.runners.claude import ClaudeRunner

    runner = ClaudeRunner()

    async def _fake_run(self, prompt, **kwargs):
        return RunnerResult(
            text="streamed answer",
            runner="claude",
            model="claude-haiku-4-5",
            input_tokens=7,
            output_tokens=12,
            latency_s=0.05,
        )

    monkeypatch.setattr(ClaudeRunner, "run", _fake_run)

    chunks: list[StreamChunk] = []
    async for chunk in runner.stream("test prompt"):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].text == "streamed answer"
    assert chunks[0].is_final
    assert chunks[0].model == "claude-haiku-4-5"


async def test_stream_propagates_runner_exceptions():
    """If the underlying run() raises, the stream propagates the
    exception (caller's responsibility to handle)."""

    class _BoomRunner:
        name = "boom"

        def is_available(self):
            return True

        async def run(self, prompt, **kwargs):
            raise RuntimeError("simulated runner crash")

    runner = _BoomRunner()
    with pytest.raises(RuntimeError, match="simulated runner crash"):
        async for _ in default_stream_via_run(runner, "anything"):
            pass


async def test_concatenated_chunks_equal_run_text_for_degenerate_stream():
    """The contract: concatenating every chunk's `text` for a streamed
    response equals the `text` you'd have gotten from `run()`. Trivially
    true for the degenerate one-chunk stream — locks the contract so
    future real-streaming impls preserve it."""

    class _FakeRunner:
        name = "fake"

        def is_available(self):
            return True

        async def run(self, prompt, **kwargs):
            return RunnerResult(
                text="The quick brown fox jumps over the lazy dog.",
                runner="fake",
                model="m",
            )

    chunks = []
    async for c in default_stream_via_run(_FakeRunner(), ""):
        chunks.append(c)

    concatenated = "".join(c.text for c in chunks)
    assert concatenated == "The quick brown fox jumps over the lazy dog."
