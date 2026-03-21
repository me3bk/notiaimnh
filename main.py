#!/usr/bin/env python3
"""Aymannoti - TikTok & Instagram Discord Notification Bot."""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config_helper import load_config, BASE_DIR, VERSION
from database import Database
from poller import Poller
from instagram_poller import InstagramPoller
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
MAX_POST_AGE_HOURS = 48


def _is_recent_post(post: dict, max_age_hours: int = MAX_POST_AGE_HOURS) -> bool:
    """Check if a post is recent enough to send a notification for."""
    published = post.get("published")
    if not published:
        return True
    try:
        if isinstance(published, (int, float)):
            post_time = datetime.fromtimestamp(published, tz=timezone.utc)
        elif isinstance(published, str) and published.isdigit():
            post_time = datetime.fromtimestamp(int(published), tz=timezone.utc)
        elif isinstance(published, str) and len(published) == 8:
            post_time = datetime.strptime(published, "%Y%m%d").replace(tzinfo=timezone.utc)
        else:
            return True
        age = datetime.now(timezone.utc) - post_time
        return age < timedelta(hours=max_age_hours)
    except (ValueError, OSError):
        return True


async def _poll_account(
    username: str,
    db_key: str,
    platform: str,
    webhook_url: str,
    group_name: str,
    poller,
    notifier: Notifier,
    delay: float,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Poll one account (TikTok or Instagram) under the concurrency semaphore.

    Returns a stats dict: {new, skipped, error}.
    Each task opens its own DB connection so concurrent writes are safe
    (SQLite WAL mode serialises writes, timeout=30 handles lock contention).
    """
    async with semaphore:
        prefix = "[IG] " if platform == "instagram" else ""
        account_start = time.monotonic()
        result: dict = {"new": 0, "skipped": 0, "error": None}

        try:
            posts = await poller.fetch_feed(username)
            with Database(DB_PATH) as db:
                first_check = not db.has_been_checked(db_key)
                account_duration = round(time.monotonic() - account_start, 1)

                if not posts:
                    logger.info(f"  {prefix}@{username} — no posts found ({account_duration}s)")
                else:
                    unseen = [
                        (i, p) for i, p in enumerate(posts)
                        if not db.is_seen(db_key, p["id"])
                    ]

                    if first_check:
                        for _, post in unseen:
                            db.mark_seen(db_key, post["id"], post["url"])
                        logger.info(
                            f"  {prefix}@{username} — initial sync, "
                            f"marked {len(unseen)} posts as seen ({account_duration}s)"
                        )
                    elif unseen:
                        first_idx, first_post = unseen[0]
                        if first_idx == 0 and _is_recent_post(first_post):
                            await notifier.send(
                                webhook_url, username, first_post,
                                group_name, platform=platform,
                            )
                            result["new"] = 1
                            logger.info(
                                f"  {prefix}@{username} — NEW POST sent! "
                                f"{first_post['url']} ({account_duration}s)"
                            )
                        elif first_idx == 0:
                            result["skipped"] = 1
                            logger.info(
                                f"  {prefix}@{username} — latest post is too old, "
                                f"skipped ({account_duration}s)"
                            )
                        else:
                            result["skipped"] = len(unseen)
                            logger.info(
                                f"  {prefix}@{username} — {len(unseen)} old unseen posts, "
                                f"skipped ({account_duration}s)"
                            )

                        for _, post in unseen:
                            db.mark_seen(db_key, post["id"], post["url"])
                    else:
                        logger.info(f"  {prefix}@{username} — no new posts ({account_duration}s)")

        except Exception as e:
            account_duration = round(time.monotonic() - account_start, 1)
            result["error"] = f"{prefix}@{username}: {str(e)[:80]}"
            logger.error(f"  {prefix}@{username} — ERROR: {e} ({account_duration}s)")

        await asyncio.sleep(delay)
        return result


async def run():
    config = load_config()
    cookies = config.get("tiktok", {}).get("cookies_file", "")
    ig_cfg = config.get("instagram", {})
    ig_cookies = ig_cfg.get("cookies_file", "")
    ig_username = ig_cfg.get("username", "")
    ig_password = ig_cfg.get("password", "")

    poller = Poller(cookies_file=cookies)
    ig_poller = InstagramPoller(
        cookies_file=ig_cookies, username=ig_username, password=ig_password
    )
    bot_name = config.get("discord", {}).get("bot_name", "Aymannoti")
    notifier = Notifier(bot_name)

    interval = config["polling"]["interval_minutes"] * 60
    delay = config["polling"]["delay_between_requests"]
    concurrent = config["polling"].get("concurrent_requests", 5)
    ig_concurrent = config["polling"].get("instagram_concurrent_requests", 1)

    groups = config.get("groups", [])
    total_tiktok = sum(len(g.get("accounts", [])) for g in groups)
    total_ig = sum(len(g.get("instagram_accounts", [])) for g in groups)
    total = total_tiktok + total_ig
    logger.info(
        f"Aymannoti v{VERSION} started — "
        f"tracking {total_tiktok} TikTok + {total_ig} Instagram accounts "
        f"across {len(groups)} groups  |  "
        f"concurrency: TikTok={concurrent}, Instagram={ig_concurrent}"
    )

    with Database(DB_PATH) as db:
        db.add_log("bot_started", "info", details=f"v{VERSION} — {total} accounts")

    cycle_number = 0

    try:
        while True:
            cycle_number += 1

            # Reload config each cycle for hot-reload
            config = load_config()
            interval = config["polling"]["interval_minutes"] * 60
            delay = config["polling"]["delay_between_requests"]
            concurrent = config["polling"].get("concurrent_requests", 5)
            ig_concurrent = config["polling"].get("instagram_concurrent_requests", 1)
            groups = config.get("groups", [])

            total_tiktok = sum(len(g.get("accounts", [])) for g in groups)
            total_ig = sum(len(g.get("instagram_accounts", [])) for g in groups)
            total = total_tiktok + total_ig

            # Hot-reload Instagram auth
            _ig = config.get("instagram", {})
            ig_poller.update_cookies(
                _ig.get("cookies_file", ""),
                _ig.get("username", ""),
                _ig.get("password", ""),
            )

            logger.info(
                f"── Cycle #{cycle_number} started — "
                f"{total} accounts to check "
                f"(TikTok concurrency={concurrent}, Instagram concurrency={ig_concurrent}) ──"
            )
            cycle_start = time.monotonic()

            # Separate semaphores: TikTok can run many in parallel,
            # Instagram must be sequential (or near-sequential) to avoid
            # session-based checkpoint/block from its anti-bot detection.
            semaphore = asyncio.Semaphore(concurrent)
            ig_semaphore = asyncio.Semaphore(ig_concurrent)
            tasks = []

            for group in groups:
                webhook_url = group.get("webhook_url", "")
                group_name = group.get("name", "Unknown")

                if not webhook_url or webhook_url == "YOUR_WEBHOOK_URL_HERE":
                    logger.warning(f"Skipping group '{group_name}': no webhook configured")
                    continue

                for account in group.get("accounts", []):
                    username = (
                        account if isinstance(account, str)
                        else account.get("username", "")
                    )
                    if not username:
                        continue
                    tasks.append(_poll_account(
                        username, username, "tiktok",
                        webhook_url, group_name,
                        poller, notifier, delay, semaphore,
                    ))

                for ig_user in group.get("instagram_accounts", []):
                    ig_user = (
                        ig_user.lstrip("@") if isinstance(ig_user, str)
                        else ig_user.get("username", "")
                    )
                    if not ig_user:
                        continue
                    tasks.append(_poll_account(
                        ig_user, f"ig:{ig_user}", "instagram",
                        webhook_url, group_name,
                        ig_poller, notifier, delay, ig_semaphore,
                    ))

            # Run all polls concurrently (bounded by semaphore)
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Aggregate stats
            new_count = 0
            skipped_old = 0
            error_count = 0
            error_details = []
            checked_count = len(tasks)

            for res in raw_results:
                if isinstance(res, Exception):
                    error_count += 1
                    error_details.append(str(res)[:80])
                else:
                    new_count += res.get("new", 0)
                    skipped_old += res.get("skipped", 0)
                    if res.get("error"):
                        error_count += 1
                        error_details.append(res["error"])

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
                    details=(
                        "; ".join(error_details[:5]) if error_details
                        else f"Checked {checked_count} account(s) in {cycle_duration}s"
                    ),
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

            logger.info(
                f"── Cycle #{cycle_number} complete ──\n"
                f"    Accounts checked : {checked_count}/{total}\n"
                f"    New posts sent   : {new_count}\n"
                f"    Old posts skipped: {skipped_old}\n"
                f"    Errors           : {error_count}\n"
                f"    Cycle duration   : {cycle_duration}s\n"
                f"    Next check at    : {next_check_str} "
                f"(in {config['polling']['interval_minutes']} min)"
            )
            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await notifier.close()
        with Database(DB_PATH) as db:
            db.add_log(
                "bot_stopped", "info",
                details=f"Graceful shutdown after {cycle_number} cycles",
            )


if __name__ == "__main__":
    asyncio.run(run())
