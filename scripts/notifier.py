import os
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "8988607257:AAHpWC7Ta_njrJdShXsdiUoyl6TaajcunqY"
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or "5551315625"

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