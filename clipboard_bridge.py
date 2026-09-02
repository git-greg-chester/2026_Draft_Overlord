"""Clipboard bridge: get picks out of the draft room without any network call.

Chrome's Private Network Access blocks an HTTPS page from POSTing to
127.0.0.1, which kills the direct bridge. The clipboard is not subject to
that, so the draft room copies a JSON list of names and this reads it.

In the draft room console, run the copy snippet (see README). Then:

    python3 clipboard_bridge.py

It watches the clipboard and pushes any new list of names to the board.
"""

import argparse
import json
import subprocess
import sys
import time

import requests


def pbpaste() -> str:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True,
                              timeout=5).stdout
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8778")
    ap.add_argument("--interval", type=float, default=1.5)
    args = ap.parse_args()

    print(f"watching clipboard -> {args.server}/api/scrape")
    print("run the copy snippet in the draft room console; ctrl-c to stop\n")

    last = None
    while True:
        raw = pbpaste().strip()
        if raw and raw != last and raw.startswith(("[", "{")):
            try:
                data = json.loads(raw)
                names = data if isinstance(data, list) else data.get("names", [])
                mine = [] if isinstance(data, list) else data.get("mine", [])
            except json.JSONDecodeError:
                last = raw
                time.sleep(args.interval)
                continue
            try:
                r = requests.post(f"{args.server}/api/scrape",
                                  json={"names": names, "mine": mine}, timeout=10)
                j = r.json()
                extra = f" | unmatched: {', '.join(j['unmatched'][:5])}" if j.get("unmatched") else ""
                print(f"  {time.strftime('%H:%M:%S')}  {len(names)} names -> "
                      f"matched {j['matched']}{extra}")
            except requests.RequestException as e:
                print(f"  push failed: {e}")
            last = raw
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
