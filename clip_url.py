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
    a = ap.parse_args()

    os.environ["TITLE_STYLE"] = a.style
    work = os.path.abspath(".clip_work")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)

    if os.path.exists(a.source):
        src, name = a.source, os.path.splitext(os.path.basename(a.source))[0]
    else:
        m = re.search(r"(?:v=|youtu\.be/|shorts/|videos/)([\w-]+)", a.source)
        name = m.group(1) if m else "video"
        section = None
        if not a.full:
            try:
                import json
                import subprocess
                r = subprocess.run(["yt-dlp", *media._cookie_args(), "--no-playlist", "--skip-download",
                                    "--print", "duration", a.source], capture_output=True, text=True)
                dur = int(float((r.stdout.strip().splitlines() or ["0"])[-1] or 0)) if r.returncode == 0 else 0
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
        cand = {"title": name, "channel": a.credit or name, "category": "gaming"}
        meta = pick.choose(l2, segs, info["duration"], cand, clip)
        st, en = meta["start"], meta["end"]
        for u0, u1 in used:  # trim small overlaps with clips already made
            if st < u0 < en:
                en = u0
            if st < u1 < en:
                st = u1
        if en - st < a.min or not free(st, en):
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
    shutil.rmtree(work, ignore_errors=True)
    print(f"\nDone. Clips are in {os.path.abspath(out_dir)}")


if __name__ == "__main__":
    main()
