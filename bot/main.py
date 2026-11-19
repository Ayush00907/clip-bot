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


class WrongLanguage(Exception):
    pass


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
    section = None
    if cand.get("live"):
        src = media.download_live(cand["url"], WORK)
    elif cand["duration"] > cfg["discovery"]["max_source_seconds"]:
        done_near = {p.get("section_start", -9999) for p in load_state()["posted"] if p["source_id"] == cand["id"]}
        section = media.hottest_window(cand["url"], cand["duration"], skip=done_near)
        src = media.download(cand["url"], WORK, section)
    else:
        src = media.download(cand["url"], WORK)
    info = media.probe(src)
    loud = media.loudness_per_second(src)
    try:
        segments, words, lang = media.transcribe(src, cfg["transcription"]["model"])
    except Exception:
        traceback.print_exc()
        print("[whisper] transcription failed - picking by loudness, no captions")
        segments, words, lang = [], [], None
    allowed = cfg.get("languages", ["hi", "en"])
    if lang and len(words) > 30 and lang not in allowed and cand["permission"] == "creative_commons":
        raise WrongLanguage(f"spoken language is '{lang}', not {allowed}")
    meta = pick.choose(loud, segments, info["duration"], cand, cfg["clip"])
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"{cand['id']}_{int(meta['start'])}.mp4")
    camp = cand.get("campaign") or {}
    render.render(src, info, meta["start"], meta["end"], words, meta["hook"], cand["credit"],
                  "" if camp.get("no_watermark") else cfg["channel_name"], cand["layout"], out,
                  fonts_dir=os.path.join(ROOT, "fonts") if os.path.isdir(os.path.join(ROOT, "fonts")) else None)
    with open(out.replace(".mp4", ".json"), "w") as f:
        json.dump({"source": cand, "meta": meta}, f, indent=2, ensure_ascii=False)
    if dry_run:
        print(f"[dry-run] rendered {out} (not uploaded)")
        return None, meta
    from .upload import upload
    cat = cfg["categories"].get(cand["category"], cfg["categories"]["default"])
    meta["section_start"] = section[0] if section else None
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
    counts = {}
    for p in state["posted"]:
        if p.get("live"):
            continue  # a live marathon can be clipped any number of times
        counts[p["source_id"]] = counts.get(p["source_id"], 0) + (0 if p.get("section_start") is not None else 99)
        if p.get("section_start") is not None:
            counts[p["source_id"]] += 1
    # normal videos: clip once; long streams: up to 6 different moments
    posted = {k for k, n in counts.items() if n >= 6} | set(state.get("skipped", []))

    camp_posts = [p.get("campaign") for p in state["posted"] if p.get("campaign")]
    cfg["_last_campaign"] = camp_posts[-1] if camp_posts else None
    cands = sources.gather(srcs, cfg, posted)
    if not cands:
        print("No usable sources. Add videos/channels to sources.yaml.")
        return 0
    done, tries = 0, 0
    for cand in cands:
        if done >= cfg.get("uploads_per_run", 1) or tries >= 4 or cands.index(cand) >= 10:
            break
        tries += 1
        print(f"\n=== {cand['title']} ({cand['permission']}, {cand['duration']}s) ===")
        try:
            vid, meta = process(cand, cfg, dry_run)
        except WrongLanguage as e:
            print(f"[skip] {e}")
            if not dry_run:
                state.setdefault("skipped", []).append(cand["id"])
                save_state(state)
            tries -= 1  # cheap check, don't count it
            continue
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
                "section_start": meta.get("section_start"),
                "live": bool(cand.get("live")),
                "campaign": (cand.get("campaign") or {}).get("name"),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            save_state(state)
    print(f"\nDone: {done} short(s).")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
