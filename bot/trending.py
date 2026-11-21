"""Find what's trending on YouTube India right now and turn it into search queries."""
import json
import os
import re
from collections import Counter

from . import yt

STOP = set("""a an the and or of to in on for with is are was be this that it its at by from as you your
my me i we our new latest video full official shorts short live part ep episode vs ft feat
hai ka ki ke ko se me mein aur bhi ye yeh wo kya kar karo nahi ho gaya gya tha the hum tum
2024 2025 2026 2027 hd 4k vlog day""".split())


def trending_titles(region="IN", categories=(20, 23, 24), per_cat=25):
    """YouTube 'Trending' chart per category. Costs 1 quota unit per category."""
    out = []
    for cat in categories:
        try:
            res = yt.client().videos().list(
                part="snippet", chart="mostPopular", regionCode=region,
                videoCategoryId=str(cat), maxResults=per_cat,
            ).execute()
        except Exception as e:  # some categories have no chart in some regions
            print(f"[trending] category {cat}: {str(e)[:120]}")
            continue
        for it in res.get("items", []):
            out.append({"title": it["snippet"]["title"], "tags": it["snippet"].get("tags", [])[:8],
                        "category": cat})
    print(f"[trending] {len(out)} trending videos in {region}")
    return out


def _gemini_queries(titles, n, focus=None):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    from .pick import GEMINI_MODELS
    import requests
    listing = "\n".join(f"- [{t['category']}] {t['title']}" for t in titles[:75])
    prompt = f"""These videos are trending on YouTube India right now (20=gaming, 23=comedy, 24=entertainment):
{listing}

Give the {n} hottest TOPICS as short YouTube search queries (2-4 words, English, no channel names,
no person names) that would find funny or exciting clip-worthy videos about the same topic,
e.g. "GTA 6 funny moments". Prefer gaming, comedy, pranks, viral challenges.{(" EVERY query MUST be about " + focus + " and contain the words '" + focus + "'.") if focus else ""}
Return JSON only: {{"queries": [{{"query": "...", "category": "gaming|comedy|prank|default"}}]}}"""
    for model in GEMINI_MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json"}},
                timeout=(15, 90),
            )
            if not r.ok:
                continue
            parts = r.json()["candidates"][0]["content"]["parts"]
            data = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
            qs = [q for q in data.get("queries", []) if q.get("query")]
            if qs:
                return qs[:n]
        except Exception as e:
            print(f"[trending] {model}: {e}")
    return None


def _keyword_queries(titles, n):
    """Fallback without Gemini: most common 2-word phrases in trending titles."""
    grams = Counter()
    for t in titles:
        words = [w for w in re.findall(r"[a-z0-9]+", t["title"].lower()) if w not in STOP and (len(w) > 1 or w.isdigit())]
        grams.update(" ".join(p) for p in zip(words, words[1:]))
    cat_name = {20: "gaming", 23: "comedy", 24: "default"}
    out = []
    for phrase, _ in grams.most_common(n * 3):
        cat = next((cat_name.get(t["category"], "default") for t in titles if phrase in t["title"].lower()), "default")
        out.append({"query": phrase, "category": cat})
        if len(out) >= n:
            break
    return out


def trending_queries(cfg_trend: dict, region="IN"):
    titles = trending_titles(region, cfg_trend.get("categories", [20, 23, 24]))
    if not titles:
        return []
    n = cfg_trend.get("queries_per_run", 2)
    focus = cfg_trend.get("focus")
    qs = _gemini_queries(titles, n, focus) or _keyword_queries(titles, n)
    if focus:  # make sure every query is on-topic
        qs = [dict(q, query=q["query"] if focus.lower() in q["query"].lower() else f"{focus} {q['query']}",
                   category="gaming") for q in qs]
    for q in qs:
        if q.get("category") == "gaming":
            q.setdefault("layout", "center_crop")
    print("[trending] queries:", [q["query"] for q in qs])
    return qs
