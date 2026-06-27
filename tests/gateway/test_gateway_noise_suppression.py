"""Gateway Telegram noise-suppression contracts."""

from gateway.human_heartbeat import has_technical_leak, render_from_activity
from gateway.run import (
    build_busy_ack_message,
    _prepare_gateway_status_message,
    _sanitize_gateway_final_response,
)


def test_telegram_status_filters_compression_noise():
    text = "configured compression model gpt-x failed; opt back out: compression.model"

    assert _prepare_gateway_status_message("telegram", "status", text) is None


def test_non_telegram_status_keeps_original_message():
    text = "configured compression model gpt-x failed"

    assert _prepare_gateway_status_message("discord", "status", text) == text


def test_telegram_provider_error_is_sanitized():
    text = "HTTP 429 rate limited after 3 retries: raw-provider-request-id=abc"

    rendered = _sanitize_gateway_final_response("telegram", text)

    assert "temporairement limité" in rendered
    assert "raw-provider-request-id" not in rendered


def test_human_heartbeat_contract_for_busy_ack():
    text = render_from_activity(
        elapsed_seconds=600,
        activity={
            "api_call_count": 21,
            "max_iterations": 60,
            "current_tool": "waiting for non-streaming API response",
            "last_activity_desc": "waiting for non-streaming API response",
        },
        model="gpt-5.5-high",
    )

    assert "Hermes travaille" in text
    assert "10 min" in text
    assert "GPT-5.5" in text
    assert "21/60" not in text
    assert "iteration" not in text.lower()
    assert "non-streaming" not in text.lower()
    assert not has_technical_leak(text)


def test_busy_ack_interrupt_text_is_user_facing():
    text = build_busy_ack_message(
        "interrupt",
        elapsed_seconds=600,
        activity={
            "api_call_count": 21,
            "max_iterations": 60,
            "current_tool": "waiting for non-streaming API response",
            "last_activity_desc": "waiting for non-streaming API response",
        },
        model="gpt-5.5-high",
    )

    assert "J'interromps la tache en cours" in text
    assert "Hermes travaille" in text
    assert "10 min" in text
    assert "GPT-5.5" in text
    assert "21/60" not in text
    assert "iteration" not in text.lower()
    assert "non-streaming" not in text.lower()
    assert not has_technical_leak(text)


def test_busy_ack_queue_text_says_queued():
    text = build_busy_ack_message("queue")

    assert "Message mis en file" in text
    assert "prochain tour" in text
    assert "interromps" not in text


def test_busy_ack_steer_text_says_next_checkpoint():
    text = build_busy_ack_message("steer")

    assert "Message ajoute a la tache en cours" in text
    assert "prochain point de controle" in text
    assert "mis en file" not in text


def test_busy_ack_subagent_demotion_names_queue_and_stop_escape():
    text = build_busy_ack_message("queue", demoted_for_subagents=True)

    assert "sous-tache" in text
    assert "mis en file" in text
    assert "/stop" in text
