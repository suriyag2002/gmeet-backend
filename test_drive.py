
import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Google Drive read-only permission
SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/drive.meet.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

VIDEO_ID = "106erqKvsZIKkSOni8SrgrtsofXcUONKl"

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE = BASE_DIR / "token.json"


def get_credentials():
    """Load valid credentials or request Google authorization."""

    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"Missing credentials file: {CREDENTIALS_FILE}\n"
            "Download your OAuth client JSON from Google Cloud Console "
            "and save it as credentials.json."
        )

    creds = None

    # Load previously saved credentials if available.
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(TOKEN_FILE),
                SCOPES,
            )
        except (ValueError, OSError) as exc:
            print("Could not load saved token:", exc)
            creds = None

    # Refresh only if the existing token can be refreshed.
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            print("Token refresh failed:", exc)
            print("Starting fresh Google authorization...")
            creds = None

    # Request authorization when no valid credentials remain.
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_FILE),
            SCOPES,
        )

        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
            include_granted_scopes="true",
        )

    # Save the newly authorized credentials.
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return creds


def test_drive_video():
    """Connect to Drive and retrieve the specified video's metadata."""

    try:
        creds = get_credentials()

        service = build(
            "drive",
            "v3",
            credentials=creds,
            cache_discovery=False,
        )

        print("\nConnected to Google Drive API.")
        print("Checking video access...\n")

        file_info = (
            service.files()
            .get(
                fileId=VIDEO_ID,
                fields="id,name,mimeType,size,webViewLink",
            )
            .execute()
        )

        print("SUCCESS: Video metadata retrieved!")
        print("Name:", file_info.get("name"))
        print("MIME type:", file_info.get("mimeType"))

        size = file_info.get("size")
        if size is not None:
            size_mb = int(size) / (1024 * 1024)
            print(f"Size: {size_mb:.2f} MB")
        else:
            print("Size: Not provided")

        print("File ID:", file_info.get("id"))
        print("\nGoogle Drive API connection works.")

    except HttpError as exc:
        print("\nGoogle Drive API request failed.")
        print("HTTP status:", exc.resp.status)
        print("Details:", exc)

    except Exception as exc:
        print("\nTest failed.")
        print(type(exc).__name__ + ":", exc)


if __name__ == "__main__":
    test_drive_video()
