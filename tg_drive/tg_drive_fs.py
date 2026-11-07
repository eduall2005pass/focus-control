#!/usr/bin/env python3
"""
Telegram Group Virtual Cloud Drive (FUSE)
Mounts a Telegram Group/Channel as a read-only Linux filesystem with on-demand streaming.
"""
import os
import sys
import errno
import stat
import time
import asyncio
import threading
from collections import OrderedDict
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from fuse import FUSE, Operations, FuseOSError

API_ID = 39352313
API_HASH = "ea37c38f22a23da008e98ce1b0e982e8"
SESSION_DIR = os.path.expanduser("~/.local/share/focus-reminder")
SESSION_FILE = os.path.join(SESSION_DIR, "telegram_drive.session")
CACHE_DIR = os.path.expanduser("~/.cache/telegram-materials")
DEFAULT_MOUNT = os.path.expanduser("~/Telegram-Materials")
TARGET_PEER = os.environ.get("TG_CHAT", "-1004415493319")

EXT_CATEGORIES = {
    "PDFs": {".pdf"},
    "Videos": {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv"},
    "Images": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"},
    "Audio": {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"},
    "Documents": {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".epub", ".rtf", ".odt"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
}


class TelegramDriveFS(Operations):
    def __init__(self, loop, client, chat_entity):
        self.loop = loop
        self.client = client
        self.chat = chat_entity
        self.lock = threading.Lock()
        self.files = {}          # filename -> file_dict
        self.msg_to_name = {}    # msg_id -> filename
        self.categories = {cat: set() for cat in EXT_CATEGORIES}
        self.cache_dir = os.path.join(CACHE_DIR, "files")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.fd_map = {}
        self.next_fd = 100

    def add_message(self, msg):
        if not (msg.media or msg.file):
            return
        msg_id = msg.id
        filename = getattr(msg.file, "name", None)
        ext = getattr(msg.file, "ext", "") or ""
        size = getattr(msg.file, "size", 0) or 0
        mtime = msg.date.timestamp() if msg.date else time.time()

        if not filename:
            if not ext and msg.photo:
                ext = ".jpg"
            caption = (msg.text or "").strip()
            if caption and len(caption) <= 60 and not any(c in caption for c in '\n/\\:*?"<>|'):
                filename = f"{caption}{ext}"
            else:
                filename = f"file_{msg_id}{ext}"

        # Clean filename
        clean_name = filename.replace("/", "_").replace("\\", "_").strip()
        if not clean_name:
            clean_name = f"file_{msg_id}{ext}"

        with self.lock:
            # Handle duplicate names
            if clean_name in self.files and self.files[clean_name]["msg_id"] != msg_id:
                base, ext_part = os.path.splitext(clean_name)
                clean_name = f"{base} ({msg_id}){ext_part}"

            file_info = {
                "name": clean_name,
                "msg_id": msg_id,
                "size": size,
                "mtime": mtime,
                "media": msg.media,
            }
            self.files[clean_name] = file_info
            self.msg_to_name[msg_id] = clean_name

            file_ext = os.path.splitext(clean_name)[1].lower()
            for cat, exts in EXT_CATEGORIES.items():
                if file_ext in exts:
                    self.categories[cat].add(clean_name)

    def _resolve_path(self, path):
        path = path.strip("/")
        if not path:
            return "root", None
        parts = path.split("/", 1)
        if len(parts) == 1:
            if parts[0] in self.categories or parts[0] == "All":
                return "dir", parts[0]
            if parts[0] in self.files:
                return "file", self.files[parts[0]]
            return "none", None
        cat, fname = parts[0], parts[1]
        if cat in self.categories and fname in self.categories[cat]:
            return "file", self.files[fname]
        if cat == "All" and fname in self.files:
            return "file", self.files[fname]
        return "none", None

    # --- FUSE Operations (Read-Only) ---

    def getattr(self, path, fh=None):
        kind, item = self._resolve_path(path)
        now = time.time()
        if kind == "root" or kind == "dir":
            return dict(
                st_mode=stat.S_IFDIR | 0o555,
                st_nlink=2,
                st_size=4096,
                st_ctime=now,
                st_mtime=now,
                st_atime=now
            )
        elif kind == "file":
            return dict(
                st_mode=stat.S_IFREG | 0o444,
                st_nlink=1,
                st_size=item["size"],
                st_ctime=item["mtime"],
                st_mtime=item["mtime"],
                st_atime=now
            )
        raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        kind, item = self._resolve_path(path)
        entries = [".", ".."]
        if kind == "root":
            entries.append("All")
            entries.extend(sorted(self.categories.keys()))
            with self.lock:
                entries.extend(sorted(self.files.keys()))
            return entries
        elif kind == "dir":
            with self.lock:
                if item == "All":
                    return entries + sorted(self.files.keys())
                elif item in self.categories:
                    return entries + sorted(self.categories[item])
        raise FuseOSError(errno.ENOENT)

    def open(self, path, flags):
        # Enforce read-only
        accmode = flags & os.O_ACCMODE
        if accmode != os.O_RDONLY:
            raise FuseOSError(errno.EROFS)
        kind, item = self._resolve_path(path)
        if kind != "file":
            raise FuseOSError(errno.ENOENT)
        with self.lock:
            fd = self.next_fd
            self.next_fd += 1
            self.fd_map[fd] = item
        return fd

    def release(self, path, fh):
        with self.lock:
            self.fd_map.pop(fh, None)
        return 0

    def read(self, path, size, offset, fh):
        kind, item = self._resolve_path(path)
        if kind != "file":
            raise FuseOSError(errno.ENOENT)

        file_size = item["size"]
        if offset >= file_size:
            return b""
        req_size = min(size, file_size - offset)
        if req_size <= 0:
            return b""

        msg_id = item["msg_id"]
        cache_file = os.path.join(self.cache_dir, f"{msg_id}.dat")

        # Check if already cached on disk
        if os.path.isfile(cache_file):
            try:
                with open(cache_file, "rb") as cf:
                    cf.seek(0, os.SEEK_END)
                    cached_len = cf.tell()
                    if cached_len >= file_size:
                        cf.seek(offset)
                        return cf.read(req_size)
            except Exception:
                pass

        # Stream requested chunk from Telegram via Telethon
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._fetch_chunk(item["media"], offset, req_size, file_size, cache_file),
                self.loop
            )
            return future.result(timeout=60)
        except Exception as e:
            print(f"Error reading chunk for {item['name']} (off={offset}, sz={req_size}): {e}", flush=True)
            raise FuseOSError(errno.EIO)

    async def _fetch_chunk(self, media, offset, size, file_size, cache_file):
        # 512 KiB aligned chunking for optimal Telegram MTProto throughput
        CHUNK_ALIGN = 524288  # 512 KB
        aligned_offset = (offset // CHUNK_ALIGN) * CHUNK_ALIGN
        fetch_len = min(CHUNK_ALIGN, file_size - aligned_offset)
        if fetch_len < (offset - aligned_offset + size):
            fetch_len = min(CHUNK_ALIGN * 2, file_size - aligned_offset)

        buf = bytearray()
        async for chunk in self.client.iter_download(media, offset=aligned_offset, limit=fetch_len, request_size=CHUNK_ALIGN):
            buf.extend(chunk)

        # Write to sparse cache file for fast re-reading
        try:
            with open(cache_file, "r+b" if os.path.exists(cache_file) else "wb") as cf:
                cf.seek(aligned_offset)
                cf.write(buf)
        except Exception:
            pass

        start_in_buf = offset - aligned_offset
        return bytes(buf[start_in_buf:start_in_buf + size])

    # Explicitly disallow any write/delete/modify operation
    def write(self, path, data, offset, fh): raise FuseOSError(errno.EROFS)
    def create(self, path, mode, fi=None): raise FuseOSError(errno.EROFS)
    def unlink(self, path): raise FuseOSError(errno.EROFS)
    def rmdir(self, path): raise FuseOSError(errno.EROFS)
    def mkdir(self, path, mode): raise FuseOSError(errno.EROFS)
    def rename(self, old, new): raise FuseOSError(errno.EROFS)
    def chmod(self, path, mode): raise FuseOSError(errno.EROFS)
    def chown(self, path, uid, gid): raise FuseOSError(errno.EROFS)
    def truncate(self, path, length, fh=None): raise FuseOSError(errno.EROFS)


async def build_index(fs, chat_entity):
    print(f"[TG-Drive] Indexing files in {getattr(chat_entity, 'title', TARGET_PEER)}...", flush=True)
    count = 0
    async for msg in fs.client.iter_messages(chat_entity, limit=None):
        if msg.media or msg.file:
            fs.add_message(msg)
            count += 1
            if count % 50 == 0:
                print(f"[TG-Drive] Indexed {count} files...", flush=True)
    print(f"[TG-Drive] Index complete: {len(fs.files)} unique files ready to stream.", flush=True)


def start_daemon(mount_point=DEFAULT_MOUNT):
    os.makedirs(mount_point, exist_ok=True)
    symlink_path = os.path.expanduser("~/PDF-Materials")
    try:
        if not os.path.exists(symlink_path):
            os.symlink(mount_point, symlink_path)
    except Exception:
        pass

    session_str_file = os.path.join(SESSION_DIR, "telegram_drive.session_string")
    if os.path.isfile(session_str_file):
        with open(session_str_file) as sf:
            s_str = sf.read().strip()
        session_obj = StringSession(s_str)
    else:
        session_path = os.path.splitext(SESSION_FILE)[0]
        if not os.path.isfile(SESSION_FILE):
            print(f"ERROR: Session file {SESSION_FILE} not found. Please authenticate first using tg_auth.py.", flush=True)
            sys.exit(1)
        session_obj = session_path

    loop = asyncio.new_event_loop()
    client = TelegramClient(session_obj, API_ID, API_HASH, loop=loop)

    def run_telethon_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    threading.Thread(target=run_telethon_loop, daemon=True).start()

    # Connect client
    future = asyncio.run_coroutine_threadsafe(client.connect(), loop)
    future.result(timeout=15)

    future = asyncio.run_coroutine_threadsafe(client.is_user_authorized(), loop)
    if not future.result(timeout=10):
        print("ERROR: User is not authorized. Please run tg_auth.py to log in.", flush=True)
        sys.exit(1)

    # Resolve target chat
    target = TARGET_PEER
    try:
        if target.startswith("-100"):
            target = int(target)
    except ValueError:
        pass

    future = asyncio.run_coroutine_threadsafe(client.get_entity(target), loop)
    chat_entity = future.result(timeout=20)
    print(f"[TG-Drive] Connected to Telegram. Target: {getattr(chat_entity, 'title', target)}", flush=True)

    fs = TelegramDriveFS(loop, client, chat_entity)

    # Initial index
    future = asyncio.run_coroutine_threadsafe(build_index(fs, chat_entity), loop)
    future.result(timeout=120)

    # Listen for new files in real time
    @client.on(events.NewMessage(chats=chat_entity))
    async def on_new_msg(event):
        if event.message and (event.message.media or event.message.file):
            fs.add_message(event.message)
            print(f"[TG-Drive] New file received: {getattr(event.message.file, 'name', 'unnamed')}", flush=True)

    print(f"[TG-Drive] Mounting FUSE filesystem at {mount_point}...", flush=True)
    print(f"[TG-Drive] Accessible at: {mount_point} and {symlink_path}")
    print("[TG-Drive] Read-only, on-demand streaming enabled. Press Ctrl+C to exit.", flush=True)

    # Clean unmount if already mounted
    os.system(f"fusermount -u {mount_point} 2>/dev/null")

    try:
        FUSE(fs, mount_point, foreground=True, nothreads=False, ro=True, allow_other=False)
    finally:
        os.system(f"fusermount -u {mount_point} 2>/dev/null")


if __name__ == "__main__":
    mnt = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MOUNT
    start_daemon(mnt)
