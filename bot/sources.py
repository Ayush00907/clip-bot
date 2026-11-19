"""Collect candidate source videos — only ones with a valid permission basis."""
import datetime as dt
import re

from . import yt

VALID_PERMISSIONS = {"own", "creative_commons", "clipping_campaign", "written_permission"}

# Common words that give away Spanish/Portuguese/French/German/Indonesian titles
FOREIGN_WORDS = {"de", "del", "y", "con", "el", "los", "las", "que", "para", "por", "una", "em", "com",
                 "não", "uma", "und", "der", "die", "das", "mit", "le", "les", "des", "avec", "et",
                 "pour", "dan", "yang", "di", "ini", "jogo", "juego", "partida", "lo", "sin"}


EN_WORDS = {"the", "is", "it", "to", "on", "in", "of", "and", "a", "my", "i", "you", "this", "that", "with",
            "how", "what", "when", "best", "funny", "moments", "try", "not", "laugh", "first", "time",
            "gets", "gets", "vs", "bad", "really", "pull", "off", "tries", "goes", "wrong", "can", "be"}


def language_ok(title, lang, allowed):
    """True if the video looks Hindi/English (or whatever `allowed` lists)."""
    words = re.findall(r"[a-z']+", title.lower())
    english = sum(w in EN_WORDS for w in words) >= 2
    if lang and lang.split("-")[0].lower() in allowed:
        return True
    if lang and not english:  # uploader says another language and the title isn't clearly English
        return False
    for ch in title:
        if ch.isalpha() and not (ord(ch) < 0x250 or 0x900 <= ord(ch) <= 0x97F):
            return False  # Cyrillic, Arabic, Thai, Chinese, Korean, Japanese...
    words = re.findall(r"[a-záéíóúãõçñü]+", title.lower())
    return sum(w in FOREIGN_WORDS for w in words) < 2


def _video_id(url: str):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def _iso_seconds(d):
    import isodate
    try:
        return int(isodate.parse_duration(d).total_seconds())
    except Exception:
        return 0


def _details(ids):
    out = {}
    for i in range(0, len(ids), 50):
        res = yt.client().videos().list(
            part="snippet,contentDetails,status,statistics", id=",".join(ids[i:i + 50])
        ).execute()
        for it in res.get("items", []):
            out[it["id"]] = it
    return out


def _cand(item, src, permission):
    sn = item["snippet"]
    return {
        "id": item["id"],
        "url": f"https://www.youtube.com/watch?v={item['id']}",
        "title": sn["title"],
        "channel": sn["channelTitle"],
        "channel_id": sn["channelId"],
        "duration": _iso_seconds(item["contentDetails"]["duration"]),
        "views": int(item.get("statistics", {}).get("viewCount", 0)),
        "license": item["status"].get("license"),
        "lang": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "",
        "permission": permission,
        "category": src.get("category", "default"),
        "layout": src.get("layout", "blur_fit"),
        "credit": src.get("credit") or sn["channelTitle"],
        "published": sn.get("publishedAt", ""),
        "campaign": src.get("campaign"),
        "title_must_contain": [w.lower() for w in src.get("title_must_contain", [])],
        "since": src.get("since", ""),
        "max_source_seconds": src.get("max_source_seconds"),
    }


def gather(sources: dict, cfg: dict, posted: set):
    disc = cfg["discovery"]
    after = (dt.datetime.utcnow() - dt.timedelta(days=disc["max_age_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    pending = []  # (video_id, src, permission)

    for src in sources.get("manual") or []:
        perm = src.get("permission")
        vid = _video_id(src.get("url"))
        if perm not in VALID_PERMISSIONS or not vid:
            print(f"[skip] manual source without valid permission/url: {src}")
            continue
        pending.append((vid, src, perm))

    for src in sources.get("allowed_channels") or []:
        perm = src.get("permission")
        if perm not in VALID_PERMISSIONS:
            print(f"[skip] channel without valid permission: {src.get('channel_id')}")
            continue
        ch = yt.client().channels().list(part="contentDetails", id=src["channel_id"]).execute()
        if not ch.get("items"):
            continue
        uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        pl = yt.client().playlistItems().list(part="contentDetails", playlistId=uploads,
                                              maxResults=src.get("recent", 25)).execute()
        for it in pl.get("items", []):
            pending.append((it["contentDetails"]["videoId"], src, perm))

    # Searches cost 100 quota units each, so each run does a few: trending topics + 1-2 fixed
    # queries taken in turn (a different one each run).
    fixed = sources.get("creative_commons_search") or []
    trend_cfg = sources.get("trending") or {}
    searches = []
    if trend_cfg.get("enabled", True):
        from .trending import trending_queries
        try:
            searches += trending_queries(trend_cfg, disc.get("region", "IN"))
        except Exception as e:
            print(f"[trending] failed: {e}")
    if fixed:
        k = max(1, disc.get("searches_per_run", 3) - len(searches))
        start = (int(dt.datetime.utcnow().timestamp()) // 3600) % len(fixed)
        searches += [fixed[(start + i) % len(fixed)] for i in range(min(k, len(fixed)))]

    for src in searches:
        print(f"[search] {src['query']}")
        res = yt.client().search().list(
            part="id", q=src["query"], type="video", videoLicense="creativeCommon",
            order="viewCount", publishedAfter=after, regionCode=disc.get("region", "IN"),
            relevanceLanguage=src.get("language", disc.get("language", "en")),
            maxResults=disc.get("results_per_query", 15), safeSearch="moderate",
        ).execute()
        for it in res.get("items", []):
            pending.append((it["id"]["videoId"], src, "creative_commons"))

    ids = list(dict.fromkeys(v for v, _, _ in pending if v not in posted))
    info = _details(ids) if ids else {}
    seen, cands = set(), []
    for vid, src, perm in pending:
        if vid in seen or vid not in info:
            continue
        seen.add(vid)
        c = _cand(info[vid], src, perm)
        # Re-verify CC license from the video itself — never trust search alone.
        if perm == "creative_commons" and c["license"] != "creativeCommon":
            continue
        if c["permission"] == "creative_commons" and not language_ok(
                c["title"], c["lang"], set(cfg.get("languages", ["hi", "en"]))):
            print(f"[skip] not Hindi/English: {c['title'][:70]}")
            continue
        if c["title_must_contain"] and not any(w in c["title"].lower() for w in c["title_must_contain"]):
            continue
        if c["since"] and c["published"][:10] < str(c["since"]):
            continue
        if not (disc["min_source_seconds"] <= c["duration"] <= (c["max_source_seconds"] or disc["max_source_seconds"])):
            continue
        if perm == "creative_commons" and c["views"] < disc["min_views"]:
            continue
        cands.append(c)

    # Your own / permitted content first, then most-viewed CC videos.
    rank = {"own": 0, "written_permission": 1, "clipping_campaign": 1, "creative_commons": 2}
    cands.sort(key=lambda c: (rank[c["permission"]], -c["views"]))
    print(f"[sources] {len(cands)} usable candidates")
    return cands
