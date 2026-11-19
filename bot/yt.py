"""YouTube login — same token.json / client_secret.json setup as the Sanatan shorts-bot."""
import os
from pathlib import Path

# force-ssl also allows reading/searching (needed to find Creative Commons videos)
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]

_client = None


def client():
    global _client
    if _client is not None:
        return _client
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if Path("token.json").exists():
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise SystemExit(
                    f"YouTube login expired ({exc}). On your computer run "
                    "`python -m bot.main --auth`, then update the YT_TOKEN secret. "
                    "(Set the OAuth consent screen to 'In production' so it stops expiring.)")
        elif os.getenv("CI"):
            raise SystemExit("No valid token.json on the server. Create it with "
                             "`python -m bot.main --auth` and save it as the YT_TOKEN secret.")
        else:
            flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        Path("token.json").write_text(creds.to_json())
    _client = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return _client
