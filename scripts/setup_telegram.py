"""One-shot Telegram setup.

Asks for the bot token, discovers the chat id from the bot's own updates,
stores both as GitHub Actions secrets and sends a confirmation message.

    python scripts/setup_telegram.py
"""

from __future__ import annotations

import subprocess
import sys

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def call(token: str, method: str, **params):
    resp = requests.get(API.format(token=token, method=method), params=params, timeout=20)
    return resp.json()


def find_chat_id(token: str) -> str | None:
    """Read the bot's pending updates and pull the chat id out of the newest one."""
    data = call(token, "getUpdates")
    if not data.get("ok"):
        return None
    for update in reversed(data.get("result", [])):
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if chat.get("id") is not None:
            return str(chat["id"])
    return None


def set_secret(name: str, value: str) -> bool:
    try:
        subprocess.run(
            ["gh", "secret", "set", name],
            input=value,
            text=True,
            check=True,
            capture_output=True,
        )
        return True
    except FileNotFoundError:
        print("  ! the GitHub CLI (gh) was not found on PATH")
    except subprocess.CalledProcessError as exc:
        print(f"  ! gh failed: {exc.stderr.strip()}")
    return False


def main() -> int:
    print("\n=== Telegram setup ===\n")
    print("1. Open Telegram and message @BotFather")
    print("2. Send /newbot and follow the prompts")
    print("3. Copy the token it gives you (looks like 12345678:AAE...)\n")

    token = input("Paste the bot token here: ").strip()
    if not token:
        print("No token entered, aborting.")
        return 1

    me = call(token, "getMe")
    if not me.get("ok"):
        print(f"\nThat token was rejected by Telegram: {me.get('description', 'unknown error')}")
        return 1

    username = me["result"].get("username", "?")
    print(f"\nConnected to bot @{username}\n")

    # The bot can only learn a chat id after the user has written to it first.
    print(f"4. Open https://t.me/{username} and send it any message (e.g. 'hi')")
    input("   Press Enter once you have sent it: ")

    chat_id = find_chat_id(token)
    while chat_id is None:
        print("\n   No message found yet. Make sure you sent one to the bot.")
        if input("   Press Enter to retry, or type 's' to skip: ").strip().lower() == "s":
            return 1
        chat_id = find_chat_id(token)

    print(f"\nFound your chat id: {chat_id}")

    print("\nStoring GitHub Actions secrets...")
    ok = set_secret("TELEGRAM_BOT_TOKEN", token) and set_secret("TELEGRAM_CHAT_ID", chat_id)
    if ok:
        print("  TELEGRAM_BOT_TOKEN  stored")
        print("  TELEGRAM_CHAT_ID    stored")
    else:
        print("\nCould not store them automatically. Add these by hand under")
        print("Settings -> Secrets and variables -> Actions:")
        print(f"  TELEGRAM_BOT_TOKEN = {token}")
        print(f"  TELEGRAM_CHAT_ID   = {chat_id}")

    sent = call(
        token,
        "sendMessage",
        chat_id=chat_id,
        text="Job radar is connected. Alerts will start once the warm-up window closes.",
    )
    print("\nTest message sent." if sent.get("ok") else f"\nTest message failed: {sent}")

    print("\nDone. Nothing else to do -- the workflow already runs every 15 minutes.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
