#!/usr/bin/env python3
"""
Read the latest Codex Desktop local session rate-limit snapshot.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

try:
    import certifi
except ImportError:  # pragma: no cover
    certifi = None  # type: ignore[assignment]


@dataclass
class Snapshot:
    session_file: Path
    thread_id: str | None
    event_timestamp: str | None
    rate_limits: dict[str, Any]


LOCAL_COUPON_TTL_DAYS = 30
LIVE_RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
LIVE_RESET_CREDITS_TIMEOUT_SEC = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read the latest Codex Desktop rate-limit snapshot from local session logs."
    )
    parser.add_argument("--sessions-root", help="Override the Codex sessions directory.")
    parser.add_argument("--session-file", help="Read a specific rollout JSONL file.")
    parser.add_argument("--thread-id", help="Limit matches to a specific thread id.")
    parser.add_argument("--auth", help="Override the Codex auth.json file for live coupon lookups.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument(
        "--timezone",
        help="IANA timezone name for rendered timestamps, for example America/Merida.",
    )
    return parser.parse_args()


def build_candidate_roots(explicit_root: str | None) -> list[Path]:
    roots: list[Path] = []
    if explicit_root:
        roots.append(Path(explicit_root).expanduser())

    codex_home = Path.home() / ".codex"
    env_home = Path(os.environ.get("CODEX_HOME", str(codex_home))).expanduser()

    roots.extend(
        [
            env_home / "sessions",
            codex_home / "sessions",
            Path.home() / "Library/Application Support/Parall/Codex/.codex/sessions",
        ]
    )

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            deduped.append(root)
            seen.add(key)
    return deduped


def build_global_state_candidates() -> list[Path]:
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    candidates = [
        codex_home / ".codex-global-state.json",
        Path.home() / "Library/Application Support/Parall/Codex/.codex/.codex-global-state.json",
    ]

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def build_auth_candidates(explicit_auth: str | None) -> list[Path]:
    auth_paths: list[Path] = []
    if explicit_auth:
        auth_paths.append(Path(explicit_auth).expanduser())

    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    auth_paths.extend(
        [
            codex_home / "auth.json",
            Path.home() / ".codex" / "auth.json",
            Path.home() / "Library/Application Support/Parall/Codex/.codex/auth.json",
        ]
    )

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in auth_paths:
        key = str(path)
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def choose_timezone(name: str | None):
    if name:
        if ZoneInfo is None:
            raise SystemExit("Timezone overrides require Python zoneinfo support.")
        try:
            return ZoneInfo(name)
        except Exception as exc:  # pragma: no cover
            raise SystemExit(f"Unknown timezone '{name}': {exc}") from exc
    return datetime.now().astimezone().tzinfo


def find_session_files(args: argparse.Namespace) -> list[Path]:
    if args.session_file:
        session_file = Path(args.session_file).expanduser()
        if not session_file.exists():
            raise SystemExit(f"Session file not found: {session_file}")
        return [session_file]

    for root in build_candidate_roots(args.sessions_root):
        if not root.exists():
            continue
        files = sorted(
            root.rglob("rollout-*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not files:
            continue
        if args.thread_id:
            filtered = [path for path in files if args.thread_id in path.name]
            if filtered:
                return filtered
            continue
        return files

    searched = ", ".join(str(path) for path in build_candidate_roots(args.sessions_root))
    raise SystemExit(f"No Codex session logs found. Searched: {searched}")


def extract_snapshot(session_file: Path) -> Snapshot | None:
    thread_id = None
    latest: Snapshot | None = None

    with session_file.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") == "session_meta":
                payload = entry.get("payload", {})
                if isinstance(payload, dict):
                    thread_id = payload.get("id") or thread_id

            if entry.get("type") != "event_msg":
                continue

            payload = entry.get("payload", {})
            if not isinstance(payload, dict):
                continue
            if payload.get("type") != "token_count":
                continue

            rate_limits = payload.get("rate_limits")
            if not isinstance(rate_limits, dict):
                continue

            latest = Snapshot(
                session_file=session_file,
                thread_id=thread_id,
                event_timestamp=entry.get("timestamp"),
                rate_limits=rate_limits,
            )

    return latest


def human_duration(seconds: float) -> str:
    remaining = max(int(round(seconds)), 0)
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def human_relative_datetime(dt: datetime | None, tzinfo) -> tuple[str | None, str | None]:
    if dt is None:
        return None, None
    local_dt = dt.astimezone(tzinfo)
    remaining = local_dt.timestamp() - datetime.now(tz=tzinfo).timestamp()
    return local_dt.isoformat(timespec="seconds"), human_duration(remaining)


def iso_local(timestamp: int | float | None, tzinfo) -> tuple[str | None, str | None]:
    if timestamp is None:
        return None, None
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(tzinfo)
    remaining = dt.timestamp() - datetime.now(tz=tzinfo).timestamp()
    return dt.isoformat(timespec="seconds"), human_duration(remaining)


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_output(snapshot: Snapshot, tzinfo) -> dict[str, Any]:
    rate_limits = snapshot.rate_limits
    output: dict[str, Any] = {
        "session_file": str(snapshot.session_file),
        "thread_id": snapshot.thread_id,
        "snapshot_timestamp": snapshot.event_timestamp,
        "limit_id": rate_limits.get("limit_id"),
        "limit_name": rate_limits.get("limit_name"),
        "plan_type": rate_limits.get("plan_type"),
        "rate_limit_reached_type": rate_limits.get("rate_limit_reached_type"),
        "credits": rate_limits.get("credits"),
        "individual_limit": rate_limits.get("individual_limit"),
    }

    for key in ("primary", "secondary"):
        window = rate_limits.get(key)
        if not isinstance(window, dict):
            output[key] = None
            continue
        reset_iso, remaining = iso_local(window.get("resets_at"), tzinfo)
        output[key] = {
            "used_percent": window.get("used_percent"),
            "window_minutes": window.get("window_minutes"),
            "resets_at": window.get("resets_at"),
            "resets_at_local": reset_iso,
            "time_until_reset": remaining,
        }

    return output


def build_ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def resolve_auth_path(explicit_auth: str | None) -> Path:
    for path in build_auth_candidates(explicit_auth):
        if path.exists():
            return path
    searched = ", ".join(str(path) for path in build_auth_candidates(explicit_auth))
    raise FileNotFoundError(f"No Codex auth.json found. Searched: {searched}")


def load_auth(auth_path: Path) -> dict[str, Any]:
    return json.loads(auth_path.read_text(encoding="utf-8"))


def fetch_live_reset_coupons(explicit_auth: str | None, tzinfo) -> dict[str, Any]:
    auth_path = resolve_auth_path(explicit_auth)
    auth = load_auth(auth_path)

    tokens = auth.get("tokens")
    if not isinstance(tokens, dict):
        raise ValueError(f"Auth file is missing a 'tokens' object: {auth_path}")

    access_token = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError(f"Auth file is missing tokens.access_token: {auth_path}")
    if not isinstance(account_id, str) or not account_id:
        raise ValueError(f"Auth file is missing tokens.account_id: {auth_path}")

    request = urllib.request.Request(
        LIVE_RESET_CREDITS_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "ChatGPT-Account-ID": account_id,
            "OpenAI-Beta": "codex-1",
            "originator": "Codex Desktop",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=LIVE_RESET_CREDITS_TIMEOUT_SEC,
        context=build_ssl_context(),
    ) as response:
        payload = json.loads(response.read())

    credits = payload.get("credits")
    if not isinstance(credits, list):
        credits = []

    normalized_credits: list[dict[str, Any]] = []
    for index, credit in enumerate(credits, start=1):
        if not isinstance(credit, dict):
            continue
        expires_at = parse_iso_datetime(credit.get("expires_at"))
        granted_at = parse_iso_datetime(credit.get("granted_at"))
        expires_at_local, time_until_expiry = human_relative_datetime(expires_at, tzinfo)
        granted_at_local, _ = human_relative_datetime(granted_at, tzinfo)
        normalized_credits.append(
            {
                "index": index,
                "status": credit.get("status"),
                "granted_at": credit.get("granted_at"),
                "granted_at_local": granted_at_local,
                "expires_at": credit.get("expires_at"),
                "expires_at_local": expires_at_local,
                "time_until_expiry": time_until_expiry,
            }
        )

    normalized_credits.sort(
        key=lambda credit: (
            parse_iso_datetime(credit.get("expires_at"))
            or datetime.max.replace(tzinfo=timezone.utc)
        )
    )

    available_credits = [
        credit for credit in normalized_credits if credit.get("status") == "available"
    ]
    next_credit = available_credits[0] if available_credits else (
        normalized_credits[0] if normalized_credits else None
    )

    return {
        "source": "live_api",
        "source_description": "Live Codex reset-credit endpoint",
        "endpoint": LIVE_RESET_CREDITS_URL,
        "auth_file": str(auth_path),
        "available_count": payload.get("available_count"),
        "total_earned_count": payload.get("total_earned_count"),
        "credits": normalized_credits,
        "next_expiring_credit": next_credit,
    }


def read_reset_coupon_state(tzinfo, fallback_reason: str | None = None) -> dict[str, Any] | None:
    state_path = next((path for path in build_global_state_candidates() if path.exists()), None)
    if state_path is None:
        return None

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    atom_state = raw.get("electron-persisted-atom-state")
    if not isinstance(atom_state, dict):
        return None

    entries = atom_state.get("rate-limit-reset-home-announcement-dismissal-by-account-id")
    if not isinstance(entries, dict) or not entries:
        return None

    account_id, payload = next(iter(entries.items()))
    if not isinstance(payload, dict):
        return None

    available_count = payload.get("availableCount")
    dismissed_at_ms = payload.get("dismissedAtMs")
    dismissed_at_local = None
    latest_possible_expiry_local = None
    latest_possible_expiry_note = None

    if isinstance(dismissed_at_ms, (int, float)):
        dismissed = datetime.fromtimestamp(
            dismissed_at_ms / 1000,
            tz=timezone.utc,
        ).astimezone(tzinfo)
        dismissed_at_local = dismissed.isoformat(timespec="seconds")
        latest_possible_expiry_local = (
            dismissed + timedelta(days=LOCAL_COUPON_TTL_DAYS)
        ).isoformat(timespec="seconds")
        latest_possible_expiry_note = (
            "Upper bound only: this is 30 days after the local dismissal/observation timestamp, "
            "not a server-provided coupon expiry."
        )

    result = {
        "source": "local_state_fallback",
        "source_description": "Local Codex Desktop global state fallback",
        "source_file": str(state_path),
        "account_key_present": bool(account_id),
        "available_count": available_count,
        "dismissed_at_ms": dismissed_at_ms,
        "dismissed_at_local": dismissed_at_local,
        "latest_possible_expiry_local": latest_possible_expiry_local,
        "latest_possible_expiry_note": latest_possible_expiry_note,
    }
    if fallback_reason:
        result["fallback_reason"] = fallback_reason
    return result


def read_reset_coupons(explicit_auth: str | None, tzinfo) -> dict[str, Any] | None:
    try:
        return fetch_live_reset_coupons(explicit_auth, tzinfo)
    except (
        FileNotFoundError,
        ValueError,
        json.JSONDecodeError,
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
    ) as exc:
        fallback = read_reset_coupon_state(tzinfo, fallback_reason=str(exc))
        if fallback is not None:
            return fallback
        return {
            "source": "unavailable",
            "source_description": "No live coupon data or local fallback metadata available",
            "error": str(exc),
        }


def print_text(output: dict[str, Any]) -> None:
    coupon_state = output.get("reset_coupons")
    if isinstance(coupon_state, dict):
        print("Reset Coupons")
        if coupon_state.get("source_description"):
            print(f"  Source: {coupon_state.get('source_description')}")
        print(f"  Available: {coupon_state.get('available_count')}")
        if coupon_state.get("total_earned_count") is not None:
            print(f"  Earned: {coupon_state.get('total_earned_count')}")
        next_credit = coupon_state.get("next_expiring_credit")
        if isinstance(next_credit, dict):
            print(f"  Next Expires: {next_credit.get('expires_at_local')}")
            print(f"  Time Left: {next_credit.get('time_until_expiry')}")
        credits = coupon_state.get("credits")
        if isinstance(credits, list):
            for credit in credits:
                if not isinstance(credit, dict):
                    continue
                print(
                    f"  #{credit.get('index')} {credit.get('status')} "
                    f"expires {credit.get('expires_at_local')} "
                    f"({credit.get('time_until_expiry')})"
                )
                if credit.get("granted_at_local"):
                    print(f"    Granted: {credit.get('granted_at_local')}")
        if coupon_state.get("dismissed_at_local"):
            print(f"  Local Timestamp: {coupon_state.get('dismissed_at_local')}")
        if coupon_state.get("latest_possible_expiry_local"):
            print(f"  Expires No Later Than: {coupon_state.get('latest_possible_expiry_local')}")
        if coupon_state.get("latest_possible_expiry_note"):
            print(f"  Note: {coupon_state.get('latest_possible_expiry_note')}")
        if coupon_state.get("fallback_reason"):
            print(f"  Live Lookup Fallback Reason: {coupon_state.get('fallback_reason')}")
        if coupon_state.get("error"):
            print(f"  Error: {coupon_state.get('error')}")
        print()

    print(f"Source: {output['session_file']}")
    if output.get("thread_id"):
        print(f"Thread ID: {output['thread_id']}")
    if output.get("snapshot_timestamp"):
        print(f"Snapshot: {output['snapshot_timestamp']}")
    if output.get("plan_type"):
        print(f"Plan: {output['plan_type']}")
    if output.get("limit_id"):
        print(f"Limit ID: {output['limit_id']}")
    if output.get("rate_limit_reached_type"):
        print(f"Reached Type: {output.get('rate_limit_reached_type')}")

    for label in ("primary", "secondary"):
        window = output.get(label)
        if not isinstance(window, dict):
            continue
        print()
        print(f"{label.title()} Window")
        print(f"  Used: {window.get('used_percent')}%")
        print(f"  Length: {window.get('window_minutes')} minutes")
        print(f"  Resets: {window.get('resets_at_local')}")
        print(f"  Remaining: {window.get('time_until_reset')}")


def main() -> int:
    args = parse_args()
    tzinfo = choose_timezone(args.timezone)
    session_files = find_session_files(args)

    snapshot: Snapshot | None = None
    for session_file in session_files:
        snapshot = extract_snapshot(session_file)
        if snapshot is not None:
            break

    if snapshot is None:
        print("No rate-limit snapshot found in the matching session logs.", file=sys.stderr)
        return 1

    output = build_output(snapshot, tzinfo)
    output["reset_coupons"] = read_reset_coupons(args.auth, tzinfo)
    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print_text(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
