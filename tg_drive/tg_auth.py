#!/usr/bin/env python3
"""
Telegram Drive Authentication Helper using persistent StringSession
"""
import os
import sys
import json
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

API_ID = 39352313
API_HASH = "ea37c38f22a23da008e98ce1b0e982e8"
SESSION_DIR = os.path.expanduser("~/.local/share/focus-reminder")
SESSION_FILE = os.path.join(SESSION_DIR, "telegram_drive.session")
PENDING_FILE = os.path.join(SESSION_DIR, "tg_pending_auth.json")


async def auth_bot(bot_token):
    os.makedirs(SESSION_DIR, exist_ok=True)
    session_path = os.path.splitext(SESSION_FILE)[0]
    client = TelegramClient(session_path, API_ID, API_HASH)
    await client.connect()
    try:
        await client.sign_in(bot_token=bot_token)
        me = await client.get_me()
        print(f"SUCCESS: Bot authenticated as @{me.username} ({me.first_name})", flush=True)
        return True
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        return False
    finally:
        await client.disconnect()


async def send_phone_code(phone):
    os.makedirs(SESSION_DIR, exist_ok=True)
    session = StringSession()
    client = TelegramClient(session, API_ID, API_HASH)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        session_str = session.save()
        with open(PENDING_FILE, "w") as f:
            json.dump({
                "phone": phone,
                "phone_code_hash": sent.phone_code_hash,
                "session_str": session_str
            }, f)
        print(f"CODE_SENT: Verification code sent to {phone}. Check your Telegram app!", flush=True)
        return True
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        return False
    finally:
        await client.disconnect()


async def verify_phone_code(code, password=None):
    if not os.path.isfile(PENDING_FILE):
        print("ERROR: No pending code request found. Run send-code first.", flush=True)
        return False

    with open(PENDING_FILE) as f:
        data = json.load(f)

    phone = data["phone"]
    phone_code_hash = data["phone_code_hash"]
    session_str = data["session_str"]

    session = StringSession(session_str)
    client = TelegramClient(session, API_ID, API_HASH)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                print("PASSWORD_NEEDED: 2-step verification password is required.", flush=True)
                return False
            await client.sign_in(password=password)

        me = await client.get_me()
        print(f"SUCCESS: Logged in as {me.first_name} (@{me.username or me.phone}) ID:{me.id}", flush=True)

        # Export authenticated StringSession to SQLite file for the daemon
        auth_session_str = session.save()
        # Save string session to file
        str_session_file = os.path.join(SESSION_DIR, "telegram_drive.session_string")
        with open(str_session_file, "w") as sf:
            sf.write(auth_session_str)

        # Also initialize standard session file
        session_path = os.path.splitext(SESSION_FILE)[0]
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
        sqlite_client = TelegramClient(session_path, API_ID, API_HASH)
        await sqlite_client.connect()
        sqlite_client.session.auth_key = session.auth_key
        sqlite_client.session.save()
        await sqlite_client.disconnect()

        if os.path.exists(PENDING_FILE):
            os.remove(PENDING_FILE)
        return True
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        return False
    finally:
        await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 tg_auth.py bot <BOT_TOKEN>")
        print("  python3 tg_auth.py send-code <PHONE_NUMBER>")
        print("  python3 tg_auth.py verify <CODE> [2FA_PASSWORD]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "bot" and len(sys.argv) >= 3:
        asyncio.run(auth_bot(sys.argv[2]))
    elif cmd == "send-code" and len(sys.argv) >= 3:
        asyncio.run(send_phone_code(sys.argv[2]))
    elif cmd == "verify" and len(sys.argv) >= 3:
        pwd = sys.argv[3] if len(sys.argv) >= 4 else None
        asyncio.run(verify_phone_code(sys.argv[2], pwd))
    else:
        print("Invalid arguments.")
        sys.exit(1)
