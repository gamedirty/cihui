# -*- coding: utf-8 -*-
"""Batch-generate vocabulary illustrations via dreamina CLI (Jimeng 5.0).
- Skips existing images (resumable)
- 6-way concurrency, 3 retries per word
- Downloads the 2048px PNG and compresses to 512px WebP
"""
import json, subprocess, os, sys, io, time, glob, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
OUT_DIRS = {"en": os.path.join(ROOT, "english", "images"),
            "zh": os.path.join(ROOT, "chinese", "images")}
WORKERS = 6

TEMPLATES = {
    "en": ("Modern editorial flat illustration of {s}, rich vibrant saturated colors, bold confident "
           "color palette, clean minimalist composition, adult magazine art style, elegant shapes "
           "with subtle grain texture, generous negative space, warm cream background, "
           "no text, no letters, no watermark"),
    "zh": ("Elegant modern Chinese gouache ink illustration of {s}, rich saturated vibrant watercolor "
           "tones, refined composition with white space, mature artistic style, subtle paper texture, "
           "poetic yet colorful, no text, no letters, no watermark"),
}


def load(site):
    """Merge base + extra word files in name order (words_en.json, words_en_a.json, ...)."""
    words = []
    for path in sorted(glob.glob(os.path.join(BASE, f"words_{site}*.json"))):
        with open(path, encoding="utf-8") as f:
            words.extend(json.load(f)["words"])
    return words


def make_image(site, idx, scene):
    out = os.path.join(OUT_DIRS[site], f"{site}_{idx:03d}.webp")
    if os.path.exists(out) and os.path.getsize(out) > 4096:
        return True, "cached"
    prompt = TEMPLATES[site].format(s=scene)
    for attempt in range(3):
        try:
            r = subprocess.run(
                ["dreamina", "text2image",
                 f"--prompt={prompt}",
                 "--ratio=1:1", "--resolution_type=2k",
                 "--poll=120"],
                capture_output=True, text=True, timeout=200)
            data = json.loads(r.stdout)
            if data.get("gen_status") == "success":
                url = data["result_json"]["images"][0]["image_url"]
                raw = urllib.request.urlopen(url, timeout=90).read()
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                im.thumbnail((512, 512), Image.LANCZOS)
                im.save(out, "WEBP", quality=80, method=4)
                return True, "ok"
        except Exception as e:
            print(f"  [retry] {site}_{idx:03d} attempt {attempt+1}: {e}", flush=True)
            time.sleep(3)
    return False, "failed"


def main():
    for d in OUT_DIRS.values():
        os.makedirs(d, exist_ok=True)
    jobs = []
    for site in ("en", "zh"):
        for i, w in enumerate(load(site), 1):
            jobs.append((site, i, w[8]))
    print(f"Total jobs: {len(jobs)}", flush=True)

    ok = fail = 0
    failed = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(make_image, s, i, sc): (s, i) for s, i, sc in jobs}
        for n, fut in enumerate(as_completed(futs), 1):
            s, i = futs[fut]
            good, how = fut.result()
            ok += good
            fail += not good
            if not good:
                failed.append(f"{s}_{i:03d}")
            if n % 10 == 0 or n == len(jobs):
                print(f"[{n}/{len(jobs)}] ok={ok} fail={fail} elapsed={int(time.time()-t0)}s", flush=True)
    print(f"DONE ok={ok} fail={fail} time={int(time.time()-t0)}s", flush=True)
    if failed:
        print("FAILED:", ",".join(failed), flush=True)
        with open(os.path.join(BASE, "failed.json"), "w") as f:
            json.dump(failed, f)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
