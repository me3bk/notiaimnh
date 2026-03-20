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
    stats["total_accounts"] = sum(len(g.get("accounts", [])) for g in config.get("groups", []))
    stats["total_groups"] = len(config.get("groups", []))
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
        accounts = g.get("accounts", [])
        groups.append({
            "name": g["name"],
            "webhook_url": g.get("webhook_url", ""),
            "icon": g.get("icon", "📁"),
            "account_count": len(accounts),
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
                        "posts_seen": stats["posts_seen"],
                        "last_seen": stats["last_seen"],
                    })
                return jsonify(accounts)
    return jsonify({"error": "Group not found"}), 404


@app.route("/api/groups/<name>/accounts", methods=["POST"])
def api_add_accounts(name):
    data = request.get_json()
    raw = data.get("usernames", "")
    # Accept comma, newline, or space separated usernames
    usernames = [u.strip().lstrip("@") for u in re.split(r"[,\n\s]+", raw) if u.strip()]
    if not usernames:
        return jsonify({"error": "No usernames provided"}), 400

    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == name:
            existing = set(g.get("accounts", []))
            added = []
            skipped = []
            for u in usernames:
                if u in existing:
                    skipped.append(u)
                else:
                    g.setdefault("accounts", []).append(u)
                    existing.add(u)
                    added.append(u)
            save_config(config)
            return jsonify({"added": added, "skipped": skipped})
    return jsonify({"error": "Group not found"}), 404


@app.route("/api/groups/<name>/accounts/<username>", methods=["DELETE"])
def api_remove_account(name, username):
    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == name:
            clean = username.lstrip("@")
            before = len(g.get("accounts", []))
            g["accounts"] = [a for a in g.get("accounts", []) if a != clean]
            if len(g["accounts"]) == before:
                return jsonify({"error": "Account not found in group"}), 404
            save_config(config)
            return jsonify({"ok": True})
    return jsonify({"error": "Group not found"}), 404


# ── API: Test Post ──────────────────────────────────────────────


@app.route("/api/groups/<name>/accounts/<username>/test", methods=["POST"])
def api_test_post(name, username):
    """Fetch the latest post from a user and send it to the group's webhook."""
    clean = username.lstrip("@")

    # Find the group and its webhook
    config = load_config()
    webhook_url = None
    for g in config.get("groups", []):
        if g["name"] == name:
            webhook_url = g.get("webhook_url", "")
            break
    if not webhook_url:
        return jsonify({"error": "Group not found or no webhook configured"}), 404

    # Fetch the latest post
    url = f"https://www.tiktok.com/@{clean}"
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            result = ydl.extract_info(url, download=False)

        if not result or not result.get("entries"):
            return jsonify({"error": f"@{clean} has no posts to send"}), 404

        entries = [e for e in result.get("entries", []) if e]
        if not entries:
            return jsonify({"error": f"@{clean} has no posts to send"}), 404

        # Use the first (latest) entry
        entry = entries[0]
        vid = str(entry.get("id", ""))
        post_url = f"https://www.tiktok.com/@{clean}/video/{vid}" if vid else entry.get("url", "")
        bot_name = config.get("discord", {}).get("bot_name", "Aymannoti")
        msg_parts = [
            "@everyone",
            f"🧪 Test — New TikTok from @{clean}",
            f"@{clean} just posted a new video!",
            "",
            post_url
        ]

        payload = {
            "username": bot_name,
            "content": "\n".join(msg_parts),
        }

        resp = httpx.post(webhook_url, json=payload, timeout=15)
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 5)
            return jsonify({"error": f"Rate limited, try again in {retry_after}s"}), 429

        if resp.status_code in (200, 204):
            return jsonify({
                "ok": True,
                "message": f"Sent latest post from @{clean} to #{name}",
                "post_url": post_url,
            })
        resp.raise_for_status()
        return jsonify({"ok": True, "message": "Test post sent!"})

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if _ZERO_POSTS_HINT in msg:
            return jsonify({"error": f"@{clean} has no posts to send"}), 404
        if any(hint in msg for hint in _DELETED_HINTS):
            return jsonify({"error": "تعذر العثور على هذا الحساب"}), 404
        return jsonify({"error": msg[:200]}), 502
    except httpx.HTTPStatusError as e:
        return jsonify({"error": f"Discord returned {e.response.status_code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


# ── API: Check User ─────────────────────────────────────────────


@app.route("/api/check/<username>")
def api_check_user(username):
    """Check if a TikTok user exists and optionally has posts."""
    clean = username.lstrip("@")
    url = f"https://www.tiktok.com/@{clean}"
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            result = ydl.extract_info(url, download=False)

        if not result or not result.get("entries"):
            return jsonify({
                "status": "found",
                "username": clean,
                "post_count": 0,
                "latest_posts": [],
                "feed_title": f"@{clean}",
                "message": "حساب موجود — لا يوجد فيديوهات حالياً",
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
                "post_count": 0,
                "latest_posts": [],
                "feed_title": f"@{clean}",
                "message": "حساب موجود — لا يوجد فيديوهات حالياً",
            })
        if any(hint in msg for hint in _DELETED_HINTS):
            return jsonify({
                "status": "error",
                "username": clean,
                "message": "تعذر العثور على هذا الحساب",
            }), 404
        return jsonify({
            "status": "error",
            "username": clean,
            "message": msg[:200],
        }), 502

    except Exception as e:
        return jsonify({
            "status": "error",
            "username": clean,
            "message": str(e)[:200],
        }), 500


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
