"""
OAuth credentials for Gmail (read + send).
Uses credentials.json from ~/code/google-client/ but stores a separate token
per account under sessions/ (different from gmailAttachmentExtractor's token).
"""

import re
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

_GOOGLE_CLIENT_DIR = Path(__file__).parent.parent.parent / "google-client"
CREDENTIALS_FILE = _GOOGLE_CLIENT_DIR / "credentials.json"
_SESSIONS_DIR = Path(__file__).parent.parent / "sessions"


def _token_file(account: str) -> Path:
    safe = re.sub(r"[^\w@._-]", "_", account)
    return _SESSIONS_DIR / f".token_{safe}.json"


def get_credentials(account: str) -> Credentials:
    """
    Load (or refresh/create) OAuth credentials for the given Gmail account.
    On first run for a new account an OAuth browser flow is opened.
    """
    _SESSIONS_DIR.mkdir(exist_ok=True)
    token_file = _token_file(account)

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"credentials.json not found at {CREDENTIALS_FILE}.\n"
                    "Copy it from your Google Cloud project or from the google-client/ folder."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())

    return creds
