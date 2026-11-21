#!/usr/bin/env python3
"""
One-time move of existing Telegram subscribers from the old GitHub bot (encrypted bot_state.json on the
'state' branch) to the Cloudflare bot.

    python worker/migrate_subscribers.py <worker url>
Needs: STATE_PASSWORD (env, or the file Documents/exammatch_STATE_PASSWORD.txt) and ADMIN_TOKEN (env).
"""
import json
import os
import subprocess
import sys
import tempfile

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "https://github.com/its0su0rj/sarkari_sync.git"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    worker = sys.argv[1].rstrip("/")
    admin = os.environ.get("ADMIN_TOKEN")
    if not admin:
        sys.exit("ADMIN_TOKEN is not set")
    env = dict(os.environ, STATE_REMOTE=REPO)
    if not env.get("STATE_PASSWORD"):
        pw_file = os.path.join(os.path.expanduser("~"), "Documents", "exammatch_STATE_PASSWORD.txt")
        if not os.path.exists(pw_file):
            sys.exit(f"STATE_PASSWORD not set and {pw_file} not found")
        env["STATE_PASSWORD"] = open(pw_file, encoding="utf-8-sig").read().strip()

    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "state.py"), "pull", "state", "bot_state.json"],
                           cwd=tmp, env=env, capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            sys.exit("could not read the old subscribers")
        path = os.path.join(tmp, "bot_state.json")
        subs = json.load(open(path, encoding="utf-8")).get("subscribers", {}) if os.path.exists(path) else {}

    print(f"found {len(subs)} subscribers in the old bot")
    if not subs:
        return
    resp = requests.post(f"{worker}/admin/import", json={"subscribers": subs},
                         headers={"Authorization": f"Bearer {admin}"}, timeout=60)
    resp.raise_for_status()
    print("imported into Cloudflare:", resp.json()["imported"])


if __name__ == "__main__":
    main()
