#!/usr/bin/env python3
"""Aymannoti - TikTok Discord Notification Bot."""

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config_helper import load_config, BASE_DIR, VERSION
from database import Database
from poller import Poller
from notifier import Notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE_DIR / "aymannoti.log"),
    ],
)
logger = logging.getLogger("aymannoti")

DB_PATH = str(BASE_DIR / "aymannoti.db")

# Maximum age for a post to trigger a notification (in hours).
# Posts older than this are marked as seen silently — prevents
# notifications for months-old content that yt-dlp returns unexpectedly.
MAX_POST_AGE_HOURS = 48


def _is_recent_post(post: dict, max_age_hours: int = MAX_POST_AGE_HOURS) -> bool:
    """Check if a post is recent enough to send a notification for."""
    published = post.get("published")
    if not published:
        # No timestamp available — assume recent (benefit of the doubt)
        return True
    try:
        if isinstance(published, (int, float)):
            post_time = datetime.fromtimestamp(published, tz=timezone.utc)
        elif isinstance(published, str) and published.isdigit():
            post_time = datetime.fromtimestamp(int(published), tz=timezone.utc)
        elif isinstance(published, str) and len(published) == 8:
            # upload_date format: "20250309"
            post_time = datetime.strptime(published, "%Y%m%d").replace(tzinfo=timezone.utc)
        else:
            return True  # Unknown format — assume recent
        age = datetime.now(timezone.utc) - post_time
        return age < timedelta(hours=max_age_hours)
    except (ValueError, OSError):
        return True  # Parse error — assume recent


async def run():
    config = load_config()
    cookies = config.get("tiktok", {}).get("cookies_file", "")
    poller = Poller(cookies_file=cookies)
    bot_name = config.get("discord", {}).get("bot_name", "Aymannoti")
    notifier = Notifier(bot_name)

    interval = config["polling"]["interval_minutes"] * 60
    delay = config["polling"]["delay_between_requests"]

    total = sum(len(g.get("accounts", [])) for g in config.get("groups", []))
    logger.info(f"Aymannoti v{VERSION} started — tracking {total} accounts across {len(config.get('groups', []))} groups")

    # Log startup
    with Database(DB_PATH) as db:
        db.add_log("bot_started", "info", details=f"v{VERSION} — {total} accounts")

    cycle_number = 0

    try:
        while True:
            cycle_number += 1

            # Reload config each cycle so you can add accounts without restarting
            config = load_config()
            interval = config["polling"]["interval_minutes"] * 60
            delay = config["polling"]["delay_between_requests"]
            total = sum(len(g.get("accounts", [])) for g in config.get("groups", []))

            logger.info(f"── Cycle #{cycle_number} started — {total} accounts to check ──")
            cycle_start = time.monotonic()
            new_count = 0
            error_count = 0
            checked_count = 0
            skipped_old = 0
            error_details = []

            with Database(DB_PATH) as db:
                for group in config.get("groups", []):
                    webhook_url = group.get("webhook_url", "")
                    group_name = group.get("name", "Unknown")

                    if not webhook_url or webhook_url == "YOUR_WEBHOOK_URL_HERE":
                        logger.warning(f"Skipping group '{group_name}': no webhook configured")
                        continue

                    for account in group.get("accounts", []):
                        username = account if isinstance(account, str) else account.get("username", "")
                        if not username:
                            continue

                        checked_count += 1
                        account_start = time.monotonic()
                        try:
                            posts = await poller.fetch_feed(username)
                            first_check = not db.has_been_checked(username)
                            account_duration = round(time.monotonic() - account_start, 1)

                            if not posts:
                                logger.info(f"  @{username} — no posts found ({account_duration}s)")
                            else:
                                # Find unseen posts
                                unseen = [(i, p) for i, p in enumerate(posts) if not db.is_seen(username, p["id"])]

                                if first_check:
                                    # First time — mark all as seen, no notifications
                                    for _, post in unseen:
                                        db.mark_seen(username, post["id"], post["url"])
                                    logger.info(f"  @{username} — initial sync, marked {len(unseen)} posts as seen ({account_duration}s)")
                                elif unseen:
                                    # Only notify for the latest post (i == 0) if it's unseen AND recent
                                    first_idx, first_post = unseen[0]
                                    if first_idx == 0 and _is_recent_post(first_post):
                                        await notifier.send(webhook_url, username, first_post, group_name)
                                        new_count += 1
                                        logger.info(f"  @{username} — NEW POST sent! {first_post['url']} ({account_duration}s)")
                                    elif first_idx == 0:
                                        skipped_old += 1
                                        logger.info(f"  @{username} — latest post is too old, skipped notification ({account_duration}s)")
                                    else:
                                        skipped_old += len(unseen)
                                        logger.info(f"  @{username} — {len(unseen)} old unseen posts, skipped ({account_duration}s)")

                                    # Mark all unseen as seen (whether notified or not)
                                    for _, post in unseen:
                                        db.mark_seen(username, post["id"], post["url"])
                                else:
                                    logger.info(f"  @{username} — no new posts ({account_duration}s)")

                        except Exception as e:
                            account_duration = round(time.monotonic() - account_start, 1)
                            error_count += 1
                            error_details.append(f"@{username}: {str(e)[:80]}")
                            logger.error(f"  @{username} — ERROR: {e} ({account_duration}s)")

                        await asyncio.sleep(delay)

            # Cycle timing
            cycle_duration = round(time.monotonic() - cycle_start, 1)
            next_check = datetime.now() + timedelta(seconds=interval)
            next_check_str = next_check.strftime("%H:%M:%S")

            # Write status file
            now = datetime.now(timezone.utc).isoformat()
            status = {
                "last_cycle": now,
                "cycle_number": cycle_number,
                "accounts_checked": checked_count,
                "new_posts_last_cycle": new_count,
                "cycle_duration_seconds": cycle_duration,
                "interval_minutes": config["polling"]["interval_minutes"],
            }
            (BASE_DIR / "status.json").write_text(json.dumps(status))

            # Log cycle to database
            with Database(DB_PATH) as db:
                db.add_log(
                    event="cycle_complete",
                    level="warning" if error_count > 0 else "info",
                    accounts_checked=checked_count,
                    notifications_sent=new_count,
                    errors=error_count,
                    duration_seconds=cycle_duration,
                    details=("; ".join(error_details[:5]) if error_details else
                             f"Checked {checked_count} account(s) in {cycle_duration}s"),
                )

                if new_count > 0:
                    db.add_log(
                        event="notifications_sent",
                        level="info",
                        notifications_sent=new_count,
                        details=f"Sent {new_count} notification(s) this cycle",
                    )

                if error_count > 0:
                    db.add_log(
                        event="errors_occurred",
                        level="error",
                        errors=error_count,
                        details="; ".join(error_details[:5]),
                    )

            # Summary log
            logger.info(
                f"── Cycle #{cycle_number} complete ──\n"
                f"    Accounts checked : {checked_count}/{total}\n"
                f"    New posts sent   : {new_count}\n"
                f"    Old posts skipped: {skipped_old}\n"
                f"    Errors           : {error_count}\n"
                f"    Cycle duration   : {cycle_duration}s\n"
                f"    Next check at    : {next_check_str} (in {config['polling']['interval_minutes']} min)"
            )
            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await notifier.close()
        with Database(DB_PATH) as db:
            db.add_log("bot_stopped", "info", details=f"Graceful shutdown after {cycle_number} cycles")


if __name__ == "__main__":
    asyncio.run(run())
