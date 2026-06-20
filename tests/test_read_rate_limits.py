from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from urllib.error import URLError


FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_snapshot_reads_latest_token_count(module):
    snapshot = module.extract_snapshot(FIXTURES / "session-rollout.jsonl")

    assert snapshot is not None
    assert snapshot.thread_id == "thread-123"
    assert snapshot.event_timestamp == "2026-06-20T04:05:00.000Z"
    assert snapshot.rate_limits["primary"]["used_percent"] == 8.0
    assert snapshot.rate_limits["secondary"]["used_percent"] == 10.0


def test_find_session_files_honors_thread_filter(module, tmp_path):
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    matching = sessions_root / "rollout-thread-123.jsonl"
    matching.write_text("{}", encoding="utf-8")
    other = sessions_root / "rollout-thread-999.jsonl"
    other.write_text("{}", encoding="utf-8")

    args = module.argparse.Namespace(
        sessions_root=str(sessions_root),
        session_file=None,
        thread_id="thread-123",
        auth=None,
        json=False,
        timezone=None,
    )

    files = module.find_session_files(args)

    assert files == [matching]


def test_build_output_formats_windows(module):
    snapshot = module.extract_snapshot(FIXTURES / "session-rollout.jsonl")
    assert snapshot is not None

    output = module.build_output(snapshot, UTC)

    assert output["plan_type"] == "prolite"
    assert output["rate_limit_reached_type"] == "soft"
    assert output["primary"]["resets_at_local"] == "2026-06-20T09:09:17+00:00"
    assert output["secondary"]["resets_at_local"] == "2026-06-24T21:23:49+00:00"
    assert output["primary"]["time_until_reset"]
    assert output["secondary"]["time_until_reset"]


def test_read_reset_coupon_state_uses_fixture(module, monkeypatch):
    fixture_path = FIXTURES / "global-state.json"
    monkeypatch.setattr(module, "build_global_state_candidates", lambda: [fixture_path])

    output = module.read_reset_coupon_state(UTC)

    assert output is not None
    assert output["source"] == "local_state_fallback"
    assert output["available_count"] == 2
    assert output["account_key_present"] is True
    assert output["dismissed_at_local"] == "2026-06-18T00:56:28+00:00"
    assert output["latest_possible_expiry_local"] == "2026-07-18T00:56:28+00:00"


def test_fetch_live_reset_coupons_normalizes_response(module, monkeypatch):
    auth_path = FIXTURES / "auth.json"
    monkeypatch.setattr(module, "build_auth_candidates", lambda explicit_auth: [auth_path])

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "available_count": 2,
                    "total_earned_count": 3,
                    "credits": [
                        {
                            "status": "available",
                            "granted_at": "2026-06-18T00:42:45.703065Z",
                            "expires_at": "2026-07-18T00:42:45.703065Z",
                        },
                        {
                            "status": "available",
                            "granted_at": "2026-06-12T02:38:07.993063Z",
                            "expires_at": "2026-07-12T02:38:07.993063Z",
                        },
                    ],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout, context):
        headers = {key.lower(): value for key, value in request.header_items()}
        assert headers["authorization"] == "Bearer test-access-token"
        assert headers["chatgpt-account-id"] == "acct_123"
        assert timeout == module.LIVE_RESET_CREDITS_TIMEOUT_SEC
        assert context is not None
        return DummyResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    output = module.fetch_live_reset_coupons(None, UTC)

    assert output["source"] == "live_api"
    assert output["available_count"] == 2
    assert output["total_earned_count"] == 3
    assert output["credits"][0]["expires_at"] == "2026-07-12T02:38:07.993063Z"
    assert output["next_expiring_credit"]["expires_at"] == "2026-07-12T02:38:07.993063Z"


def test_read_reset_coupons_falls_back_on_network_error(module, monkeypatch):
    fixture_path = FIXTURES / "global-state.json"
    monkeypatch.setattr(
        module,
        "fetch_live_reset_coupons",
        lambda explicit_auth, tzinfo: (_ for _ in ()).throw(URLError("offline")),
    )
    monkeypatch.setattr(module, "build_global_state_candidates", lambda: [fixture_path])

    output = module.read_reset_coupons(None, UTC)

    assert output is not None
    assert output["source"] == "local_state_fallback"
    assert "offline" in output["fallback_reason"]


def test_print_text_renders_live_coupon_summary(module, capsys):
    output = {
        "session_file": "/tmp/session.jsonl",
        "thread_id": "thread-123",
        "snapshot_timestamp": "2026-06-20T04:05:00.000Z",
        "plan_type": "prolite",
        "limit_id": "codex",
        "rate_limit_reached_type": None,
        "primary": {
            "used_percent": 8.0,
            "window_minutes": 300,
            "resets_at_local": "2026-06-20T09:09:17+00:00",
            "time_until_reset": "1h 0m 0s",
        },
        "secondary": {
            "used_percent": 10.0,
            "window_minutes": 10080,
            "resets_at_local": "2026-06-24T21:23:49+00:00",
            "time_until_reset": "4d 0h 0m 0s",
        },
        "reset_coupons": {
            "source_description": "Live Codex reset-credit endpoint",
            "available_count": 2,
            "total_earned_count": 3,
            "next_expiring_credit": {
                "expires_at_local": "2026-07-12T02:38:07+00:00",
                "time_until_expiry": "21d 0h 0m 0s",
            },
            "credits": [
                {
                    "index": 1,
                    "status": "available",
                    "expires_at_local": "2026-07-12T02:38:07+00:00",
                    "time_until_expiry": "21d 0h 0m 0s",
                    "granted_at_local": "2026-06-12T02:38:07+00:00",
                }
            ],
        },
    }

    module.print_text(output)
    captured = capsys.readouterr().out

    assert "Reset Coupons" in captured
    assert "Source: Live Codex reset-credit endpoint" in captured
    assert "#1 available expires 2026-07-12T02:38:07+00:00" in captured
    assert "Primary Window" in captured
    assert "Secondary Window" in captured
