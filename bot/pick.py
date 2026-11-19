"""Choose the best moment to clip + write title/hook. Gemini if available, else heuristics."""
import json
import os
import random
import time

import numpy as np
import requests

# Same model list as the Sanatan bot (Gemini 2.5 was shut down Oct 2026).
GEMINI_MODELS = ([os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL") else
                 ["gemini-3.1-flash-lite", "gemini-3.8-flash",
                  "gemini-3-flash-preview", "gemini-4-hydrogen"])

HOOKS = {
    "gaming": ["WAIT FOR IT 💀", "GTA 6 IS INSANE", "NOBODY EXPECTED THIS", "THIS GAME IS WILD"],
    "comedy": ["HE REALLY SAID THAT 😭", "WAIT FOR THE ENDING", "CROWD LOST IT 😂", "NO WAY HE SAID THIS"],
    "prank": ["HIS REACTION 😂", "WAIT FOR IT...", "THIS PRANK WENT WRONG", "SHE DIDN'T SEE IT COMING"],
    "default": ["WAIT FOR IT 😂", "THIS IS TOO FUNNY", "WATCH TILL THE END"],
}


def _z(x):
    return (x - x.mean()) / (x.std() + 1e-6)


def _snap(t, bounds, after=True):
    if not bounds:
        return t
    return min(bounds, key=lambda b: abs(b - t) + (0 if (b >= t) == after else 0.5))


def heuristic(loud, segments, duration, clip):
    lo, hi, target = clip["min_seconds"], clip["max_seconds"], clip["target_seconds"]
    z = _z(loud)
    starts = [s["start"] for s in segments] or list(np.arange(0, max(1, duration - lo), 2.0))
    ends = [s["end"] for s in segments]
    best, best_score = (0, min(target, duration)), -1e9
    for st in starts:
        if st < 3 and duration > 120:  # skip intros of long videos
            continue
        en = _snap(st + target, [e for e in ends if lo <= e - st <= hi]) if ends else st + target
        if not (lo <= en - st <= hi) or en > duration:
            continue
        w = z[int(st): max(int(st) + 1, int(en))]
        top = np.sort(w)[-max(1, len(w) // 4):]
        late_peak = w[int(len(w) * 0.5):].max() if len(w) > 2 else 0  # payoff near the end
        score = top.mean() + 0.6 * late_peak
        if score > best_score:
            best, best_score = (float(st), float(en)), score
    return best


def gemini(segments, loud, cand, clip):
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not segments:
        return None
    peaks = np.argsort(loud)[-12:]
    lines = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)[:60000]
    prompt = f"""You are an expert YouTube Shorts clip editor for an Indian audience (Hindi/Hinglish/English).
Source video: "{cand['title']}" by {cand['channel']} (category: {cand['category']}).
Loudest seconds (likely laughs/reactions): {sorted(int(p) for p in peaks)}
Transcript with timestamps (seconds):
{lines}

Pick the ONE funniest / most viral self-contained moment, {clip['min_seconds']}-{clip['max_seconds']} seconds long.
It must start with context the viewer understands and END right after the punchline/payoff.
Return JSON only:
{{"start": float, "end": float,
 "title": "catchy Shorts title, max 70 chars, Hinglish ok, 1 emoji, no clickbait lies",
 "hook": "on-screen hook text, max 7 words, ALL CAPS",
 "context": "one-sentence original take/context for the description (your own words)",
 "tags": ["5-8 lowercase tags"]}}"""
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key},
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"responseMimeType": "application/json",
                                               "temperature": 0.7}},
                    timeout=(15, 120),
                )
            except requests.exceptions.RequestException as e:
                print(f"[gemini] {model}: network problem ({type(e).__name__}), retrying...")
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 503):
                print(f"[gemini] {model}: busy ({r.status_code}), retrying...")
                time.sleep(20 * (attempt + 1))
                continue
            break
        else:
            continue
        if not r.ok:
            print(f"[gemini] {model}: HTTP {r.status_code}, trying next model...")
            continue
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            out = json.loads(text)
            out = out[0] if isinstance(out, list) else out
            print(f"[gemini] using {model}")
            return out
        except Exception as e:
            print(f"[gemini] {model}: bad answer ({e}), trying next model...")
    print("[gemini] every model failed, using heuristics")
    return None


def choose(loud, segments, duration, cand, clip):
    ai = gemini(segments, loud, cand, clip)
    if ai:
        st, en = float(ai["start"]), float(ai["end"])
        if en - st > clip["max_seconds"]:
            en = st + clip["max_seconds"]
        if en - st < clip["min_seconds"]:
            en = min(duration, st + clip["min_seconds"])
        if 0 <= st < en <= duration + 0.5:
            print(f"[pick] gemini {st:.1f}-{en:.1f}: {ai.get('title')}")
            ai["start"], ai["end"] = st, min(en, duration)
            return ai
    st, en = heuristic(loud, segments, duration, clip)
    hook = random.choice(HOOKS.get(cand["category"], HOOKS["default"]))
    base = cand["title"].split("|")[0].split("#")[0].strip()[:55]
    print(f"[pick] heuristic {st:.1f}-{en:.1f}")
    return {"start": st, "end": en, "title": f"{base} 😂", "hook": hook, "context": "", "tags": []}
