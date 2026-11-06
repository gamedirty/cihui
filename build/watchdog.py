# -*- coding: utf-8 -*-
"""Watchdog: probe until the shark-block limit lifts, then finish all missing images."""
import json, subprocess, sys, time, os

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
CHECK_INTERVAL = 45 * 60          # probe every 45 min
MAX_PROBES = 60                   # give up after ~45 hours

PROBE_PROMPT = ("Modern editorial flat illustration of a small green sprout in a clay pot, "
                "rich vibrant saturated colors, clean minimalist composition, "
                "no text, no letters, no watermark")


def probe_ok():
    try:
        r = subprocess.run(
            ["dreamina", "text2image", f"--prompt={PROBE_PROMPT}",
             "--ratio=1:1", "--resolution_type=2k", "--poll=60"],
            capture_output=True, text=True, timeout=180)
        data = json.loads(r.stdout)
        status = data.get("gen_status")
        reason = data.get("fail_reason", "")
        print(f"[probe] gen_status={status} {reason[:80]}", flush=True)
        return status == "success"
    except Exception as e:
        print(f"[probe] error {e}", flush=True)
        return False


def count_missing():
    miss = 0
    for site, n, d in (("en", 1048, "english"), ("zh", 980, "chinese")):
        for i in range(1, n + 1):
            p = os.path.join(ROOT, d, "images", f"{site}_{i:03d}.webp")
            if not (os.path.exists(p) and os.path.getsize(p) > 4096):
                miss += 1
    return miss


def run_gen():
    r = subprocess.run([sys.executable, os.path.join(BASE, "gen_images.py")],
                       capture_output=True, text=True)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "no output"
    print(f"[gen] {tail}", flush=True)
    return r.returncode == 0


def main():
    for attempt in range(1, MAX_PROBES + 1):
        miss = count_missing()
        print(f"=== round {attempt}: missing={miss} ===", flush=True)
        if miss == 0:
            print("ALL COMPLETE", flush=True)
            return 0
        if probe_ok():
            # limit lifted: run the real batch (repeat while progress is being made)
            while count_missing() > 0:
                ok = run_gen()
                if not ok:
                    break  # blocked again mid-run; go back to probing
                if count_missing() == 0:
                    print("ALL COMPLETE", flush=True)
                    return 0
        print(f"still blocked or incomplete; sleeping {CHECK_INTERVAL//60} min", flush=True)
        time.sleep(CHECK_INTERVAL)
    print("GAVE UP after max probes", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
