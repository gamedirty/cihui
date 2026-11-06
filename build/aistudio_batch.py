# -*- coding: utf-8 -*-
"""Regenerate the whole vocabulary set with Google AI Studio (gemini-nano-banana-2.1).

- Reads the same cleaned word lists as the dreamina pipeline.
- Saves to aistudio/<english|chinese>/images/<site>_NNN.webp  (dreamina output untouched)
- Model chosen via ?model= URL param; overlays force-cleared before every click.
- Resumable + rate-limit backoff.
"""
import json, glob, os, sys, time, base64, io, subprocess
from pathlib import Path

sys.path.insert(0, r"D:\aistudiocli\src")

from aistudio_cli.config import load_settings
from aistudio_cli.browser import BrowserSession
from aistudio_cli import page_selectors as sel
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
OUT = {"en": os.path.join(ROOT, "aistudio", "english", "images"),
       "zh": os.path.join(ROOT, "aistudio", "chinese", "images")}
NEW_CHAT = "https://aistudio.google.com/prompts/new_chat"

MODEL = "gemini-nano-banana-2.1"
PAGE_LIFETIME = 25
RETRY_ROUNDS = 96
BACKOFF_SECONDS = 15 * 60

TEMPLATES = {
    "en": ("Modern editorial flat illustration of {s}, rich vibrant saturated colors, bold confident "
           "color palette, clean minimalist composition, adult magazine art style, elegant shapes "
           "with subtle grain texture, generous negative space, warm cream background, "
           "no text, no letters, no watermark"),
    "zh": ("Elegant modern Chinese gouache ink illustration of {s}, rich saturated vibrant watercolor "
           "tones, refined composition with white space, mature artistic style, subtle paper texture, "
           "poetic yet colorful, no text, no letters, no watermark"),
}


def load_all():
    jobs = []
    for site in ("en", "zh"):
        idx = 0
        for path in sorted(f for f in glob.glob(os.path.join(BASE, f"words_{site}*.json"))
                           if not f.endswith(".bak")):
            with open(path, encoding="utf-8") as f:
                for w in json.load(f)["words"]:
                    idx += 1
                    jobs.append((site, idx, w[0], w[8]))
    return jobs


def target_path(site, idx):
    return os.path.join(OUT[site], f"{site}_{idx:03d}.webp")


def already_done(site, idx):
    p = target_path(site, idx)
    return os.path.exists(p) and os.path.getsize(p) > 4096


def save_webp(raw_bytes, path):
    im = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    im.thumbnail((512, 512), Image.LANCZOS)
    tmp = path + ".tmp.webp"
    im.save(tmp, "WEBP", quality=80, method=4)
    os.replace(tmp, path)


class RateLimitError(Exception):
    pass


class Session:
    def __init__(self, ctx):
        self.page = ctx.new_page()

    def clear(self):
        self.page.evaluate(
            "document.querySelectorAll('.cdk-overlay-backdrop').forEach(el => el.remove())")

    def open_page(self):
        self.page.goto(f"{NEW_CHAT}?model={MODEL}", wait_until="domcontentloaded")
        self.page.wait_for_timeout(9000)
        self.clear()
        cur = self.current_model()
        if cur != MODEL:
            print(f"  [model] url param gave {cur!r}; selecting manually", flush=True)
            self.page.locator(sel.MODEL_SELECTOR_CARD).click()
            self.page.wait_for_timeout(1500)
            self.clear()
            self.page.get_by_role("button", name="All", exact=True).click()
            self.page.wait_for_timeout(1200)
            self.clear()
            row = self.page.locator(f'button[id="model-carousel-row-models/{MODEL}"]')
            if row.count() == 0:
                raise RuntimeError(f"model row not found: {MODEL}")
            row.first.click()
            self.page.wait_for_timeout(1500)
        # settings: images only + 1:1
        radio = self.page.locator('mat-radio-button:has-text("Images only")')
        if radio.count() > 0 and radio.first.get_attribute("aria-checked") != "true":
            radio.first.click()
            self.page.wait_for_timeout(600)
        ar = self.page.locator(sel.combobox("Aspect ratio"))
        if ar.count() > 0:
            self.clear()
            ar.first.click()
            self.page.wait_for_timeout(800)
            opts = self.page.locator("mat-option")
            for i, t in enumerate(opts.all_inner_texts()):
                if t.strip() == "1:1":
                    opts.nth(i).click()
                    break
            self.page.wait_for_timeout(600)
        self.clear()

    def current_model(self):
        loc = self.page.locator('ms-model-selector [data-test-id="model-name"]')
        return loc.inner_text().strip() if loc.count() > 0 else ""

    def generate(self, prompt, timeout_s=180):
        page = self.page
        base_turns = page.locator(sel.CHAT_TURN).count()
        page.locator(sel.PROMPT_TEXTAREA).fill(prompt)
        self.clear()
        page.evaluate("() => document.querySelector('ms-run-button button').click()")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            page.wait_for_timeout(4000)
            img = page.locator("ms-chat-turn").last.locator(sel.TURN_IMAGE)
            if img.count() > 0 and img.first.evaluate("i => i.naturalWidth") >= 256:
                src = img.first.get_attribute("src") or ""
                if src.startswith("data:"):
                    return base64.b64decode(src.split(",", 1)[1])
                if src.startswith("blob:"):
                    data_url = page.evaluate(
                        """async s => { const r = await fetch(s); const b = await r.blob();
                             return await new Promise(res => { const fr = new FileReader();
                               fr.onload = () => res(fr.result); fr.readAsDataURL(b); }); }""", src)
                    return base64.b64decode(data_url.split(",", 1)[1])
                resp = page.context.request.get(src)
                return resp.body()
            err = page.locator("ms-chat-turn").last.locator(sel.TURN_ERROR)
            if err.count() > 0 and err.inner_text().strip():
                msg = err.inner_text()[:180]
                if "rate limit" in msg.lower():
                    raise RateLimitError(msg)
                raise RuntimeError("model error: " + msg)
        raise TimeoutError("image not returned in time")


def main():
    for d in OUT.values():
        os.makedirs(d, exist_ok=True)
    jobs = load_all()
    total = len(jobs)
    print(f"total words: {total}", flush=True)

    settings = load_settings()
    done = fails = 0
    failed_list = []
    t0 = time.time()

    for browser_attempt in range(1, 21):   # browser can die; rebuild and continue
        try:
            with BrowserSession(settings, "default", visible=False) as ctx:
                sess = Session(ctx)
                sess.open_page()
                page_n = 0

                for rnd in range(1, RETRY_ROUNDS + 1):
                    still = [(s, i, t, sc) for s, i, t, sc in jobs if not already_done(s, i)]
                    if not still:
                        break
                    if rnd > 1:
                        print(f"retry round {rnd}: {len(still)} left, backoff {BACKOFF_SECONDS}s", flush=True)
                        time.sleep(BACKOFF_SECONDS)
                    consecutive_fails = 0

                    for site, idx, term, scene in still:
                        if already_done(site, idx):
                            continue
                        if page_n >= PAGE_LIFETIME:
                            sess.open_page()
                            page_n = 0
                        prompt = TEMPLATES[site].format(s=scene)
                        try:
                            raw = sess.generate(prompt)
                            save_webp(raw, target_path(site, idx))
                            done += 1
                            page_n += 1
                            consecutive_fails = 0
                        except RateLimitError:
                            print(f"  [rate-limited] at {site}_{idx:03d}: sleeping {BACKOFF_SECONDS}s "
                                  "then retrying the whole batch", flush=True)
                            time.sleep(BACKOFF_SECONDS)
                            break   # leave this round; next round re-probes from the first missing word
                        except Exception as e:
                            fails += 1
                            consecutive_fails += 1
                            print(f"  [fail] {site}_{idx:03d} ({term}): {str(e)[:100]}", flush=True)
                            sess.open_page()   # fresh page after any failure
                            page_n = 0
                            if consecutive_fails >= 3:
                                print(f"  3 consecutive fails -> backoff {BACKOFF_SECONDS}s", flush=True)
                                time.sleep(BACKOFF_SECONDS)
                                consecutive_fails = 0
                        n_done = done + fails
                        if n_done % 10 == 0:
                            left = sum(1 for s, i, t, sc in jobs if not already_done(s, i))
                            rate = n_done / max(time.time() - t0, 1)
                            print(f"[{n_done}/{total}] done={done} fail={fails} left={left} "
                                  f"elapsed={int(time.time()-t0)}s rate={rate*3600:.0f}/h", flush=True)

        except Exception as e:
            left = sum(1 for s, i, t, sc in jobs if not already_done(s, i))
            if left == 0:
                break
            print(f"[browser crash #{browser_attempt}] {str(e)[:120]}", flush=True)
            # Hard-clean leftover Chrome on this profile + remove lockfile,
            # because browser.py's orphan cleanup skips processes whose parent is alive.
            profile_dir = settings.profile_dir("default")
            ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                  f"Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('--user-data-dir={profile_dir}') }} | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=60)
            time.sleep(5)
            lock = Path(profile_dir) / "lockfile"
            try:
                if lock.exists():
                    lock.unlink()
                    print("  removed stale lockfile", flush=True)
            except OSError:
                pass
            print(f"  left={left}; rebuilding browser in 15s...", flush=True)
            time.sleep(15)
            continue
        break

    left = sum(1 for s, i, t, sc in jobs if not already_done(s, i))
    print(f"ALL DONE done={done} fail={fails} left={left} time={int(time.time()-t0)}s", flush=True)
    if left:
        still_ids = [f"{s}_{i:03d}" for s, i, t, sc in jobs if not already_done(s, i)]
        with open(os.path.join(BASE, "aistudio_failed.json"), "w") as f:
            json.dump(still_ids, f)
    return 0 if left == 0 else 1

    left = sum(1 for s, i, t, sc in jobs if not already_done(s, i))
    print(f"ALL DONE done={done} fail={fails} left={left} time={int(time.time()-t0)}s", flush=True)
    if failed_list:
        with open(os.path.join(BASE, "aistudio_failed.json"), "w") as f:
            json.dump(failed_list, f)
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
