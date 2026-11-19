"""Download, probe, audio energy, transcription."""
import json
import os
import subprocess

import numpy as np


class BlockedError(Exception):
    pass


def run(cmd):
    print("$", " ".join(str(c) for c in cmd)[:300])
    subprocess.run(cmd, check=True)


def _cookie_args():
    cookies = os.environ.get("YT_COOKIES_FILE")
    if cookies and os.path.exists(cookies) and os.path.getsize(cookies) > 0:
        return ["--cookies", cookies]
    print("[download] no YT_COOKIES secret - YouTube often blocks GitHub servers without it")
    return []


def hottest_window(url: str, duration: int, window: int = 600, skip: set = ()):
    """For long streams: use YouTube's 'Most replayed' graph to find the best `window` seconds.
    Returns (start, end). Falls back to a random spot in the middle of the stream."""
    import random
    heat = []
    try:
        r = subprocess.run(["yt-dlp", *_cookie_args(), "-J", "--no-playlist", url],
                           capture_output=True, text=True, timeout=180)
        heat = (json.loads(r.stdout).get("heatmap") or []) if r.returncode == 0 else []
    except Exception as e:
        print("[heatmap] failed:", e)
    if heat:
        best, best_score = None, -1
        for h in heat:
            st = max(0, int(h["start_time"] - window * 0.4))
            if any(abs(st - x) < window for x in skip):
                continue  # already clipped near here
            score = sum(x["value"] for x in heat if st <= x["start_time"] < st + window)
            if score > best_score:
                best, best_score = st, score
        if best is not None:
            print(f"[heatmap] most replayed window starts at {best // 60} min")
            return best, min(duration, best + window)
    st = random.randint(int(duration * 0.15), max(int(duration * 0.15) + 1, int(duration * 0.85) - window))
    print(f"[heatmap] none available, using random window at {st // 60} min")
    return st, min(duration, st + window)


def download(url: str, out_dir: str, section=None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "source.%(ext)s")
    cmd = [
        "yt-dlp", *_cookie_args(), "--sleep-requests", "1", "--retries", "3",
        "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "--merge-output-format", "mp4", "--no-playlist", "-o", out, url,
    ]
    if section:  # only download part of a long stream
        cmd[1:1] = ["--download-sections", f"*{section[0]}-{section[1]}", "--force-keyframes-at-cuts"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout[-2000:], r.stderr[-2000:])
    if r.returncode != 0:
        if any(x in r.stderr for x in ("429", "403", "Sign in to confirm", "not a bot")):
            raise BlockedError("YouTube blocked the download from GitHub's server. Add/refresh the "
                               "YT_COOKIES secret (see README).")
        raise RuntimeError("yt-dlp failed")
    return os.path.join(out_dir, "source.mp4")


def probe(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    j = json.loads(r.stdout)
    s = j["streams"][0]
    return {"w": int(s["width"]), "h": int(s["height"]), "duration": float(j["format"]["duration"])}


def loudness_per_second(path: str) -> np.ndarray:
    """RMS loudness (dB) for every second — laughter, screams and hype moments spike here."""
    sr = 8000
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(a) // sr
    if n == 0:
        return np.zeros(1)
    a = a[: n * sr].reshape(n, sr)
    rms = np.sqrt((a ** 2).mean(axis=1) + 1e-9)
    return 20 * np.log10(rms)


def transcribe(path: str, model_size: str = "base"):
    """Returns (segments, words). Each word: {start, end, word}."""
    from faster_whisper import WhisperModel

    # Decode with ffmpeg ourselves (faster-whisper's own decoder breaks with some PyAV versions)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segs, info = model.transcribe(audio, word_timestamps=True, vad_filter=True)
    segments, words = [], []
    for s in segs:
        segments.append({"start": s.start, "end": s.end, "text": s.text.strip()})
        for w in s.words or []:
            words.append({"start": w.start, "end": w.end, "word": w.word.strip()})
    print(f"[whisper] lang={info.language} segments={len(segments)} words={len(words)}")
    return segments, words, info.language
