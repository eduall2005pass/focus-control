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
STR_SESSION_FILE = os.path.join(SESSION_DIR, "telegram_drive.session_string")


async def auth_bot(bot_token):
    os.makedirs(SESSION_DIR, exist_ok=True)
    session = StringSession()
    client = TelegramClient(session, API_ID, API_HASH)
    await client.connect()
    try:
        await client.sign_in(bot_token=bot_token)
        me = await client.get_me()
        print(f"SUCCESS: Bot authenticated as @{me.username} ({me.first_name})", flush=True)
        with open(STR_SESSION_FILE, "w") as sf:
            sf.write(session.save())
        os.chmod(STR_SESSION_FILE, 0o600)
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
        os.chmod(PENDING_FILE, 0o600)
        print(f"\n[OK] Verification code sent to {phone}. Please check your official Telegram app!", flush=True)
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
        print(f"\nSUCCESS: Logged in as {me.first_name} (@{me.username or me.phone}) ID:{me.id}", flush=True)

        # Export authenticated StringSession to file for the daemon
        auth_session_str = session.save()
        with open(STR_SESSION_FILE, "w") as sf:
            sf.write(auth_session_str)
        os.chmod(STR_SESSION_FILE, 0o600)

        # Also initialize standard session file if possible
        try:
            session_path = os.path.splitext(SESSION_FILE)[0]
            if os.path.exists(SESSION_FILE):
                os.remove(SESSION_FILE)
            sqlite_client = TelegramClient(session_path, API_ID, API_HASH)
            await sqlite_client.connect()
            sqlite_client.session.set_dc(session.dc_id, session.server_address, session.port)
            sqlite_client.session.auth_key = session.auth_key
            sqlite_client.session.save()
            await sqlite_client.disconnect()
        except Exception:
            pass

        if os.path.exists(PENDING_FILE):
            os.remove(PENDING_FILE)
        return True
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        return False
    finally:
        await client.disconnect()


async def check_status():
    if not os.path.isfile(STR_SESSION_FILE):
        print("STATUS: NOT_AUTHENTICATED (No session file found)")
        return False
    with open(STR_SESSION_FILE) as f:
        s_str = f.read().strip()
    client = TelegramClient(StringSession(s_str), API_ID, API_HASH)
    await client.connect()
    try:
        auth = await client.is_user_authorized()
        if not auth:
            print("STATUS: NOT_AUTHORIZED (Session invalid or expired)")
            return False
        me = await client.get_me()
        print(f"STATUS: AUTHORIZED as {me.first_name} (@{me.username or me.phone}) ID:{me.id}")

        print("\nChecking Target Groups:")
        # Check PDF materials
        try:
            ent1 = await client.get_entity(-1004415493319)
            print(f"  [OK] PDF Materials Upload (ID: {ent1.id}, Title: '{ent1.title}')")
        except Exception:
            try:
                ent1 = await client.get_entity("pdf258upload")
                print(f"  [OK] PDF Materials Upload (ID: {ent1.id}, Title: '{ent1.title}')")
            except Exception as e2:
                print(f"  [FAIL] PDF Materials Upload: {e2}")

        # Check Biprova
        found_biprova = False
        async for dialog in client.iter_dialogs():
            name = (dialog.name or "").lower()
            if "biprov" in name or "biprova" in name:
                print(f"  [OK] Biprova 20 found: '{dialog.name}' (ID: {dialog.id})")
                found_biprova = True
                try:
                    with open(os.path.join(SESSION_DIR, "telegram-biprova-peer"), "w") as pf:
                        pf.write(str(dialog.id))
                except Exception:
                    pass
                break
        if not found_biprova:
            print("  [INFO] 'biprova 20' not yet found by title in joined dialogs.")
        return True
    except Exception as e:
        print(f"ERROR: {e}")
        return False
    finally:
        await client.disconnect()


async def interactive_login():
    print("========================================")
    print("   Telegram Drive Interactive Login    ")
    print("========================================")
    default_phone = "+8801826847602"
    prompt_str = f"Enter Phone Number (default [{default_phone}], or enter new): "
    inp_phone = input(prompt_str).strip()
    phone = inp_phone if inp_phone else default_phone

    ok = await send_phone_code(phone)
    if not ok:
        print("Failed to send code. Please verify your phone number.")
        return

    code = input("\nEnter Verification Code from Telegram: ").strip()
    pwd = input("Enter 2FA Password (if set, or press Enter to skip): ").strip() or None

    ok = await verify_phone_code(code, pwd)
    if ok:
        print("\nChecking Telegram groups...")
        await check_status()
        print("\nEnabling & starting telegram-materials.service...")
        os.system("systemctl --user enable --now telegram-materials.service")
        print("\n[SUCCESS] Telegram Drive is now live at ~/Telegram-Materials and ~/PDF-Materials!")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        asyncio.run(interactive_login())
        sys.exit(0)

    cmd = sys.argv[1].lower()
    if cmd == "login":
        asyncio.run(interactive_login())
    elif cmd == "bot" and len(sys.argv) >= 3:
        asyncio.run(auth_bot(sys.argv[2]))
    elif cmd == "send-code" and len(sys.argv) >= 3:
        asyncio.run(send_phone_code(sys.argv[2]))
    elif cmd == "verify" and len(sys.argv) >= 3:
        pwd = sys.argv[3] if len(sys.argv) >= 4 else None
        asyncio.run(verify_phone_code(sys.argv[2], pwd))
    elif cmd == "status":
        asyncio.run(check_status())
    else:
        print("Usage:")
        print("  ./tg_login.sh                    (Interactive login)")
        print("  ./tg_login.sh send-code <PHONE>")
        print("  ./tg_login.sh verify <CODE> [2FA]")
        print("  ./tg_login.sh status")
        sys.exit(1)
