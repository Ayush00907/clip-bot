"""Clipping bot: find permitted source -> pick best moment -> edit vertical Short -> upload."""
import json
import os
import shutil
import sys
import time
import traceback

import yaml

from . import media, pick, render, sources

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state", "posted.json")
WORK = os.path.join(ROOT, "work")
OUT = os.path.join(ROOT, "output")


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"posted": []}


def save_state(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def process(cand, cfg, dry_run):
    shutil.rmtree(WORK, ignore_errors=True)
    src = media.download(cand["url"], WORK)
    info = media.probe(src)
    loud = media.loudness_per_second(src)
    segments, words, lang = media.transcribe(src, cfg["transcription"]["model"])
    meta = pick.choose(loud, segments, info["duration"], cand, cfg["clip"])
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"{cand['id']}_{int(meta['start'])}.mp4")
    render.render(src, info, meta["start"], meta["end"], words, meta["hook"], cand["credit"],
                  cfg["channel_name"], cand["layout"], out,
                  fonts_dir=os.path.join(ROOT, "fonts") if os.path.isdir(os.path.join(ROOT, "fonts")) else None)
    with open(out.replace(".mp4", ".json"), "w") as f:
        json.dump({"source": cand, "meta": meta}, f, indent=2, ensure_ascii=False)
    if dry_run:
        print(f"[dry-run] rendered {out} (not uploaded)")
        return None, meta
    from .upload import upload
    cat = cfg["categories"].get(cand["category"], cfg["categories"]["default"])
    return upload(out, cand, meta, cat, cfg.get("privacy", "public")), meta


def main():
    if "--auth" in sys.argv:
        from . import yt
        yt.client()
        print("Logged in. token.json created - copy its contents into the YT_TOKEN secret.")
        return 0
    dry_run = os.environ.get("DRY_RUN", "0") == "1"
    with open(os.path.join(ROOT, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    with open(os.path.join(ROOT, "sources.yaml")) as f:
        srcs = yaml.safe_load(f) or {}
    state = load_state()
    posted = {p["source_id"] for p in state["posted"]}

    cands = sources.gather(srcs, cfg, posted)
    if not cands:
        print("No usable sources. Add videos/channels to sources.yaml.")
        return 0
    done, tries = 0, 0
    for cand in cands:
        if done >= cfg.get("uploads_per_run", 1) or tries >= 4:
            break
        tries += 1
        print(f"\n=== {cand['title']} ({cand['permission']}, {cand['duration']}s) ===")
        try:
            vid, meta = process(cand, cfg, dry_run)
        except media.BlockedError as e:
            print(f"\nSTOPPING: {e}")
            break
        except Exception:
            traceback.print_exc()
            continue
        done += 1
        if not dry_run:
            state["posted"].append({
                "source_id": cand["id"], "short_id": vid, "start": meta["start"], "end": meta["end"],
                "title": meta["title"], "credit": cand["credit"], "permission": cand["permission"],
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            save_state(state)
    print(f"\nDone: {done} short(s).")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
