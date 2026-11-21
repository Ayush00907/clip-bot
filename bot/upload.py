"""Upload the finished Short to YouTube."""
from . import yt

PERMISSION_LINE = {
    "own": "Original footage.",
    "creative_commons": "Used under Creative Commons Attribution (CC BY).",
    "clipping_campaign": "Clipped with permission as part of the creator's clipping program.",
    "written_permission": "Clipped with the creator's permission.",
}


def build_metadata(cand, meta, cat_cfg):
    """Title, description and tags for a Short (used for upload and for the .txt file)."""
    camp = cand.get("campaign") or {}
    title = meta["title"].strip()
    for h in camp.get("hashtags", []):
        if h.lower() not in title.lower() and len(title) + len(h) < 90:
            title += " " + h
    if "#shorts" not in title.lower() and len(title) <= 90:
        title += " #shorts"
    tags = list(dict.fromkeys((meta.get("tags") or []) + cat_cfg["tags"]))[:15]
    desc = "\n".join(filter(None, [
        camp.get("caption_line", ""),
        meta.get("context", ""),
        "",
        f"🎥 Credit: {cand['credit']} — full video: {cand['url']}",
        PERMISSION_LINE[cand["permission"]],
        "",
        "Follow for daily GTA 6 moments 🔥🌴",
        " ".join(camp.get("hashtags", []) + ["#" + t.replace(" ", "") for t in tags[:5]]),
    ]))
    return title[:100], desc[:4900], tags


def write_upload_sheet(path_txt, cand, meta, cat_cfg):
    """Everything needed to upload this Short by hand, ready to copy-paste."""
    title, desc, tags = build_metadata(cand, meta, cat_cfg)
    camp = cand.get("campaign") or {}
    lines = [
        "TITLE (copy into YouTube):", title, "",
        "DESCRIPTION (copy into YouTube):", desc, "",
        "TAGS:", ", ".join(tags), "",
        f"SOURCE: {cand['url']}  (moment {int(meta['start'])}s-{int(meta['end'])}s"
        + (f" of the downloaded part starting at {meta['section_start']}s" if meta.get("section_start") else "") + ")",
    ]
    if camp:
        lines += ["", f"CAMPAIGN: {camp.get('name')}",
                  f"After uploading, submit the Short's link here: {cand.get('proof', '')}",
                  "Rules: " + camp.get("rules", "see campaign page")]
    with open(path_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def upload(path, cand, meta, cat_cfg, privacy):
    title, desc, tags = build_metadata(cand, meta, cat_cfg)
    body = {
        "snippet": {"title": title, "description": desc, "tags": tags,
                    "categoryId": str(cat_cfg["youtube_category"])},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": False},
    }
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    req = yt.client().videos().insert(
        part="snippet,status", body=body, notifySubscribers=False,
        media_body=MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True,
                                   mimetype="video/mp4"),
    )
    resp = None
    try:
        while resp is None:
            _, resp = req.next_chunk(num_retries=5)
    except HttpError as exc:
        if "uploadLimitExceeded" in str(exc):
            raise SystemExit("Daily upload limit reached. Verify the channel at youtube.com/verify.")
        if "quotaExceeded" in str(exc):
            raise SystemExit("YouTube API quota used up. Use a SEPARATE Google Cloud project from "
                             "the Sanatan bot; quota resets at midnight Pacific time.")
        raise
    vid = resp["id"]
    print(f"[upload] https://youtube.com/shorts/{vid}")
    try:
        actual = yt.client().videos().list(part="status", id=vid).execute()["items"][0]["status"]["privacyStatus"]
        if actual != privacy:
            print(f"WARNING: asked for '{privacy}' but YouTube set '{actual}'. Your Google Cloud "
                  "project needs the YouTube API audit, or change visibility in YouTube Studio.")
    except Exception as exc:
        print("Could not double-check visibility:", exc)
    return vid
