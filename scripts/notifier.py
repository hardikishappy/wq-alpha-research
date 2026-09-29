import os
import requests

import json
from pathlib import Path

# Load credentials dynamically from the gitignored config file
_cfg_path = Path(__file__).resolve().parent.parent / "telegram_config.json"
_cfg = {}
if _cfg_path.exists():
    try:
        _cfg = json.loads(_cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or _cfg.get("bot_token", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or _cfg.get("chat_id", "")

def send_telegram_alert(message: str):
    """Sends a markdown-formatted message to the designated Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Notifier] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID. Skipping alert.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        resp = requests.post(url, json=payload, timeout=25)
        if resp.status_code == 200:
            print(">> Telegram notification dispatched successfully.")
            return True
        else:
            print(f">> Telegram notification failed (Status {resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f">> Telegram notification network error: {e}")
        return False

if __name__ == "__main__":
    send_telegram_alert("🚀 *BRAIN Orchestrator Test*: Telegram notifier pipeline connected successfully!")