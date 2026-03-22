"""
Google Drive downloader — lists and downloads PDF files from a Drive folder.

credentials.json and token.json live alongside this file in ~/code/google-client/.
GOOGLE_CREDENTIALS_FILE is loaded from the .env in that same directory.

Auth: OAuth2 Desktop flow. First run opens a browser for consent;
subsequent runs use the shared token.json (auto-refreshed).
"""

import io
import os
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Load credentials path from google-client's own .env
load_dotenv(Path(__file__).parent / ".env")

# Combined scopes shared with google_sheets_client so both modules use one token.json
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

# Default paths — both Google modules share one token file in the google-client directory
_DEFAULT_CREDENTIALS_FILE = Path(
    os.environ.get("GOOGLE_CREDENTIALS_FILE", str(Path(__file__).parent / "credentials.json"))
).expanduser()
_DEFAULT_TOKEN_FILE = Path(__file__).parent / "token.json"


class GoogleDriveDownloader:
    def __init__(
        self,
        credentials_file: Path | None = None,
        token_file: Path | None = None,
    ):
        creds_file = Path(credentials_file).expanduser() if credentials_file else _DEFAULT_CREDENTIALS_FILE
        tok_file = Path(token_file) if token_file else _DEFAULT_TOKEN_FILE
        self._service = self._authenticate(creds_file, tok_file)

    def _authenticate(self, credentials_file: Path, token_file: Path):
        creds = None
        if token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not credentials_file.exists():
                    raise FileNotFoundError(
                        f"Google credentials not found at {credentials_file}\n"
                        "Download OAuth2 Desktop credentials from Google Cloud Console\n"
                        "and place them at that path."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
                creds = flow.run_local_server(port=0)
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json())
        return build("drive", "v3", credentials=creds)

    def _list_items(self, query: str) -> list[dict]:
        """List all Drive items matching a query, handling pagination."""
        results = []
        page_token = None
        while True:
            resp = (
                self._service.files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                    pageToken=page_token,
                    pageSize=100,
                )
                .execute()
            )
            results.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return results

    def list_files_recursive(self, folder_id: str) -> list[dict]:
        """
        Recursively list all PDF files within a folder tree.

        Traverses subfolders at any depth (year/month structure etc.).
        Returns a flat list of file dicts: {id, name, mimeType, modifiedTime, size}.
        """
        pdfs = self._list_items(
            f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
        )
        subfolders = self._list_items(
            f"'{folder_id}' in parents "
            f"and mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        for subfolder in subfolders:
            pdfs.extend(self.list_files_recursive(subfolder["id"]))
        return pdfs

    def download_file(self, file_id: str) -> bytes:
        """Download a file by Drive file ID and return its raw bytes."""
        request = self._service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()
