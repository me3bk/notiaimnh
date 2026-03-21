#!/usr/bin/env python3
"""CLI tool for managing Aymannoti groups and accounts."""

import argparse
import sys
from pathlib import Path

from config_helper import load_config, save_config, VERSION


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

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
