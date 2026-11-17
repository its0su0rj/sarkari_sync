#!/usr/bin/env python3
"""
Keeps private working data encrypted on separate git branches, so it can live in a public
repository without anyone being able to read it.

    python state.py pull BRANCH FILE...    download + decrypt FILEs from BRANCH (missing branch = start empty)
    python state.py push BRANCH FILE...    encrypt FILEs and replace BRANCH with one fresh commit (no history)
    python state.py newkey                 print a new random STATE_PASSWORD

Branches used:
    jobsdb   sarkari.db       written by whoever runs the sync (your PC, or GitHub)
    state    bot_state.json   written only by the Telegram bot workflow
Keeping them apart means a sync upload can never overwrite new subscribers.

Environment: STATE_PASSWORD (required); GITHUB_REPOSITORY and GITHUB_TOKEN (set automatically in
GitHub Actions; pc_sync.bat sets them on your PC); STATE_REMOTE overrides the git URL (tests).
"""
import base64
import hashlib
import os
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile

from cryptography.fernet import Fernet, InvalidToken


def rmtree(path):
    """Delete a temp folder, including git's read-only files on Windows."""
    def fix(func, p, _):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=fix)


def hash_file(branch: str) -> str:
    return f".state_hash_{branch}"


def digest(files) -> str:
    h = hashlib.sha256()
    for name in files:
        if os.path.exists(name):
            if name.endswith(".db"):
                db = sqlite3.connect(name)
                for line in db.iterdump():               # content, not file layout
                    h.update(line.encode())
                db.close()
            else:
                h.update(open(name, "rb").read())
        h.update(b"|")
    return h.hexdigest()


def fernet() -> Fernet:
    pw = os.environ.get("STATE_PASSWORD", "").strip()
    if len(pw) < 20:
        sys.exit("STATE_PASSWORD must be set (at least 20 characters). Run: python state.py newkey")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(pw.encode()).digest()))


def remote() -> str:
    if os.environ.get("STATE_REMOTE"):
        return os.environ["STATE_REMOTE"]
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        sys.exit("GITHUB_REPOSITORY and GITHUB_TOKEN must be set")
    return f"https://x-access-token:{token}@github.com/{repo}.git"


def git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def hide(text: str) -> str:
    tok = os.environ.get("GITHUB_TOKEN")
    return text.replace(tok, "***") if tok else text


def pull(branch: str, files: list[str]):
    f = fernet()
    tmp = tempfile.mkdtemp()
    try:
        r = git("clone", "--depth", "1", "--branch", branch, remote(), tmp, check=False)
        if r.returncode != 0:
            print(f"no '{branch}' branch yet: starting with empty data")
            return
        for name in files:
            enc = os.path.join(tmp, name + ".enc")
            if not os.path.exists(enc):
                continue
            try:
                data = f.decrypt(open(enc, "rb").read())
            except InvalidToken:
                sys.exit(f"cannot decrypt {name}: STATE_PASSWORD is different from the one used to save it")
            for extra in ("-wal", "-shm"):                # stale SQLite side files would corrupt the copy
                if os.path.exists(name + extra):
                    os.remove(name + extra)
            with open(name, "wb") as fh:
                fh.write(data)
            print(f"restored {name} from '{branch}' ({len(data)} bytes)")
        with open(hash_file(branch), "w") as fh:
            fh.write(digest(files))
    finally:
        rmtree(tmp)


def push(branch: str, files: list[str]):
    f = fernet()
    for name in files:
        if name.endswith(".db") and os.path.exists(name):    # fold the WAL into the main file first
            db = sqlite3.connect(name)
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.close()
    hf = hash_file(branch)
    if os.path.exists(hf) and open(hf).read() == digest(files):
        print(f"no changes to save to '{branch}'")
        return
    tmp = tempfile.mkdtemp()
    try:
        git("init", "-q", tmp)
        git("checkout", "-q", "-b", branch, cwd=tmp)
        git("config", "user.name", "exammatch-bot", cwd=tmp)
        git("config", "user.email", "exammatch-bot@users.noreply.github.com", cwd=tmp)
        saved = []
        for name in files:
            if os.path.exists(name):
                with open(os.path.join(tmp, name + ".enc"), "wb") as fh:
                    fh.write(f.encrypt(open(name, "rb").read()))
                saved.append(name)
        if not saved:
            print("nothing to save")
            return
        with open(os.path.join(tmp, "README.md"), "w") as fh:
            fh.write("Encrypted working data for ExamMatch. Replaced on every run; do not edit.\n")
        git("add", ".", cwd=tmp)
        git("commit", "-q", "-m", "data", cwd=tmp)
        r = git("push", "-q", "--force", remote(), f"HEAD:{branch}", cwd=tmp, check=False)
        if r.returncode != 0:
            sys.exit("push failed: " + hide(r.stderr)[-500:])
        with open(hf, "w") as fh:
            fh.write(digest(files))
        print(f"saved {', '.join(saved)} to '{branch}'")
    finally:
        rmtree(tmp)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["newkey"]:
        print(secrets.token_urlsafe(32))
    elif len(args) >= 3 and args[0] in ("pull", "push"):
        (pull if args[0] == "pull" else push)(args[1], args[2:])
    else:
        print(__doc__)
        sys.exit(1)
