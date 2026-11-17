#!/usr/bin/env python3
"""
Keeps the private working data (job database, subscribers) encrypted on a separate git branch,
so it can live in a public repository without anyone being able to read it.

    python state.py pull     download + decrypt into sarkari.db and bot_state.json (if present)
    python state.py push     encrypt + replace the branch with a single fresh commit (no history kept)
    python state.py newkey   print a new random STATE_PASSWORD

Environment: STATE_PASSWORD (required), GITHUB_REPOSITORY and GITHUB_TOKEN (set by GitHub Actions),
STATE_BRANCH (default "state").
"""
import base64
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
import tempfile

from cryptography.fernet import Fernet, InvalidToken

FILES = ["sarkari.db", "bot_state.json"]
HASH_FILE = ".state_hash"


def digest() -> str:
    h = hashlib.sha256()
    for name in FILES:
        if os.path.exists(name):
            if name.endswith(".db"):
                import sqlite3
                db = sqlite3.connect(name)
                for line in db.iterdump():               # content, not file layout
                    h.update(line.encode())
                db.close()
            else:
                h.update(open(name, "rb").read())
        h.update(b"|")
    return h.hexdigest()


def fernet() -> Fernet:
    pw = os.environ.get("STATE_PASSWORD", "")
    if len(pw) < 20:
        sys.exit("STATE_PASSWORD must be set (at least 20 characters). Run: python state.py newkey")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(pw.encode()).digest()))


def remote() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if os.environ.get("STATE_REMOTE"):                  # tests / local use
        return os.environ["STATE_REMOTE"]
    if not repo or not token:
        sys.exit("GITHUB_REPOSITORY and GITHUB_TOKEN must be set (they are, inside GitHub Actions)")
    return f"https://x-access-token:{token}@github.com/{repo}.git"


def git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def pull():
    f = fernet()
    branch = os.environ.get("STATE_BRANCH", "state")
    tmp = tempfile.mkdtemp()
    try:
        r = git("clone", "--depth", "1", "--branch", branch, remote(), tmp, check=False)
        if r.returncode != 0:
            print(f"no '{branch}' branch yet: starting with empty data")
            return
        for name in FILES:
            enc = os.path.join(tmp, name + ".enc")
            if os.path.exists(enc):
                try:
                    data = f.decrypt(open(enc, "rb").read())
                except InvalidToken:
                    sys.exit(f"cannot decrypt {name}: STATE_PASSWORD is different from the one used to save it")
                with open(name, "wb") as fh:
                    fh.write(data)
                print(f"restored {name} ({len(data)} bytes)")
        with open(HASH_FILE, "w") as fh:
            fh.write(digest())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def push():
    f = fernet()
    if os.path.exists(HASH_FILE) and open(HASH_FILE).read() == digest():
        print("no changes to save")
        return
    branch = os.environ.get("STATE_BRANCH", "state")
    tmp = tempfile.mkdtemp()
    try:
        git("init", "-q", "-b", branch, tmp)
        git("config", "user.name", "exammatch-bot", cwd=tmp)
        git("config", "user.email", "exammatch-bot@users.noreply.github.com", cwd=tmp)
        saved = []
        for name in FILES:
            if os.path.exists(name):
                if name.endswith(".db"):                   # fold the WAL into the main file first
                    import sqlite3
                    db = sqlite3.connect(name)
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    db.close()
                with open(os.path.join(tmp, name + ".enc"), "wb") as fh:
                    fh.write(f.encrypt(open(name, "rb").read()))
                saved.append(name)
        if not saved:
            print("nothing to save")
            return
        with open(os.path.join(tmp, "README.md"), "w") as fh:
            fh.write("Encrypted working data for ExamMatch. Replaced on every run; do not edit.\n")
        git("add", ".", cwd=tmp)
        git("commit", "-q", "-m", "state", cwd=tmp)
        r = git("push", "-q", "--force", remote(), f"HEAD:{branch}", cwd=tmp, check=False)
        if r.returncode != 0:
            sys.exit("push failed: " + r.stderr.replace(os.environ.get("GITHUB_TOKEN", "<none>"), "***")[-500:])
        print(f"saved {', '.join(saved)} to branch '{branch}'")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "pull":
        pull()
    elif cmd == "push":
        push()
    elif cmd == "newkey":
        print(secrets.token_urlsafe(32))
    else:
        print(__doc__)
        sys.exit(1)
