#!/usr/bin/env python3
"""
Telegram Multi-Group Virtual Cloud Drive (FUSE)
Mounts Telegram Groups/Channels as a read/write Linux filesystem with:
- On-demand streaming (lazy reading, sparse local chunk caching)
- Direct file uploads (copy/drop files into group folders to upload to Telegram)
- Multi-group support: "PDF Materials Upload" and "biprova 20"
"""
import os
import sys
import errno
import stat
import time
import asyncio
import threading
import shutil
import ctypes.util
from collections import OrderedDict

# Ensure libfuse is located even if LD_LIBRARY_PATH isn't set in user environment
_orig_find_library = ctypes.util.find_library
def _custom_find_library(name):
    if name == 'fuse':
        for p in ('/home/prof21/.local/lib/libfuse.so.2',
                  '/home/prof21/.local/lib/libfuse.so',
                  '/usr/lib/x86_64-linux-gnu/libfuse.so.2',
                  '/lib/x86_64-linux-gnu/libfuse.so.2'):
            if os.path.exists(p):
                return p
    return _orig_find_library(name)
ctypes.util.find_library = _custom_find_library

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from fuse import FUSE, Operations, FuseOSError

API_ID = 39352313
API_HASH = "ea37c38f22a23da008e98ce1b0e982e8"
SESSION_DIR = os.path.expanduser("~/.local/share/focus-reminder")
SESSION_FILE = os.path.join(SESSION_DIR, "telegram_drive.session")
CACHE_DIR = os.path.expanduser("~/.cache/telegram-materials")
STAGING_DIR = os.path.join(CACHE_DIR, "upload_staging")
DEFAULT_MOUNT = os.path.expanduser("~/Telegram-Materials")

# Default groups to mount:
# 1) Primary PDF Materials Upload (-1004415493319 / pdf258upload)
# 2) Biprova 20 (searches joined chats/channels)
DEFAULT_TARGETS = [
    "-1004415493319",
    "biprova 20",
]

EXT_CATEGORIES = {
    "PDFs": {".pdf"},
    "Videos": {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv"},
    "Images": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"},
    "Audio": {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac"},
    "Documents": {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".epub", ".rtf", ".odt"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
}


class GroupDrive:
    """Manages file listing and categories for a single Telegram group."""
    def __init__(self, chat_entity, folder_name=None):
        self.chat = chat_entity
        self.id = getattr(chat_entity, "id", 0)
        self.title = getattr(chat_entity, "title", str(self.id)).strip()
        self.name = folder_name or self._clean_folder_name(self.title)
        self.lock = threading.Lock()
        self.files = {}          # clean_filename -> file_dict
        self.msg_to_name = {}    # msg_id -> clean_filename
        self.categories = {cat: set() for cat in EXT_CATEGORIES}

    @staticmethod
    def _clean_folder_name(title):
        clean = title.replace("/", "_").replace("\\", "_").replace(":", "_").strip()
        return clean or "Unnamed_Group"

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

        clean_name = filename.replace("/", "_").replace("\\", "_").strip()
        if not clean_name:
            clean_name = f"file_{msg_id}{ext}"

        with self.lock:
            # Handle duplicate names inside this group
            if clean_name in self.files and self.files[clean_name]["msg_id"] != msg_id:
                base, ext_part = os.path.splitext(clean_name)
                clean_name = f"{base} ({msg_id}){ext_part}"

            file_info = {
                "name": clean_name,
                "msg_id": msg_id,
                "size": size,
                "mtime": mtime,
                "media": msg.media,
                "group": self,
            }
            self.files[clean_name] = file_info
            self.msg_to_name[msg_id] = clean_name

            file_ext = os.path.splitext(clean_name)[1].lower()
            for cat, exts in EXT_CATEGORIES.items():
                if file_ext in exts:
                    self.categories[cat].add(clean_name)


class TelegramMultiDriveFS(Operations):
    """
    FUSE filesystem exposing multiple Telegram groups as virtual directories
    with lazy chunk streaming and write/upload capability.
    """
    def __init__(self, loop, client, groups_dict, default_group):
        self.loop = loop
        self.client = client
        self.groups = groups_dict         # folder_name -> GroupDrive
        self.default_group = default_group
        self.lock = threading.Lock()
        self.cache_dir = os.path.join(CACHE_DIR, "files")
        self.staging_dir = STAGING_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(self.staging_dir, exist_ok=True)

        self.next_fd = 1000
        self.read_fd_map = {}             # fd -> file_info
        self.write_fd_map = {}            # fd -> upload_dict
        self.active_uploads = {}          # normalized_path -> upload_dict

    def _resolve(self, path):
        path = path.strip("/")
        if not path:
            return "root", None

        parts = path.split("/")
        first = parts[0]

        # Case 1: First path element is a known group folder
        if first in self.groups:
            grp = self.groups[first]
            if len(parts) == 1:
                return "group_dir", grp
            second = parts[1]
            if len(parts) == 2:
                if second == "All" or second in grp.categories:
                    return "cat_dir", (grp, second)
                with grp.lock:
                    if second in grp.files:
                        return "file", (grp, grp.files[second])
                return "none", None
            if len(parts) == 3:
                cat, fname = second, parts[2]
                with grp.lock:
                    if cat == "All" and fname in grp.files:
                        return "file", (grp, grp.files[fname])
                    if cat in grp.categories and fname in grp.categories[cat]:
                        return "file", (grp, grp.files[fname])
                return "none", None
            return "none", None

        # Case 2: Direct access at root level (fallback to default group)
        if self.default_group:
            grp = self.default_group
            if len(parts) == 1:
                if first == "All" or first in grp.categories:
                    return "cat_dir", (grp, first)
                with grp.lock:
                    if first in grp.files:
                        return "file", (grp, grp.files[first])
            elif len(parts) == 2:
                cat, fname = parts[0], parts[1]
                with grp.lock:
                    if cat == "All" and fname in grp.files:
                        return "file", (grp, grp.files[fname])
                    if cat in grp.categories and fname in grp.categories[cat]:
                        return "file", (grp, grp.files[fname])

        return "none", None

    # --- FUSE Operations ---

    def getattr(self, path, fh=None):
        now = time.time()
        norm_path = path.strip("/")

        # Active in-flight upload file info
        with self.lock:
            if norm_path in self.active_uploads:
                up = self.active_uploads[norm_path]
                return dict(
                    st_mode=stat.S_IFREG | 0o664,
                    st_nlink=1,
                    st_size=up["size"],
                    st_ctime=up["mtime"],
                    st_mtime=up["mtime"],
                    st_atime=now
                )

        kind, item = self._resolve(path)
        if kind in ("root", "group_dir", "cat_dir"):
            return dict(
                st_mode=stat.S_IFDIR | 0o775,
                st_nlink=2,
                st_size=4096,
                st_ctime=now,
                st_mtime=now,
                st_atime=now
            )
        elif kind == "file":
            grp, f_info = item
            return dict(
                st_mode=stat.S_IFREG | 0o664,
                st_nlink=1,
                st_size=f_info["size"],
                st_ctime=f_info["mtime"],
                st_mtime=f_info["mtime"],
                st_atime=now
            )
        raise FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        kind, item = self._resolve(path)
        entries = [".", ".."]

        if kind == "root":
            # Expose all group folders
            entries.extend(sorted(self.groups.keys()))
            return entries

        if kind == "group_dir":
            grp = item
            entries.append("All")
            entries.extend(sorted(grp.categories.keys()))
            with grp.lock:
                entries.extend(sorted(grp.files.keys()))
            return entries

        if kind == "cat_dir":
            grp, cat = item
            with grp.lock:
                if cat == "All":
                    return entries + sorted(grp.files.keys())
                if cat in grp.categories:
                    return entries + sorted(grp.categories[cat])

        raise FuseOSError(errno.ENOENT)

    def open(self, path, flags):
        accmode = flags & os.O_ACCMODE
        if accmode != os.O_RDONLY:
            return self._create_or_open_write(path, flags)

        kind, item = self._resolve(path)
        if kind != "file":
            raise FuseOSError(errno.ENOENT)
        grp, f_info = item

        with self.lock:
            fd = self.next_fd
            self.next_fd += 1
            self.read_fd_map[fd] = f_info
        return fd

    def read(self, path, size, offset, fh):
        with self.lock:
            f_info = self.read_fd_map.get(fh)

        if not f_info:
            kind, res = self._resolve(path)
            if kind != "file":
                raise FuseOSError(errno.ENOENT)
            f_info = res[1]

        file_size = f_info["size"]
        if offset >= file_size:
            return b""
        req_size = min(size, file_size - offset)
        if req_size <= 0:
            return b""

        msg_id = f_info["msg_id"]
        cache_file = os.path.join(self.cache_dir, f"{msg_id}.dat")

        # Fast local cache read
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

        # Stream chunk from Telegram via Telethon MTProto
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._fetch_chunk(f_info["media"], offset, req_size, file_size, cache_file),
                self.loop
            )
            return future.result(timeout=60)
        except Exception as e:
            print(f"[TG-Drive] Read error for {f_info['name']} (off={offset}, sz={req_size}): {e}", flush=True)
            raise FuseOSError(errno.EIO)

    async def _fetch_chunk(self, media, offset, size, file_size, cache_file):
        CHUNK_ALIGN = 524288  # 512 KiB aligned chunks
        aligned_offset = (offset // CHUNK_ALIGN) * CHUNK_ALIGN
        fetch_len = min(CHUNK_ALIGN, file_size - aligned_offset)
        if fetch_len < (offset - aligned_offset + size):
            fetch_len = min(CHUNK_ALIGN * 2, file_size - aligned_offset)

        buf = bytearray()
        async for chunk in self.client.iter_download(media, offset=aligned_offset, limit=fetch_len, request_size=CHUNK_ALIGN):
            buf.extend(chunk)

        try:
            with open(cache_file, "r+b" if os.path.exists(cache_file) else "wb") as cf:
                cf.seek(aligned_offset)
                cf.write(buf)
        except Exception:
            pass

        start_in_buf = offset - aligned_offset
        return bytes(buf[start_in_buf:start_in_buf + size])

    # --- Write & Upload Operations ---

    def create(self, path, mode, fi=None):
        return self._create_or_open_write(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)

    def _create_or_open_write(self, path, flags):
        parts = path.strip("/").split("/")
        filename = parts[-1]

        # Determine destination group
        target_group = None
        if len(parts) >= 2 and parts[0] in self.groups:
            target_group = self.groups[parts[0]]
        else:
            target_group = self.default_group

        if not target_group:
            raise FuseOSError(errno.EROFS)

        staging_name = f"{int(time.time() * 1000)}_{filename}"
        staging_file = os.path.join(self.staging_dir, staging_name)

        try:
            fp = open(staging_file, "wb+")
        except Exception as e:
            print(f"[TG-Drive] Failed to create staging file {staging_file}: {e}", flush=True)
            raise FuseOSError(errno.EIO)

        norm_path = path.strip("/")
        with self.lock:
            fd = self.next_fd
            self.next_fd += 1
            upload_entry = {
                "path": norm_path,
                "filename": filename,
                "group": target_group,
                "staging_path": staging_file,
                "fp": fp,
                "size": 0,
                "mtime": time.time(),
            }
            self.write_fd_map[fd] = upload_entry
            self.active_uploads[norm_path] = upload_entry

        print(f"[TG-Drive] In-flight upload initiated: '{filename}' -> Group '{target_group.name}'", flush=True)
        return fd

    def write(self, path, data, offset, fh):
        with self.lock:
            up = self.write_fd_map.get(fh)
        if not up:
            raise FuseOSError(errno.EBADF)
        try:
            up["fp"].seek(offset)
            up["fp"].write(data)
            up["size"] = max(up["size"], offset + len(data))
            return len(data)
        except Exception as e:
            print(f"[TG-Drive] Write staging error: {e}", flush=True)
            raise FuseOSError(errno.EIO)

    def truncate(self, path, length, fh=None):
        with self.lock:
            up = self.write_fd_map.get(fh) if fh else self.active_uploads.get(path.strip("/"))
        if up:
            up["fp"].truncate(length)
            up["size"] = length
            return 0
        return 0

    def flush(self, path, fh):
        with self.lock:
            up = self.write_fd_map.get(fh)
        if up:
            try:
                up["fp"].flush()
            except Exception:
                pass
        return 0

    def release(self, path, fh):
        norm_path = path.strip("/")
        up = None

        with self.lock:
            if fh in self.read_fd_map:
                self.read_fd_map.pop(fh, None)
                return 0
            if fh in self.write_fd_map:
                up = self.write_fd_map.pop(fh, None)
                self.active_uploads.pop(norm_path, None)

        if up:
            try:
                up["fp"].flush()
                up["fp"].close()
            except Exception:
                pass

            staging_path = up["staging_path"]
            group = up["group"]
            filename = up["filename"]

            # Trigger background Telegram upload worker
            def _upload_worker():
                try:
                    if not os.path.isfile(staging_path) or os.path.getsize(staging_path) == 0:
                        print(f"[TG-Drive] File {filename} was empty; skipped upload.", flush=True)
                        return

                    size_mb = os.path.getsize(staging_path) / (1024 * 1024)
                    print(f"[TG-Drive] 📤 Uploading '{filename}' ({size_mb:.2f} MB) to Telegram group '{group.name}'...", flush=True)

                    async def _send():
                        # Send file to group
                        msg = await self.client.send_file(group.chat, staging_path, caption=filename)
                        group.add_message(msg)
                        print(f"[TG-Drive] ✅ Successfully uploaded '{filename}' to '{group.name}' (Msg ID: {msg.id})", flush=True)

                    future = asyncio.run_coroutine_threadsafe(_send(), self.loop)
                    future.result(timeout=1800)  # 30 minutes timeout for big files
                except Exception as e:
                    print(f"[TG-Drive] ❌ Upload failed for '{filename}': {e}", flush=True)
                finally:
                    try:
                        if os.path.exists(staging_path):
                            os.remove(staging_path)
                    except Exception:
                        pass

            threading.Thread(target=_upload_worker, daemon=True).start()

        return 0

    # Read-only fallback for other modification operations
    def unlink(self, path): raise FuseOSError(errno.EPERM)
    def rmdir(self, path): raise FuseOSError(errno.EPERM)
    def mkdir(self, path, mode): raise FuseOSError(errno.EPERM)
    def rename(self, old, new): raise FuseOSError(errno.EPERM)
    def chmod(self, path, mode): return 0
    def chown(self, path, uid, gid): return 0


async def resolve_target(client, target):
    """Resolve a chat entity by ID, username, or case-insensitive title search."""
    target_str = str(target).strip()

    # Try numeric ID
    try:
        if target_str.startswith("-100") or target_str.isdigit() or target_str.startswith("-"):
            ent = await client.get_entity(int(target_str))
            if ent: return ent
    except Exception:
        pass

    # Try username or direct entity string
    try:
        ent = await client.get_entity(target_str)
        if ent: return ent
    except Exception:
        pass

    # Search all user dialogs by title/name
    target_lower = target_str.lower()
    async for dialog in client.iter_dialogs():
        title = (dialog.name or "").lower()
        if target_lower in title or ("biprov" in target_lower and "biprov" in title):
            return dialog.entity

    return None


async def build_index(group_drive, client):
    """Index all media files in a Telegram group."""
    print(f"[TG-Drive] Indexing files in '{group_drive.name}'...", flush=True)
    count = 0
    async for msg in client.iter_messages(group_drive.chat, limit=None):
        if msg.media or msg.file:
            group_drive.add_message(msg)
            count += 1
            if count % 50 == 0:
                print(f"[TG-Drive] Indexed {count} files in '{group_drive.name}'...", flush=True)
    print(f"[TG-Drive] Index complete for '{group_drive.name}': {len(group_drive.files)} files ready.", flush=True)


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
    future.result(timeout=20)

    future = asyncio.run_coroutine_threadsafe(client.is_user_authorized(), loop)
    if not future.result(timeout=10):
        print("ERROR: User is not authorized. Please run tg_auth.py to log in.", flush=True)
        sys.exit(1)

    print("[TG-Drive] Connected and authorized with Telegram.", flush=True)

    # Determine targets from env or default
    targets = DEFAULT_TARGETS
    if os.environ.get("TG_CHATS"):
        targets = [t.strip() for t in os.environ.get("TG_CHATS").split(",") if t.strip()]
    elif os.environ.get("TG_CHAT"):
        targets = [os.environ.get("TG_CHAT").strip(), "biprova 20"]

    groups_dict = OrderedDict()
    primary_group = None

    for target in targets:
        try:
            future = asyncio.run_coroutine_threadsafe(resolve_target(client, target), loop)
            ent = future.result(timeout=20)
            if ent:
                title = getattr(ent, "title", str(target))
                # Custom friendly folder name
                folder_name = "PDF Materials" if ("pdf" in title.lower() or "4415493319" in str(getattr(ent, "id", ""))) else GroupDrive._clean_folder_name(title)
                grp = GroupDrive(ent, folder_name=folder_name)
                groups_dict[folder_name] = grp
                if primary_group is None:
                    primary_group = grp
                print(f"[TG-Drive] Resolved group: '{title}' -> Folder: '{folder_name}' (ID: {grp.id})", flush=True)

                # Persist discovered peer IDs for Study Mode
                if "4415493319" in str(grp.id) or "pdf" in title.lower():
                    try:
                        with open(os.path.join(SESSION_DIR, "telegram-target-peer"), "w") as f:
                            f.write(str(grp.id))
                    except Exception:
                        pass
                if "biprov" in title.lower():
                    try:
                        with open(os.path.join(SESSION_DIR, "telegram-biprova-peer"), "w") as f:
                            f.write(str(grp.id))
                    except Exception:
                        pass
            else:
                print(f"[TG-Drive] Warning: Could not find Telegram group matching '{target}'", flush=True)
        except Exception as e:
            print(f"[TG-Drive] Error resolving '{target}': {e}", flush=True)

    if not groups_dict:
        print("ERROR: No groups could be resolved. Please check group permissions.", flush=True)
        sys.exit(1)

    # Build initial index for all groups
    for grp in groups_dict.values():
        future = asyncio.run_coroutine_threadsafe(build_index(grp, client), loop)
        future.result(timeout=180)

    # Real-time event listener for incoming messages across all connected groups
    all_chats = [g.chat for g in groups_dict.values()]
    @client.on(events.NewMessage(chats=all_chats))
    async def on_new_msg(event):
        if event.message and (event.message.media or event.message.file):
            chat_id = getattr(event.chat, "id", None)
            for g in groups_dict.values():
                if getattr(g.chat, "id", None) == chat_id:
                    g.add_message(event.message)
                    print(f"[TG-Drive] 📥 New file arrived in '{g.name}': {getattr(event.message.file, 'name', 'unnamed')}", flush=True)
                    break

    fs = TelegramMultiDriveFS(loop, client, groups_dict, default_group=primary_group)

    print(f"[TG-Drive] Mounting FUSE filesystem at {mount_point}...", flush=True)
    print(f"[TG-Drive] Mounted groups:")
    for name, g in groups_dict.items():
        print(f"  📁 {mount_point}/{name} ({len(g.files)} files)")
    print(f"[TG-Drive] Upload enabled! Drag and drop files into group folders to upload directly to Telegram.")
    print("[TG-Drive] Press Ctrl+C to unmount and exit.", flush=True)

    # Clean unmount if already mounted
    os.system(f"fusermount -u {mount_point} 2>/dev/null")

    try:
        FUSE(fs, mount_point, foreground=True, nothreads=False, ro=False, allow_other=False)
    finally:
        os.system(f"fusermount -u {mount_point} 2>/dev/null")


if __name__ == "__main__":
    mnt = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MOUNT
    start_daemon(mnt)
