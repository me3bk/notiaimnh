#!/usr/bin/env python3
"""CLI tool for managing Aymannoti groups and accounts."""

import argparse
import getpass
import sys
from pathlib import Path

from config_helper import load_config, save_config, BASE_DIR, VERSION


# ── Group commands ──────────────────────────────────────────────


def group_add(args):
    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == args.name:
            print(f"Group '{args.name}' already exists.")
            return
    config.setdefault("groups", []).append(
        {"name": args.name, "webhook_url": args.webhook, "accounts": []}
    )
    save_config(config)
    print(f"Added group '{args.name}'")


def group_list(_args):
    config = load_config()
    groups = config.get("groups", [])
    if not groups:
        print("No groups configured. Add one with:  python manage.py group add <name> <webhook_url>")
        return
    for g in groups:
        count = len(g.get("accounts", []))
        webhook = g.get("webhook_url", "N/A")
        print(f"  {g['name']}  ({count} accounts)  ->  {webhook[:60]}...")


def group_remove(args):
    config = load_config()
    before = len(config.get("groups", []))
    config["groups"] = [g for g in config.get("groups", []) if g["name"] != args.name]
    if len(config["groups"]) == before:
        print(f"Group '{args.name}' not found.")
        return
    save_config(config)
    print(f"Removed group '{args.name}'")


# ── Account commands ────────────────────────────────────────────


def account_add(args):
    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == args.group:
            existing = set(g.get("accounts", []))
            added = []
            for u in args.usernames:
                clean = u.lstrip("@")
                if clean in existing:
                    print(f"  @{clean} already in '{args.group}'")
                else:
                    g.setdefault("accounts", []).append(clean)
                    existing.add(clean)
                    added.append(clean)
            save_config(config)
            if added:
                print(f"Added {len(added)} account(s) to '{args.group}': {', '.join('@' + u for u in added)}")
            return
    print(f"Group '{args.group}' not found. Create it first:")
    print(f'  python manage.py group add "{args.group}" <webhook_url>')


def account_remove(args):
    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == args.group:
            clean = args.username.lstrip("@")
            before = len(g.get("accounts", []))
            g["accounts"] = [a for a in g.get("accounts", []) if a != clean]
            if len(g["accounts"]) == before:
                print(f"@{clean} not found in '{args.group}'")
                return
            save_config(config)
            print(f"Removed @{clean} from '{args.group}'")
            return
    print(f"Group '{args.group}' not found.")


def account_list(_args):
    config = load_config()
    total = 0
    for g in config.get("groups", []):
        accounts = g.get("accounts", [])
        total += len(accounts)
        print(f"\n[{g['name']}] ({len(accounts)} accounts)")
        for a in accounts:
            print(f"  @{a}")
    print(f"\nTotal: {total} accounts")


# ── Import command ──────────────────────────────────────────────


def account_import(args):
    """Bulk-import usernames from a text file (one per line)."""
    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        return
    usernames = [
        line.strip().lstrip("@")
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not usernames:
        print("No usernames found in file.")
        return
    # Reuse account_add logic
    args.usernames = usernames
    account_add(args)


# ── Instagram account commands ──────────────────────────────────


def instagram_add(args):
    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == args.group:
            existing = set(g.get("instagram_accounts", []))
            added = []
            for u in args.usernames:
                clean = u.lstrip("@")
                if clean in existing:
                    print(f"  @{clean} already in '{args.group}' (Instagram)")
                else:
                    g.setdefault("instagram_accounts", []).append(clean)
                    existing.add(clean)
                    added.append(clean)
            save_config(config)
            if added:
                print(f"Added {len(added)} Instagram account(s) to '{args.group}': {', '.join('@' + u for u in added)}")
            return
    print(f"Group '{args.group}' not found. Create it first:")
    print(f'  python manage.py group add "{args.group}" <webhook_url>')


def instagram_remove(args):
    config = load_config()
    for g in config.get("groups", []):
        if g["name"] == args.group:
            clean = args.username.lstrip("@")
            before = len(g.get("instagram_accounts", []))
            g["instagram_accounts"] = [a for a in g.get("instagram_accounts", []) if a != clean]
            if len(g.get("instagram_accounts", [])) == before:
                print(f"@{clean} not found in '{args.group}' (Instagram)")
                return
            save_config(config)
            print(f"Removed Instagram @{clean} from '{args.group}'")
            return
    print(f"Group '{args.group}' not found.")


def instagram_list(_args):
    config = load_config()
    total = 0
    for g in config.get("groups", []):
        accounts = g.get("instagram_accounts", [])
        total += len(accounts)
        if accounts:
            print(f"\n[{g['name']}] ({len(accounts)} Instagram accounts)")
            for a in accounts:
                print(f"  @{a}")
    print(f"\nTotal Instagram accounts: {total}")


def instagram_import(args):
    """Bulk-import Instagram usernames from a text file (one per line)."""
    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        return
    usernames = [
        line.strip().lstrip("@")
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not usernames:
        print("No usernames found in file.")
        return
    args.usernames = usernames
    instagram_add(args)


# ── Instagram cookie setup ──────────────────────────────────────

_SUPPORTED_BROWSERS = ("chrome", "firefox", "edge", "safari", "chromium", "brave", "opera")

_COOKIE_FILE_DEFAULT = str(BASE_DIR / "instagram_cookies.txt")


def instagram_setup_cookies(args):
    """
    Obtain Instagram cookies via one of two methods and save them to a file.

    Method A (--browser):  read cookies from a locally installed browser that
                           is already logged into Instagram (most reliable).
    Method B (--username): attempt a credential login through yt-dlp and save
                           the resulting session cookies to a file.
                           NOTE: Instagram heavily restricts programmatic login
                           since 2023 — this may fail or trigger 2FA/checkpoint.
    """
    try:
        import yt_dlp
    except ImportError:
        print("ERROR: yt-dlp is not installed. Run:  pip install yt-dlp")
        return

    output_file = args.output or _COOKIE_FILE_DEFAULT

    if args.browser:
        _setup_via_browser(yt_dlp, args.browser.lower(), output_file)
    elif args.username:
        password = args.password or getpass.getpass(f"Instagram password for @{args.username}: ")
        _setup_via_credentials(yt_dlp, args.username, password, output_file)
    else:
        print("Specify either --browser or --username.  Examples:")
        print("  python manage.py instagram setup-cookies --browser chrome")
        print("  python manage.py instagram setup-cookies --username myaccount")
        print(f"  Supported browsers: {', '.join(_SUPPORTED_BROWSERS)}")
        return


def _setup_via_browser(yt_dlp, browser: str, output_file: str):
    """Extract Instagram cookies from a locally installed browser."""
    if browser not in _SUPPORTED_BROWSERS:
        print(f"Unknown browser '{browser}'. Supported: {', '.join(_SUPPORTED_BROWSERS)}")
        return

    print(f"Extracting Instagram cookies from {browser} ...")
    print("Make sure you are logged into Instagram in that browser.")

    opts = {
        "cookiesfrombrowser": (browser,),
        "cookiefile": output_file,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "socket_timeout": 20,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            # Use Instagram's own verified account as a lightweight test URL
            ydl.extract_info("https://www.instagram.com/instagram/", download=False)
        _save_and_update_config(output_file)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "login" in msg.lower() or "checkpoint" in msg.lower():
            # Cookies were exported even if the page failed — check if file exists
            if Path(output_file).exists() and Path(output_file).stat().st_size > 100:
                print("WARNING: Instagram page returned a login challenge, but cookies were saved.")
                print("The cookies may still work — try running the bot.")
                _save_and_update_config(output_file)
            else:
                print(f"Failed — cookies not saved. Make sure you are logged into Instagram in {browser}.")
                print(f"Detail: {msg[:200]}")
        else:
            print(f"Failed to extract cookies: {msg[:200]}")
    except Exception as e:
        print(f"Unexpected error: {e}")


def _setup_via_credentials(yt_dlp, username: str, password: str, output_file: str):
    """
    Attempt a credential-based login via yt-dlp and save session cookies.
    Instagram blocks most programmatic logins — may require handling 2FA
    or checkpoint in browser first.
    """
    print(f"Attempting Instagram login for @{username} ...")
    print("NOTE: Instagram may block this or ask for verification.")

    opts = {
        "username": username,
        "password": password,
        "cookiefile": output_file,
        "quiet": False,   # show yt-dlp output so user can see 2FA prompts
        "no_warnings": False,
        "skip_download": True,
        "extract_flat": True,
        "socket_timeout": 30,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(f"https://www.instagram.com/{username}/", download=False)
        _save_and_update_config(output_file)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "checkpoint" in msg.lower():
            print("\nInstagram triggered a security checkpoint.")
            print("Open Instagram in your browser, complete the verification,")
            print("then re-run with --browser instead.")
        elif "two" in msg.lower() or "2fa" in msg.lower() or "code" in msg.lower():
            print("\nInstagram requires two-factor authentication.")
            print("Use --browser method instead — log in manually then extract cookies.")
        elif Path(output_file).exists() and Path(output_file).stat().st_size > 100:
            # Login triggered an error but cookies were saved (partial session)
            print(f"Login error but cookies file was created — may still work.")
            print(f"Detail: {msg[:200]}")
            _save_and_update_config(output_file)
        else:
            print(f"Login failed: {msg[:200]}")
            print("Try the --browser method instead.")
    except Exception as e:
        print(f"Unexpected error: {e}")


def _save_and_update_config(output_file: str):
    """Print success message and update config.yaml with the cookie file path."""
    if not Path(output_file).exists():
        print(f"ERROR: Cookie file was not created at {output_file}")
        return
    size = Path(output_file).stat().st_size
    print(f"\nCookies saved to: {output_file}  ({size} bytes)")

    config = load_config()
    config.setdefault("instagram", {})["cookies_file"] = output_file
    save_config(config)
    print(f"config.yaml updated: instagram.cookies_file = {output_file}")
    print("\nYou can now start the bot. Cookies will be reloaded automatically each cycle.")


# ── CLI setup ───────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description=f"Aymannoti v{VERSION} — manage TikTok notification groups and accounts"
    )
    sub = parser.add_subparsers(dest="command")

    # group
    gp = sub.add_parser("group", help="Manage groups")
    gsub = gp.add_subparsers(dest="action")

    g_add = gsub.add_parser("add", help="Add a group")
    g_add.add_argument("name", help="Group name")
    g_add.add_argument("webhook", help="Discord webhook URL")
    g_add.set_defaults(func=group_add)

    g_list = gsub.add_parser("list", help="List groups")
    g_list.set_defaults(func=group_list)

    g_rm = gsub.add_parser("remove", help="Remove a group")
    g_rm.add_argument("name", help="Group name")
    g_rm.set_defaults(func=group_remove)

    # account
    ap = sub.add_parser("account", help="Manage accounts")
    asub = ap.add_subparsers(dest="action")

    a_add = asub.add_parser("add", help="Add accounts to a group")
    a_add.add_argument("group", help="Group name")
    a_add.add_argument("usernames", nargs="+", help="TikTok username(s)")
    a_add.set_defaults(func=account_add)

    a_rm = asub.add_parser("remove", help="Remove an account")
    a_rm.add_argument("group", help="Group name")
    a_rm.add_argument("username", help="TikTok username")
    a_rm.set_defaults(func=account_remove)

    a_list = asub.add_parser("list", help="List all accounts")
    a_list.set_defaults(func=account_list)

    a_imp = asub.add_parser("import", help="Bulk-import usernames from a text file (one per line)")
    a_imp.add_argument("group", help="Group name")
    a_imp.add_argument("file", help="Path to text file with usernames")
    a_imp.set_defaults(func=account_import)

    # instagram
    ip = sub.add_parser("instagram", help="Manage Instagram accounts")
    isub = ip.add_subparsers(dest="action")

    i_add = isub.add_parser("add", help="Add Instagram accounts to a group")
    i_add.add_argument("group", help="Group name")
    i_add.add_argument("usernames", nargs="+", help="Instagram username(s)")
    i_add.set_defaults(func=instagram_add)

    i_rm = isub.add_parser("remove", help="Remove an Instagram account")
    i_rm.add_argument("group", help="Group name")
    i_rm.add_argument("username", help="Instagram username")
    i_rm.set_defaults(func=instagram_remove)

    i_list = isub.add_parser("list", help="List all Instagram accounts")
    i_list.set_defaults(func=instagram_list)

    i_imp = isub.add_parser("import", help="Bulk-import Instagram usernames from a text file")
    i_imp.add_argument("group", help="Group name")
    i_imp.add_argument("file", help="Path to text file with usernames")
    i_imp.set_defaults(func=instagram_import)

    i_sc = isub.add_parser(
        "setup-cookies",
        help="Obtain Instagram cookies and save them (run once before starting bot)",
    )
    i_sc.add_argument(
        "--browser",
        metavar="BROWSER",
        help=f"Extract from installed browser: {', '.join(_SUPPORTED_BROWSERS)}",
    )
    i_sc.add_argument(
        "--username",
        metavar="USERNAME",
        help="Instagram username for credential login (may be blocked by Instagram)",
    )
    i_sc.add_argument(
        "--password",
        metavar="PASSWORD",
        help="Instagram password (omit to be prompted securely)",
    )
    i_sc.add_argument(
        "--output",
        metavar="FILE",
        help=f"Where to save cookies (default: {_COOKIE_FILE_DEFAULT})",
    )
    i_sc.set_defaults(func=instagram_setup_cookies)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
