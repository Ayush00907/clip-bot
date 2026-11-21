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


SUBMIT = os.path.join(ROOT, "SUBMIT_THESE.md")


def add_to_submit_list(short_id, cand, meta):
    """Newest first: every uploaded campaign Short + where to submit it."""
    ist = time.strftime("%d %b %Y, %I:%M %p IST", time.gmtime(time.time() + 19800))
    row = (f"| {ist} | {cand['campaign'].get('name')} | https://youtube.com/shorts/{short_id} "
           f"| [Submit here]({cand.get('proof', '')}) | {meta['title'][:60]} |\n")
    head = ("# Campaign Shorts to submit\n\nNewest at the top. Open the campaign link, click **Submit clip**, "
            "paste the Short link. (LACY & Clix want it within 30 min of posting.)\n\n"
            "| Uploaded | Campaign | Short | Campaign page | Title |\n|---|---|---|---|---|\n")
    rows = ""
    if os.path.exists(SUBMIT):
        txt = open(SUBMIT, encoding="utf-8").read()
        rows = txt.split("|---|---|---|---|---|\n", 1)[-1] if "|---|" in txt else ""
    with open(SUBMIT, "w", encoding="utf-8") as f:
        f.write(head + row + rows)


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
    overlay = None
    if camp.get("overlay_url"):
        import requests
        overlay = os.path.join(WORK, "overlay.png")
        r = requests.get(camp["overlay_url"], timeout=60)
        r.raise_for_status()
        raw = os.path.join(WORK, "overlay_raw")
        open(raw, "wb").write(r.content)
        media.run(["ffmpeg", "-y", "-v", "error", "-i", raw, overlay])  # webp -> png (keeps transparency)
    render.render(src, info, meta["start"], meta["end"], words, meta["hook"], cand["credit"],
                  "" if camp.get("no_watermark") else cfg["channel_name"], cand["layout"], out,
                  overlay=overlay,
                  fonts_dir=os.path.join(ROOT, "fonts") if os.path.isdir(os.path.join(ROOT, "fonts")) else None)
    with open(out.replace(".mp4", ".json"), "w") as f:
        json.dump({"source": cand, "meta": meta}, f, indent=2, ensure_ascii=False)
    from .upload import write_upload_sheet
    cat = cfg["categories"].get(cand["category"], cfg["categories"]["default"])
    meta["section_start"] = section[0] if section else None
    write_upload_sheet(out.replace(".mp4", ".txt"), cand, meta, cat)
    if dry_run or not cfg.get("upload_to_youtube", True):
        print(f"[made] {out} + .txt (not uploaded - upload it yourself)")
        return None, meta
    from .upload import upload
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
    os.environ["TITLE_STYLE"] = cfg.get("title_style", "hinglish")
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

    # when each campaign was last used (index in the log) -> take turns between campaigns
    cfg["_campaign_last"] = {p["campaign"]: i for i, p in enumerate(state["posted"]) if p.get("campaign")}
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
            if vid and cand.get("campaign"):
                add_to_submit_list(vid, cand, meta)
    print(f"\nDone: {done} short(s).")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
