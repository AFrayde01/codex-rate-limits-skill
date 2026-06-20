# Codex Rate Limits Skill

Shareable Codex skill for inspecting Codex Desktop rate-limit windows and reset coupons.

## What It Does

- Reads the latest local Codex session snapshot for primary and secondary rate-limit windows
- Tries the live Codex reset-credit endpoint for exact coupon expirations
- Falls back to local Codex Desktop state when live coupon lookup is unavailable
- Supports human-readable output and raw JSON output

## Repository Layout

```text
codex-rate-limits-skill/
  codex-rate-limit-reset/
    SKILL.md
    agents/openai.yaml
    scripts/read_rate_limits.py
```

## Install

Clone this repository:

```bash
git clone https://github.com/AFrayde01/codex-rate-limits-skill.git
cd codex-rate-limits-skill
```

Copy the `codex-rate-limit-reset` folder into your Codex skills directory:

```bash
cp -R codex-rate-limit-reset ~/.codex/skills/
```

If you use a custom `CODEX_HOME`, copy it into:

```bash
$CODEX_HOME/skills/
```

Restart Codex if the skill does not appear immediately.

## Requirements

- Python 3.10 or newer recommended
- `certifi` is optional but helpful on some systems for HTTPS certificate trust when calling the live coupon endpoint

## Usage

From Codex chat:

```text
$codex-rate-limit-reset
```

From a terminal:

```bash
python3 codex-rate-limit-reset/scripts/read_rate_limits.py
python3 codex-rate-limit-reset/scripts/read_rate_limits.py --json
```

Optional flags:

```bash
python3 codex-rate-limit-reset/scripts/read_rate_limits.py --thread-id <thread-id>
python3 codex-rate-limit-reset/scripts/read_rate_limits.py --session-file <absolute-path-to-rollout.jsonl>
python3 codex-rate-limit-reset/scripts/read_rate_limits.py --auth "/Users/<user>/Library/Application Support/Parall/Codex/.codex/auth.json"
```

## Privacy And Security

Do not commit or share local Codex secrets or state files. This repository should only contain the skill files.

Do not share:

- `~/.codex/auth.json`
- `~/.codex/.codex-global-state.json`
- `/Users/<user>/Library/Application Support/Parall/Codex/.codex/auth.json`
- `/Users/<user>/Library/Application Support/Parall/Codex/.codex/.codex-global-state.json`

## Notes

- Standard rate-limit windows come from the latest local Codex session snapshot.
- Exact coupon expirations come from the live reset-credit endpoint when reachable.
- On some Python installs, HTTPS certificate trust may require `certifi`, which the script uses automatically when available.
- The repository only shares the skill code and instructions, never your personal Codex auth or local state.
