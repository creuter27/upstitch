"""
OAuth credentials for Gmail + Drive.
Uses credentials.json from ~/code/google-client/ but maintains a separate
.token.json in this project (different scopes from the shared google-client token).
"""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive",
]

_GOOGLE_CLIENT_DIR = Path(__file__).parent.parent.parent / "google-client"
CREDENTIALS_FILE = _GOOGLE_CLIENT_DIR / "credentials.json"
TOKEN_FILE = Path(__file__).parent.parent / ".token.json"


def get_credentials() -> Credentials:
    """Load (or refresh/create) OAuth credentials with Gmail+Drive scopes."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"credentials.json not found at {CREDENTIALS_FILE}. "
                    "Copy it from your Google Cloud project or from ~/code/google-client/."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return creds
