# CLAUDE.md — Aymannoti Codebase Guide

This file provides context for AI assistants (Claude, etc.) working on this codebase.

---

## Project Overview

**Aymannoti** is a TikTok and Instagram-to-Discord notification bot. It polls TikTok and Instagram accounts using `yt-dlp`, deduplicates posts via SQLite, and dispatches notifications to Discord webhooks. A Flask-based web dashboard provides account/group management and monitoring.

- **Version**: 1.3
- **Language**: Python 3.x
- **Deployment**: systemd service on Linux

---

## Repository Structure

```
notiaimnh/
├── main.py                # Entry point — async polling loop (TikTok + Instagram)
├── dashboard.py           # Flask web API and dashboard server
├── poller.py              # TikTok feed fetcher (via yt-dlp)
├── instagram_poller.py    # Instagram feed fetcher (via yt-dlp)
├── notifier.py            # Discord webhook dispatcher (TikTok + Instagram)
├── database.py            # SQLite data layer
├── config_helper.py       # Config loading/saving + constants
├── manage.py              # CLI for managing groups and accounts (TikTok + Instagram)
├── config.yaml            # Runtime config (webhooks, accounts, settings)
├── aymannoti.service  # systemd unit file
├── requirements.txt   # Python dependencies
├── setup_rsshub.sh    # Optional RSSHub Docker setup
├── templates/
│   └── dashboard.html # Web dashboard UI
└── static/
    └── style.css      # Dark-theme dashboard styling
```

**Runtime-generated files** (not in git, do not commit):
- `aymannoti.db` — SQLite database
- `aymannoti.log` — Application log file
- `status.json` — Current polling cycle status

---

## Technology Stack

| Component | Technology |
|-----------|------------|
| Core language | Python 3.x |
| TikTok metadata | `yt-dlp` |
| HTTP client | `httpx` (async) |
| Web framework | Flask 3.x |
| Database | SQLite 3 (WAL mode) |
| Frontend | Vanilla JS + HTML5 |
| Config format | YAML |
| Service manager | systemd |

---

## Key Modules

### `main.py` — Polling Orchestrator
- `async def run()` is the entry point
- Loops continuously; each cycle:
  1. Reloads `config.yaml` (hot-reload without restart)
  2. Iterates groups → accounts
  3. Fetches recent posts via `Poller`
  4. Filters posts older than `MAX_POST_AGE_HOURS` (48h default)
  5. Marks first-seen posts as seen (no initial spam on startup)
  6. Sends Discord notification for new unseen posts
  7. Logs cycle summary to DB and `status.json`
- Graceful shutdown on `KeyboardInterrupt`

### `poller.py` — TikTok Feed Fetcher
- Class `Poller` wraps `yt-dlp` in `asyncio.to_thread()`
- Retry logic: **3 attempts, exponential backoff** (2, 4, 8 seconds)
- Distinguishes permanent errors (account not found/private) from transient ones
- Returns list of dicts: `{id, title, url, description, timestamp, thumbnail}`

### `notifier.py` — Discord Notifier
- Class `Notifier` uses pooled `httpx.AsyncClient`
- Handles Discord **rate-limits (HTTP 429)** with `Retry-After` header
- Message format: `@everyone` ping + account tag + post URL
- TikTok brand color `#69C9D0` used in embeds

### `database.py` — SQLite Layer
- Context manager interface: `with Database() as db:`
- WAL mode enabled for concurrent read/write
- Two tables:
  - `seen_posts(username, post_id, url, timestamp)` — deduplication
  - `bot_log(timestamp, event, level, ...)` — analytics/dashboard logs
- Key methods: `is_seen()`, `mark_seen()`, `has_been_checked()`, `get_stats()`, `add_log()`
- Raw parameterized SQL — no ORM

### `dashboard.py` — Flask API Server
- Runs on configurable host/port (default `0.0.0.0:8080`)
- REST API endpoints:
  - `GET /api/stats` — aggregate stats
  - `GET /api/status` — current polling cycle status
  - `GET /api/health` — health check
  - `GET/POST /api/groups` — list/create groups
  - `DELETE /api/groups/<name>` — remove group
  - `GET/POST /api/groups/<name>/accounts` — list/add accounts
  - `DELETE /api/groups/<name>/accounts/<username>` — remove account
  - `POST /api/groups/<name>/accounts/<username>/test` — send test notification
  - `GET /api/check/<username>` — validate TikTok username
  - `POST /api/test-webhook` — test Discord webhook
  - `GET /api/logs` — recent log entries
  - `GET /api/logs/summary` — log analytics
  - `DELETE /api/logs` — clear logs
  - `GET /api/version` — app version

### `config_helper.py` — Configuration
- `load_config()` — loads `config.yaml` with sensible defaults
- `save_config(config)` — writes back to `config.yaml`
- `VERSION = "1.3"`, `BASE_DIR`, `CONFIG_PATH` constants

### `instagram_poller.py` — Instagram Feed Fetcher
- Class `InstagramPoller`, mirrors `Poller` but targets Instagram
- URL pattern: `https://www.instagram.com/<username>/`
- Post URLs come from `entry["webpage_url"]` (can be `/p/<id>/` or `/reel/<id>/`)
- Logs a warning if no `cookies_file` is set — Instagram aggressively rate-limits unauthenticated requests
- Same retry/backoff logic as `Poller` (3 attempts, 2^n seconds)
- Permanent error strings: `"does not exist"`, `"404"`, `"This account is private"`, `"Sorry, this page"`, `"not available"`

### `manage.py` — CLI Tool
- Usage: `python manage.py <subcommand>`
- Subcommands:
  - `group add <name> <webhook_url>`
  - `group list`
  - `group remove <name>`
  - `account add <group> <username> [username ...]`
  - `account remove <group> <username>`
  - `account list`
  - `account import <group> <file>` — bulk import (lines starting with `#` are comments; `@` prefix stripped)
  - `instagram add <group> <username> [username ...]`
  - `instagram remove <group> <username>`
  - `instagram list`
  - `instagram import <group> <file>`

---

## Configuration (`config.yaml`)

Key config fields:

```yaml
tiktok:
  cookies_file: ''            # Path to Netscape cookies file for TikTok (optional)
instagram:
  cookies_file: ''            # Path to Netscape cookies file for Instagram (STRONGLY recommended)
polling:
  interval_minutes: 3         # Minutes between full cycles
  delay_between_requests: 1   # Seconds between per-account requests
discord:
  bot_name: "Aymannoti"       # Discord bot display name
dashboard:
  host: "0.0.0.0"
  port: 8080
groups:
  - name: "GroupName"
    webhook_url: "https://discord.com/api/webhooks/..."
    accounts:                 # TikTok usernames
      - tiktok_user1
    instagram_accounts:       # Instagram usernames
      - insta_user1
```

**Instagram cookies**: Instagram heavily rate-limits unauthenticated yt-dlp requests. Export cookies from a logged-in browser session (e.g. using the "Get cookies.txt LOCALLY" extension) and set `instagram.cookies_file` to the file path.

Config is **hot-reloaded** each polling cycle — add/remove accounts without restarting the service.

---

## Development Conventions

### Code Style
- Python 3 with standard library `asyncio`
- Async/await throughout for I/O-bound operations
- CPU-bound calls (yt-dlp) wrapped with `asyncio.to_thread()`
- Raw SQL with parameterized queries — no ORM
- Context managers for DB connections

### Error Handling
- Exceptions are caught and logged; they do **not** crash the bot
- Per-account errors accumulate and are included in cycle summary
- Discord 429 rate-limits are explicitly handled
- Distinguish permanent errors (deleted account) from transient ones

### Logging
- Python standard `logging` module
- Logs to both console and `aymannoti.log`
- Log levels: `INFO`, `WARNING`, `ERROR`
- Per-account timing logged at millisecond precision
- Cycle summaries include: accounts checked, notifications sent, errors

### Deduplication
- TikTok posts: identified by `(username, post_id)` in the DB
- Instagram posts: identified by `("ig:<username>", post_id)` — the `ig:` prefix prevents collision with TikTok keys of the same username
- Each post ID is marked seen exactly once
- On first check of an account, all current posts are marked seen (prevents notification flood on startup)

### Post Age Filtering
- Only posts within `MAX_POST_AGE_HOURS = 48` are notified
- Prevents retroactive notifications when yt-dlp returns historical content
- Missing timestamps handled gracefully

### Frontend
- Dashboard uses dark theme (`#0a0e17` background)
- TikTok brand accent color: `#69C9D0`
- Vanilla JavaScript — no build step required
- Responsive layout with hamburger menu for mobile

---

## Running the Bot

```bash
# Install dependencies
pip install -r requirements.txt

# Start the polling bot
python main.py

# Start the dashboard (separate process or alongside main)
python dashboard.py
```

Or via systemd:
```bash
sudo systemctl start aymannoti
sudo systemctl status aymannoti
```

---

## Running the Dashboard

The dashboard server is started separately from the bot:

```bash
python dashboard.py
```

Access at `http://localhost:8080` (or configured host/port).

---

## Common Tasks for AI Assistants

### Adding a New API Endpoint
1. Add route to `dashboard.py` following existing patterns
2. Use `load_config()` / `save_config()` for config changes
3. Return JSON responses; use appropriate HTTP status codes
4. Handle errors gracefully — log them, return descriptive messages

### Modifying Polling Logic
- Edit `main.py` `run()` function
- Respect `MAX_POST_AGE_HOURS` and deduplication logic
- Any new per-cycle data should be persisted to `status.json` and `bot_log`

### Adding a New Database Table
1. Add `CREATE TABLE IF NOT EXISTS` in `Database.__init__` or `_init_db()`
2. Add corresponding methods to `Database` class
3. Use parameterized queries (never string interpolation)

### Modifying Discord Message Format
- Edit `notifier.py` `Notifier` class
- Test with `/api/test-webhook` endpoint before deploying

### Changing Config Schema
1. Update defaults in `config_helper.py` `load_config()`
2. Update `save_config()` if new fields need special serialization
3. Update `manage.py` and `dashboard.py` if the field is user-configurable
4. Document new field in this file under Configuration section

---

## Security Notes

- `config.yaml` contains **live Discord webhook URLs** — treat as secrets
- Do **not** commit `config.yaml` with real webhooks to public repositories
- The `.gitignore` does not currently exclude `config.yaml` — be cautious
- Webhook URLs contain embedded authentication tokens that should be rotated if exposed

---

## No Formal Test Suite

There is no automated test framework. Manual testing is done via:
- `GET /api/check/<username>` — validate a TikTok account exists
- `POST /api/test-webhook` — verify Discord webhook connectivity
- `POST /api/groups/<name>/accounts/<username>/test` — send a sample notification

When making changes, verify with these endpoints and inspect `aymannoti.log` for errors.
