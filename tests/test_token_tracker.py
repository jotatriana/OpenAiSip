"""Tests for token usage tracking and aggregation in state_store."""
import pytest
from datetime import datetime

from core.models import Call, CallState, TokenUsage
from core.state_store import StateStore


@pytest.fixture
def store():
    return StateStore()


@pytest.fixture
def call():
    return Call(call_id="call-token-test", state=CallState.ACTIVE)


async def test_token_usage_aggregates_per_call(store, call):
    await store.create_call(call)

    usage1 = TokenUsage(
        call_id="call-token-test", session_id="s1", response_id="r1",
        total_tokens=100, input_tokens=60, output_tokens=40,
        input_audio_tokens=50, input_text_tokens=10,
        output_audio_tokens=30, output_text_tokens=10,
    )
    usage2 = TokenUsage(
        call_id="call-token-test", session_id="s1", response_id="r2",
        total_tokens=200, input_tokens=120, output_tokens=80,
        input_audio_tokens=100, input_text_tokens=20,
        output_audio_tokens=60, output_text_tokens=20,
    )

    await store.record_token_usage(usage1)
    await store.record_token_usage(usage2)

    call_tokens = await store.get_call_tokens("call-token-test")
    assert call_tokens.total_tokens == 300
    assert call_tokens.input_tokens == 180
    assert call_tokens.output_tokens == 120
    assert call_tokens.response_count == 2


async def test_global_token_aggregation(store):
    for i in range(3):
        c = Call(call_id=f"call-{i}", state=CallState.ACTIVE)
        await store.create_call(c)
        usage = TokenUsage(
            call_id=f"call-{i}", session_id="s", response_id=f"r{i}",
            total_tokens=100,
        )
        await store.record_token_usage(usage)

    global_tokens = await store.get_global_tokens()
    assert global_tokens.total_tokens == 300
    assert global_tokens.response_count == 3


async def test_call_tokens_not_found(store):
    result = await store.get_call_tokens("nonexistent")
    assert result is None


async def test_token_fields_all_tracked(store, call):
    await store.create_call(call)
    usage = TokenUsage(
        call_id="call-token-test", session_id="s1", response_id="r1",
        total_tokens=500,
        input_tokens=300, output_tokens=200,
        input_text_tokens=50, input_audio_tokens=200, input_cached_tokens=50,
        output_text_tokens=50, output_audio_tokens=150,
    )
    await store.record_token_usage(usage)
    agg = await store.get_call_tokens("call-token-test")
    assert agg.input_cached_tokens == 50
    assert agg.input_audio_tokens == 200
    assert agg.output_audio_tokens == 150
