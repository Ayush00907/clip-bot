#!/usr/bin/env python3
"""ClipVerse clipper: turn any video URL (or local file) into vertical Shorts.

  python3 clip_url.py "https://www.youtube.com/watch?v=..."            # 3 clips
  python3 clip_url.py URL -n 5 --layout center_crop --style english
  python3 clip_url.py my_gameplay.mp4 -n 4 --credit "@MyChannel"

For each clip it finds the best moment (Gemini if GEMINI_API_KEY is set, otherwise
loudness peaks), crops to 1080x1920, adds a hook banner + word-by-word captions,
and writes clips/<name>/clip_N.mp4 plus clip_N.txt with a suggested title.
Use it on videos you own or have permission to clip.
"""
import argparse
import os
import re
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot import media, pick, render  # noqa: E402


def upload_clip(path, meta, a, start):
    """Upload one clip with title, description, hashtags and tags; log it in state/posted.json."""
    import time
    import yaml
    from bot.main import load_state, save_state
    from bot.upload import upload
    cfg = yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")))
    cat = cfg["categories"]["gaming"]
    url = a.source if a.source.startswith("http") else ""
    cand = {"credit": a.credit or "original creator", "url": url or "(original upload)",
            "permission": a.permission, "campaign": None, "category": "gaming"}
    vid = upload(path, cand, meta, cat, cfg.get("privacy", "public"))
    state = load_state()
    state["posted"].append({"source_id": url or os.path.basename(a.source), "short_id": vid, "title": meta["title"],
                            "permission": a.permission, "credit": a.credit, "section_start": int(start),
                            "via": "clip_url", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    save_state(state)
    return vid


def main():
    ap = argparse.ArgumentParser(description="Clip a video URL or file into vertical Shorts")
    ap.add_argument("source", help="video URL (YouTube, Twitch, ...) or a local video file")
    ap.add_argument("-n", "--count", type=int, default=3, help="how many clips (default 3)")
    ap.add_argument("--layout", choices=["blur_fit", "center_crop"], default="blur_fit",
                    help="blur_fit = whole frame on blurred background; center_crop = full-screen crop")
    ap.add_argument("--style", choices=["hinglish", "english"], default="hinglish", help="hook/title language")
    ap.add_argument("--min", type=int, default=20, help="shortest clip in seconds")
    ap.add_argument("--max", type=int, default=50, help="longest clip in seconds")
    ap.add_argument("--credit", default="", help="credit line shown at the bottom, e.g. '@Creator'")
    ap.add_argument("--model", default="base", help="Whisper model: tiny | base | small")
    ap.add_argument("--out", default="clips", help="output folder")
    ap.add_argument("--full", action="store_true", help="download the whole video even if it is very long")
    ap.add_argument("--upload", action="store_true", help="upload every clip to the YouTube channel (needs token.json)")
    ap.add_argument("--permission", choices=["own", "clipping_campaign", "written_permission", "creative_commons"],
                    default="own", help="why you may use this video (shown in the description)")
    a = ap.parse_args()

    os.environ["TITLE_STYLE"] = a.style
    work = os.path.abspath(".clip_work")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)

    if os.path.exists(a.source):
        src, name = a.source, os.path.splitext(os.path.basename(a.source))[0]
        title = name
    else:
        m = re.search(r"(?:v=|youtu\.be/|shorts/|videos/)([\w-]+)", a.source)
        name = m.group(1) if m else "video"
        title = name
        section = None
        if not a.full:
            try:
                import json
                import subprocess
                r = subprocess.run(["yt-dlp", *media._cookie_args(), "--no-playlist", "--skip-download",
                                    "--print", "%(duration)s|%(title)s", a.source], capture_output=True, text=True)
                line = (r.stdout.strip().splitlines() or ["0|"])[-1] if r.returncode == 0 else "0|"
                d, _, t = line.partition("|")
                dur = int(float(d)) if d.replace(".", "").isdigit() else 0
                title = t.strip() or title
                print(f"Video length: {dur // 60} min")
                if dur > 2400:  # long stream: only the most replayed ~10 minutes
                    section = media.hottest_window(a.source, dur)
            except Exception as e:
                print("could not read video info:", e)
        src = media.download(a.source, work, section)

    info = media.probe(src)
    if info["duration"] > 2700 and not a.full:
        # safety net: never transcribe hours of video - keep the loudest 10 minutes
        lo = media.loudness_per_second(src)
        win = 600
        sums = np.convolve(lo - lo.min(), np.ones(win), "valid") if len(lo) > win else np.array([0])
        st = int(np.argmax(sums))
        cut = os.path.join(work, "part.mp4")
        media.run(["ffmpeg", "-y", "-v", "error", "-ss", str(st), "-t", str(win), "-i", src, "-c", "copy", cut])
        print(f"Long video: using the loudest 10 minutes starting at {st // 60} min")
        src = cut
        info = media.probe(src)
    print(f"Video: {info['w']}x{info['h']}, {info['duration'] / 60:.1f} min")
    loud = media.loudness_per_second(src)
    try:
        segments, words, _ = media.transcribe(src, a.model)
    except Exception as e:
        print("transcription failed, picking by loudness only:", e)
        segments, words = [], []

    whole = info["duration"] <= 60  # short video: the whole thing becomes one Short
    if whole:
        a.count = 1
        print("Video is under 1 minute: making it one Short as a whole")
    out_dir = os.path.join(a.out, name)
    os.makedirs(out_dir, exist_ok=True)
    clip = {"min_seconds": a.min, "max_seconds": a.max, "target_seconds": (a.min + a.max) // 2}
    used = []
    for i in range(1, a.count + 1):
        free = lambda s, e: all(e <= u0 or s >= u1 for u0, u1 in used)
        segs = [s for s in segments if free(s["start"], s["end"])]
        l2 = loud.copy()
        for u0, u1 in used:
            l2[int(u0):int(u1) + 1] = l2.min() - 30  # don't pick the same moment twice
        cand = {"title": title, "channel": a.credit or title, "category": "gaming"}
        if whole:
            c1 = {"min_seconds": max(1, int(info["duration"]) - 1), "max_seconds": int(info["duration"]) + 1,
                  "target_seconds": int(info["duration"])}
            meta = pick.choose(l2, segs, info["duration"], cand, c1)
            meta["start"], meta["end"] = 0.0, info["duration"]
        else:
            meta = pick.choose(l2, segs, info["duration"], cand, clip)
        st, en = meta["start"], meta["end"]
        for u0, u1 in used:  # trim small overlaps with clips already made
            if st < u0 < en:
                en = u0
            if st < u1 < en:
                st = u1
        if not whole and (en - st < a.min or not free(st, en)):
            print("no more distinct moments found")
            break
        used.append((st, en))
        out = os.path.join(out_dir, f"clip_{i}.mp4")
        render.render(src, info, st, en, words, meta["hook"], a.credit or "", "", a.layout, out)
        if os.path.exists(out[:-4] + ".ass"):
            os.remove(out[:-4] + ".ass")
        with open(out[:-4] + ".txt", "w", encoding="utf-8") as f:
            f.write(f"TITLE: {meta['title']}\nHOOK: {meta['hook']}\nMOMENT: {st:.0f}s - {en:.0f}s\n"
                    f"NOTE: {meta.get('context', '')}\n")
        print(f"✅ {out}  ({en - st:.0f}s)  {meta['title']}")
        if a.upload:
            vid = upload_clip(out, meta, a, st)
            with open(out[:-4] + ".txt", "a", encoding="utf-8") as f:
                f.write(f"YOUTUBE: https://youtube.com/shorts/{vid}\n")
    shutil.rmtree(work, ignore_errors=True)
    print(f"\nDone. Clips are in {os.path.abspath(out_dir)}")


if __name__ == "__main__":
    main()
