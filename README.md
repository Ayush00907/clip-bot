# ClipBot — automated clipping channel (GTA 6 · pranks · comedy)

> **Current mode: auto-upload.** `upload_to_youtube: true` in `config.yaml`. Each scheduled run makes
> one Short and saves it (with a `.txt` holding the title, description and campaign link) to
> **Releases → "Shorts DD Mon YYYY"**. Download them and upload them yourself.

Runs on GitHub Actions 3× a day. For each run it does this:

1. **Finds a source video** you're allowed to clip (your own uploads, channels that allow clipping, or Creative-Commons videos on YouTube).
2. **Downloads** it (yt-dlp) and **transcribes** it (faster-whisper, works for Hindi, Hinglish and English).
3. **Picks the best moment**. Gemini reads the transcript and the loudest spots (laughs, screams, hype) and picks a 20–50 s clip that ends on the punchline. It also writes the title, the hook and a one-line take. Without Gemini it falls back to loudness-based picking.
4. **Edits a 1080×1920 Short**: blurred background (or full-screen crop for gameplay), yellow hook banner, word-by-word captions with the current word in yellow, slow zoom, loudness-normalised audio, credit line.
5. **Uploads** it to your new channel with credit and the license in the description, then logs it in `state/posted.json` so the same source isn't posted twice.

---

## ⚠️ Read this first: why there's no "auto-rip Latent / random prank channels" mode

- **Copyright strikes:** 3 strikes and the channel is deleted. India's Got Latent S2 is co-released with Netflix, so Content ID will claim clips of it.
- **YouTube's 1 Oct 2026 Shorts update:** channels that mostly re-upload other people's clips get much less reach. The reused-content policy can also block monetisation for the **whole channel**.

So the bot only clips from sources in `sources.yaml` that have a `permission`. Clipping channels that grow use one of these legally:

| Source | How to get it | Bot setting |
|---|---|---|
| **Your own GTA 6 gameplay** (best: zero copyright risk, huge demand right now) | Play on PS5/Xbox → record → upload the full session to your channel (unlisted is fine) | `manual:` with `permission: own` |
| **Clipping campaigns**: creators pay clippers per view | Whop "Content Rewards", ClipFarm, Vyro and similar. Many Indian streamers and podcasters run these | `allowed_channels:` with `permission: clipping_campaign` |
| **Creator permission** | DM or email small and mid prank/comedy creators. Many say yes for free promotion | `permission: written_permission` |
| **Creative Commons (CC-BY) videos** | Automatic. The bot searches YouTube with a CC filter and re-checks every video's license | `creative_commons_search:` |

If Samay Raina / Latent ever runs an official clipping program, add the channel under `allowed_channels`.

---

## Setup (≈30 min, once) — same steps as your Sanatan bot

### 1. Create the new channel
youtube.com → profile → **Create a channel** (a brand account under the same Gmail is fine).
Verify it with a phone number at **youtube.com/verify** (higher upload limit).

### 2. A NEW Google Cloud project (don't reuse the Sanatan one)
YouTube API quota is **per project**, and the Sanatan bot already uses ~9,750 of its 10,000
units a day. Uploads from this bot would fail with `quotaExceeded`.
- console.cloud.google.com → New project (e.g. `clip-bot`) → enable **YouTube Data API v3**.
- OAuth consent screen → **Publish app → In production** (otherwise the login expires every 7 days).
- Credentials → OAuth client ID → **Desktop app** → download as `client_secret.json`.

### 3. Log in as the NEW channel (on your Mac)
```bash
pip install -r requirements.txt
python3 -m bot.main --auth
```
When Google asks, pick the **new clipping channel, not Sanatan Stories**. This creates `token.json`.

### 4. GitHub
Create a new repo (e.g. `clip-bot`), push this folder.
**Settings → Secrets and variables → Actions:**

| Secret | Value |
|---|---|
| `YT_CLIENT_SECRET` | full text of the new `client_secret.json` (`pbcopy < client_secret.json`) |
| `YT_TOKEN` | full text of the new `token.json` |
| `GEMINI_API_KEY` | same Gemini key as the Sanatan bot is fine |
| `YT_COOKIES` | *(recommended)* cookies.txt text. YouTube often blocks downloads from GitHub servers ("Sign in to confirm you're not a bot"). Export with the "Get cookies.txt LOCALLY" extension while logged into a **spare** Google account. |

Optional variable: `GEMINI_MODEL` to force one model.

**Actions minutes:** a clip run takes ~8–15 min (download + transcription). Private repos share
your account's 2,000 free min/month with the Sanatan bot, so make this repo **public**
(secrets stay hidden) like your README recommends.

### 5. Test, then schedule
Actions → **Clip Bot** → Run workflow → tick **dry_run** → download the `clip-N` artifact and watch it.
Then add a cron-job.org job (same as your Sanatan jobs) that calls the workflow_dispatch API for
`clip.yml` at e.g. 12:30, 18:00, 21:00 IST:
```
POST https://api.github.com/repos/Ayush00907/clip-bot/actions/workflows/clip.yml/dispatches
Headers: Authorization: Bearer <your GitHub token>, Accept: application/vnd.github+json
Body:    {"ref":"main"}
```
For the first real uploads, set `privacy: private` in `config.yaml`, check them in Studio, then switch to `public`.

## Tuning

- `config.yaml` sets the channel name, clip length, uploads per run, how recent and how popular sources must be, and the Whisper model (`small` = better Hindi, slower).
- `sources.yaml` holds your queries, channels and videos. Add `layout: center_crop` for gameplay.
- Schedule: change the times in your cron-job.org job.
- API quota (new project, 10,000/day): upload ≈1,600, each CC search 100. Default 3 runs × (1,600 + 5 searches) ≈ 6,300. Remove searches if you add more uploads.

## Grow faster (what automation can't do for you)
- **Add your voice.** A 3–5 s reaction or commentary recorded on your phone is what YouTube now counts as "original". Even one in three Shorts with your voice helps.
- **GTA 6 launch week:** post your own gameplay moments daily: funny NPCs, police chases, glitches, "things you missed".
- Keep one niche per channel if you can. A GTA-only clip channel usually grows faster than a mixed one.
