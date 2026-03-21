#!/usr/bin/env python3
"""Aymannoti — Web dashboard for managing TikTok notifications."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yt_dlp
from flask import Flask, render_template, request, jsonify

from config_helper import load_config, save_config, BASE_DIR, VERSION
from database import Database

app = Flask(__name__)

DB_PATH = BASE_DIR / "aymannoti.db"
STATUS_PATH = BASE_DIR / "status.json"
TIKTOK_COLOR = 0x69C9D0

# Hints from yt-dlp error messages
_ZERO_POSTS_HINT = "does not have any videos"
_DELETED_HINTS = ("Unable to extract", "does not exist", "404")

YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": "in_playlist",
    "playlistend": 5,
    "socket_timeout": 20,
}


# ── Pages ───────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("dashboard.html", version=VERSION)


# ── API: Stats & Status ────────────────────────────────────────


@app.route("/api/stats")
def api_stats():
    with Database(str(DB_PATH)) as db:
        stats = db.get_stats()
    config = load_config()
    groups = config.get("groups", [])
    stats["total_accounts"] = sum(len(g.get("accounts", [])) for g in groups)
    stats["total_instagram_accounts"] = sum(len(g.get("instagram_accounts", [])) for g in groups)
    stats["total_groups"] = len(groups)
    return jsonify(stats)


@app.route("/api/status")
def api_status():
    if STATUS_PATH.exists():
        data = json.loads(STATUS_PATH.read_text())
        return jsonify({"running": True, **data})
    return jsonify({"running": False})


@app.route("/api/version")
def api_version():
    return jsonify({"version": VERSION})


# ── API: Groups ─────────────────────────────────────────────────


@app.route("/api/groups")
def api_list_groups():
    config = load_config()
    groups = []
    for g in config.get("groups", []):
        groups.append({
            "name": g["name"],
            "webhook_url": g.get("webhook_url", ""),
            "icon": g.get("icon", "📁"),
            "account_count": len(g.get("accounts", [])),
            "instagram_account_count": len(g.get("instagram_accounts", [])),
        })
    return jsonify(groups)


@app.route("/api/groups", methods=["POST"])
def api_create_group():
    data = request.get_json()
    name = data.get("name", "").strip()
    webhook = data.get("webhook_url", "").strip()
    icon = data.get("icon", "📁").strip()
    if not name:
        return jsonify({"error": "Group name is required"}), 400
    if not webhook:
        return jsonify({"error": "Webhook URL is required"}), 400

    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == name:
            return jsonify({"error": f"Group '{name}' already exists"}), 409

    config.setdefault("groups", []).append({
        "name": name,
        "webhook_url": webhook,
        "icon": icon,
        "accounts": [],
    })
    save_config(config)
    return jsonify({"ok": True, "name": name}), 201


@app.route("/api/groups/<name>", methods=["PUT"])
def api_edit_group(name):
    data = request.get_json()
    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == name:
            if "webhook_url" in data:
                g["webhook_url"] = data["webhook_url"].strip()
            if "icon" in data:
                g["icon"] = data["icon"].strip()
            if "new_name" in data and data["new_name"].strip():
                new_name = data["new_name"].strip()
                # Check for name conflicts
                if new_name != name:
                    for other in config.get("groups", []):
                        if other["name"] == new_name:
                            return jsonify({"error": f"Group '{new_name}' already exists"}), 409
                    g["name"] = new_name
            save_config(config)
            return jsonify({"ok": True, "name": g["name"]})
    return jsonify({"error": "Group not found"}), 404


@app.route("/api/groups/<name>", methods=["DELETE"])
def api_delete_group(name):
    config = load_config()
    before = len(config.get("groups", []))
    config["groups"] = [g for g in config.get("groups", []) if g["name"] != name]
    if len(config["groups"]) == before:
        return jsonify({"error": "Group not found"}), 404
    save_config(config)
    return jsonify({"ok": True})


# ── API: Accounts ───────────────────────────────────────────────


@app.route("/api/groups/<name>/accounts")
def api_list_accounts(name):
    config = load_config()
    with Database(str(DB_PATH)) as db:
        for g in config.get("groups", []):
            if g["name"] == name:
                accounts = []
                for username in g.get("accounts", []):
                    stats = db.get_user_stats(username)
                    accounts.append({
                        "username": username,
                        "platform": "tiktok",
                        "posts_seen": stats["posts_seen"],
                        "last_seen": stats["last_seen"],
                    })
                for username in g.get("instagram_accounts", []):
                    stats = db.get_user_stats(f"ig:{username}")
                    accounts.append({
                        "username": username,
                        "platform": "instagram",
                        "posts_seen": stats["posts_seen"],
                        "last_seen": stats["last_seen"],
                    })
                return jsonify(accounts)
    return jsonify({"error": "Group not found"}), 404


@app.route("/api/groups/<name>/accounts", methods=["POST"])
def api_add_accounts(name):
    data = request.get_json()
    raw = data.get("usernames", "")
    platform = data.get("platform", "tiktok").lower()
    # Accept comma, newline, or space separated usernames
    usernames = [u.strip().lstrip("@") for u in re.split(r"[,\n\s]+", raw) if u.strip()]
    if not usernames:
        return jsonify({"error": "No usernames provided"}), 400
    if platform not in ("tiktok", "instagram"):
        return jsonify({"error": "platform must be 'tiktok' or 'instagram'"}), 400

    field = "accounts" if platform == "tiktok" else "instagram_accounts"
    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == name:
            existing = set(g.get(field, []))
            added = []
            skipped = []
            for u in usernames:
                if u in existing:
                    skipped.append(u)
                else:
                    g.setdefault(field, []).append(u)
                    existing.add(u)
                    added.append(u)
            save_config(config)
            return jsonify({"added": added, "skipped": skipped, "platform": platform})
    return jsonify({"error": "Group not found"}), 404


@app.route("/api/groups/<name>/accounts/<username>", methods=["DELETE"])
def api_remove_account(name, username):
    platform = request.args.get("platform", "tiktok").lower()
    field = "accounts" if platform == "tiktok" else "instagram_accounts"
    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == name:
            clean = username.lstrip("@")
            before = len(g.get(field, []))
            g[field] = [a for a in g.get(field, []) if a != clean]
            if len(g.get(field, [])) == before:
                return jsonify({"error": "Account not found in group"}), 404
            save_config(config)
            return jsonify({"ok": True})
    return jsonify({"error": "Group not found"}), 404


# ── API: Test Post ──────────────────────────────────────────────


@app.route("/api/groups/<name>/accounts/<username>/test", methods=["POST"])
def api_test_post(name, username):
    """Fetch the latest post from a user and send it to the group's webhook.
    Query param: ?platform=tiktok (default) or ?platform=instagram
    """
    platform = request.args.get("platform", "tiktok").lower()
    clean = username.lstrip("@")

    config = load_config()
    webhook_url = None
    for g in config.get("groups", []):
        if g["name"] == name:
            webhook_url = g.get("webhook_url", "")
            break
    if not webhook_url:
        return jsonify({"error": "Group not found or no webhook configured"}), 404

    bot_name = config.get("discord", {}).get("bot_name", "Aymannoti")

    if platform == "instagram":
        return _test_instagram_post(clean, name, webhook_url, bot_name, config)
    return _test_tiktok_post(clean, name, webhook_url, bot_name)


def _test_tiktok_post(clean, group_name, webhook_url, bot_name):
    url = f"https://www.tiktok.com/@{clean}"
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            result = ydl.extract_info(url, download=False)
        if not result or not result.get("entries"):
            return jsonify({"error": f"@{clean} has no posts to send"}), 404
        entries = [e for e in result.get("entries", []) if e]
        if not entries:
            return jsonify({"error": f"@{clean} has no posts to send"}), 404
        entry = entries[0]
        vid = str(entry.get("id", ""))
        post_url = f"https://www.tiktok.com/@{clean}/video/{vid}" if vid else entry.get("url", "")
        payload = {
            "username": bot_name,
            "content": "\n".join([
                "@everyone",
                f"Test — New TikTok from @{clean}",
                f"@{clean} just posted a new video!",
                "",
                post_url,
            ]),
        }
        return _send_test_webhook(webhook_url, payload, clean, group_name, post_url)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if _ZERO_POSTS_HINT in msg:
            return jsonify({"error": f"@{clean} has no posts to send"}), 404
        if any(hint in msg for hint in _DELETED_HINTS):
            return jsonify({"error": "Account not found"}), 404
        return jsonify({"error": msg[:200]}), 502
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


def _test_instagram_post(clean, group_name, webhook_url, bot_name, config):
    ig_cfg = config.get("instagram", {})
    opts = {**YDL_OPTS}
    if ig_cfg.get("cookies_file"):
        opts["cookiefile"] = ig_cfg["cookies_file"]
    if ig_cfg.get("username"):
        opts["username"] = ig_cfg["username"]
    if ig_cfg.get("password"):
        opts["password"] = ig_cfg["password"]

    url = f"https://www.instagram.com/{clean}/"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(url, download=False)
        if not result or not result.get("entries"):
            return jsonify({"error": f"@{clean} has no Instagram posts to send"}), 404
        entries = [e for e in result.get("entries", []) if e]
        if not entries:
            return jsonify({"error": f"@{clean} has no Instagram posts to send"}), 404
        entry = entries[0]
        vid = str(entry.get("id", ""))
        post_url = (
            entry.get("webpage_url")
            or entry.get("url")
            or f"https://www.instagram.com/p/{vid}/"
        )
        post_type = "Reel" if "/reel/" in post_url else "Post"
        payload = {
            "username": bot_name,
            "content": "\n".join([
                "@everyone",
                f"Test — New Instagram {post_type} from @{clean}",
                f"@{clean} just posted a new {post_type.lower()} on Instagram!",
                "",
                post_url,
            ]),
        }
        return _send_test_webhook(webhook_url, payload, clean, group_name, post_url)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if "private" in msg or "login" in msg or "checkpoint" in msg:
            return jsonify({"error": "Account is private or cookies are required"}), 404
        if "does not exist" in msg or "404" in msg or "sorry" in msg:
            return jsonify({"error": "Instagram account not found"}), 404
        return jsonify({"error": str(e)[:200]}), 502
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


def _send_test_webhook(webhook_url, payload, clean, group_name, post_url):
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=15)
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 5)
            return jsonify({"error": f"Rate limited, try again in {retry_after}s"}), 429
        if resp.status_code in (200, 204):
            return jsonify({"ok": True, "message": f"Sent latest post from @{clean} to #{group_name}", "post_url": post_url})
        resp.raise_for_status()
        return jsonify({"ok": True, "message": "Test post sent!"})
    except httpx.HTTPStatusError as e:
        return jsonify({"error": f"Discord returned {e.response.status_code}"}), 502


# ── API: Check User ─────────────────────────────────────────────


@app.route("/api/check/<username>")
def api_check_user(username):
    """Check if a TikTok or Instagram user exists and optionally has posts.
    Query param: ?platform=tiktok (default) or ?platform=instagram
    """
    platform = request.args.get("platform", "tiktok").lower()
    clean = username.lstrip("@")

    if platform == "instagram":
        return _check_instagram_user(clean)
    return _check_tiktok_user(clean)


def _check_tiktok_user(clean: str):
    url = f"https://www.tiktok.com/@{clean}"
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            result = ydl.extract_info(url, download=False)

        if not result or not result.get("entries"):
            return jsonify({
                "status": "found",
                "username": clean,
                "platform": "tiktok",
                "post_count": 0,
                "latest_posts": [],
                "feed_title": f"@{clean}",
                "message": "Account found — no videos yet",
            })

        entries = [e for e in result.get("entries", []) if e]
        posts = []
        for entry in entries[:5]:
            vid = str(entry.get("id", ""))
            posts.append({
                "title": (entry.get("title") or "")[:100],
                "url": f"https://www.tiktok.com/@{clean}/video/{vid}" if vid else entry.get("url", ""),
                "published": "",
            })
        return jsonify({
            "status": "found",
            "username": clean,
            "platform": "tiktok",
            "feed_title": result.get("title", f"@{clean}"),
            "post_count": len(entries),
            "latest_posts": posts,
        })

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if _ZERO_POSTS_HINT in msg:
            return jsonify({
                "status": "found",
                "username": clean,
                "platform": "tiktok",
                "post_count": 0,
                "latest_posts": [],
                "feed_title": f"@{clean}",
                "message": "Account found — no videos yet",
            })
        if any(hint in msg for hint in _DELETED_HINTS):
            return jsonify({"status": "error", "username": clean, "message": "Account not found"}), 404
        return jsonify({"status": "error", "username": clean, "message": msg[:200]}), 502
    except Exception as e:
        return jsonify({"status": "error", "username": clean, "message": str(e)[:200]}), 500


def _check_instagram_user(clean: str):
    config = load_config()
    ig_cfg = config.get("instagram", {})
    opts = {**YDL_OPTS}
    if ig_cfg.get("cookies_file"):
        opts["cookiefile"] = ig_cfg["cookies_file"]
    if ig_cfg.get("username"):
        opts["username"] = ig_cfg["username"]
    if ig_cfg.get("password"):
        opts["password"] = ig_cfg["password"]

    url = f"https://www.instagram.com/{clean}/"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(url, download=False)

        if not result or not result.get("entries"):
            return jsonify({
                "status": "found",
                "username": clean,
                "platform": "instagram",
                "post_count": 0,
                "latest_posts": [],
                "feed_title": f"@{clean}",
                "message": "Account found — no posts yet",
            })

        entries = [e for e in result.get("entries", []) if e]
        posts = []
        for entry in entries[:5]:
            vid = str(entry.get("id", ""))
            post_url = (
                entry.get("webpage_url")
                or entry.get("url")
                or f"https://www.instagram.com/p/{vid}/"
            )
            post_type = "reel" if "/reel/" in post_url else "post"
            posts.append({
                "title": (entry.get("title") or "")[:100],
                "url": post_url,
                "post_type": post_type,
                "published": "",
            })
        return jsonify({
            "status": "found",
            "username": clean,
            "platform": "instagram",
            "feed_title": result.get("title", f"@{clean}"),
            "post_count": len(entries),
            "latest_posts": posts,
        })

    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if any(k in msg for k in ("private", "login", "checkpoint", "does not exist", "404", "sorry")):
            status_msg = "Account is private or requires login" if "private" in msg or "login" in msg else "Account not found"
            return jsonify({"status": "error", "username": clean, "platform": "instagram", "message": status_msg}), 404
        return jsonify({"status": "error", "username": clean, "platform": "instagram", "message": str(e)[:200]}), 502
    except Exception as e:
        return jsonify({"status": "error", "username": clean, "platform": "instagram", "message": str(e)[:200]}), 500


# ── API: Test Webhook ───────────────────────────────────────────


@app.route("/api/test-webhook", methods=["POST"])
def api_test_webhook():
    data = request.get_json()
    webhook_url = data.get("webhook_url", "").strip()
    if not webhook_url:
        return jsonify({"error": "Webhook URL is required"}), 400

    bot_name = load_config().get("discord", {}).get("bot_name", "Aymannoti")
    embed = {
        "title": "Test Notification from Aymannoti",
        "description": "If you see this, your webhook is working correctly!",
        "color": TIKTOK_COLOR,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"Test \u2022 {bot_name}"},
    }
    payload = {"username": bot_name, "embeds": [embed]}

    try:
        resp = httpx.post(webhook_url, json=payload, timeout=15)
        if resp.status_code == 204:
            return jsonify({"ok": True, "message": "Test notification sent!"})
        resp.raise_for_status()
        return jsonify({"ok": True, "message": "Test notification sent!"})
    except httpx.HTTPStatusError as e:
        return jsonify({"error": f"Discord returned {e.response.status_code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── API: Logs ───────────────────────────────────────────────────


@app.route("/api/logs")
def api_logs():
    limit = request.args.get("limit", 100, type=int)
    with Database(str(DB_PATH)) as db:
        logs = db.get_logs(min(limit, 500))
    return jsonify(logs)


@app.route("/api/logs/summary")
def api_logs_summary():
    with Database(str(DB_PATH)) as db:
        summary = db.get_log_summary()
    return jsonify(summary)


@app.route("/api/logs", methods=["DELETE"])
def api_clear_logs():
    with Database(str(DB_PATH)) as db:
        db.clear_logs()
    return jsonify({"ok": True})


# ── API: Health ─────────────────────────────────────────────────


@app.route("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ── Run ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    config = load_config()
    dash = config.get("dashboard", {})
    host = dash.get("host", "0.0.0.0")
    port = dash.get("port", 8080)
    print(f"Aymannoti v{VERSION} Dashboard running at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
