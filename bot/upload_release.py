"""Upload Shorts that were already made (saved in a GitHub Release) to YouTube.

Usage (in GitHub Actions):  python -m bot.upload_release <folder with .mp4 + .txt files>
Uses the TITLE / DESCRIPTION / TAGS / CAMPAIGN written in each .txt upload sheet.
"""
import glob
import json
import os
import sys
import time

import yaml

from . import yt
from .main import ROOT, add_to_submit_list, load_state, save_state


def parse_sheet(path):
    text = open(path, encoding="utf-8").read()
    def section(name, nxt):
        a = text.split(name, 1)[1]
        for n in nxt:
            if n in a:
                a = a.split(n, 1)[0]
        return a.strip()
    title = section("TITLE (copy into YouTube):", ["DESCRIPTION (copy into YouTube):"])
    desc = section("DESCRIPTION (copy into YouTube):", ["\nTAGS:"])
    tags = [t.strip() for t in section("TAGS:", ["\nSOURCE:"]).split(",") if t.strip()]
    campaign = proof = None
    if "CAMPAIGN:" in text:
        campaign = text.split("CAMPAIGN:", 1)[1].splitlines()[0].strip()
        if "submit the Short's link here:" in text:
            proof = text.split("submit the Short's link here:", 1)[1].splitlines()[0].strip()
    source = text.split("SOURCE:", 1)[1].split()[0] if "SOURCE:" in text else ""
    return title, desc, tags, campaign, proof, source


def main(folder):
    from googleapiclient.http import MediaFileUpload
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml")))
    privacy = cfg.get("privacy", "public")
    state = load_state()
    done = {p.get("file") for p in state["posted"]}
    for mp4 in sorted(glob.glob(os.path.join(folder, "*.mp4"))):
        name = os.path.basename(mp4)
        if name in done:
            print(f"[skip] {name} already uploaded")
            continue
        title, desc, tags, campaign, proof, source = parse_sheet(mp4[:-4] + ".txt")
        body = {"snippet": {"title": title[:100], "description": desc[:4900], "tags": tags[:15],
                            "categoryId": "20"},
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                           "containsSyntheticMedia": False}}
        req = yt.client().videos().insert(part="snippet,status", body=body, notifySubscribers=False,
                                          media_body=MediaFileUpload(mp4, chunksize=8 * 1024 * 1024,
                                                                     resumable=True, mimetype="video/mp4"))
        resp = None
        while resp is None:
            _, resp = req.next_chunk(num_retries=5)
        vid = resp["id"]
        print(f"[upload] {name} -> https://youtube.com/shorts/{vid}  ({title})")
        state["posted"].append({"source_id": name.split("_")[0], "short_id": vid, "title": title,
                                "campaign": campaign, "file": name, "permission": "clipping_campaign" if campaign else "",
                                "section_start": -1, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        save_state(state)
        if campaign:
            add_to_submit_list(vid, {"campaign": {"name": campaign}, "proof": proof or ""}, {"title": title})


if __name__ == "__main__":
    main(sys.argv[1])
