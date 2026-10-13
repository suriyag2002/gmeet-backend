
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# --------------------------------------------------
# Configuration
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / "token.json"

# Exact video ID from your successful metadata test
VIDEO_ID = "106erqKvsZIKkSOni8SrgrtsofXcUONKl"

SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/drive.meet.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def main():
    # 1. Check that the token file exists
    if not TOKEN_FILE.exists():
        print(f"Token file not found: {TOKEN_FILE}")
        print("Check whether your successful test created token.json.")
        return

    # 2. Load Google credentials
    creds = Credentials.from_authorized_user_file(
        str(TOKEN_FILE),
        SCOPES,
    )

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            print("Refreshing Google credentials...")
            creds.refresh(Request())
        else:
            print("Credentials are invalid or cannot be refreshed.")
            print("Run test_drive.py to authorize again.")
            return

    # 3. Connect to Google Drive API
    service = build(
        "drive",
        "v3",
        credentials=creds,
        cache_discovery=False,
    )

    # 4. Verify the video ID before requesting its content
    print("Testing video ID:", repr(VIDEO_ID))

    try:
        file_info = service.files().get(
            fileId=VIDEO_ID,
            fields="id,name,mimeType,size"
        ).execute()

        print("\nVideo found successfully!")
        print("Name:", file_info.get("name"))
        print("MIME type:", file_info.get("mimeType"))

        size = int(file_info.get("size", 0))
        print(f"Size: {size / (1024 * 1024):.2f} MB")

    except Exception as exc:
        print("\nCould not find the video.")
        print("Check the file ID and Google account permissions.")
        print("Error:", exc)
        return

    # 5. Request only the first 1 MB of video data
    # This avoids downloading the entire video into memory.
    print("\nTesting partial video download...")

    try:
        request = service.files().get_media(fileId=VIDEO_ID)

        # Generate an authorized request URL.
        # The request is authenticated using the current access token.
        request.headers["Range"] = "bytes=0-1048575"

        response = requests.get(
            request.uri,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Range": "bytes=0-1048575",
            },
            stream=True,
            timeout=60,
        )

        print("HTTP status:", response.status_code)

        if response.status_code == 206:
            first_chunk = next(
                response.iter_content(chunk_size=64 * 1024),
                b"",
            )
            print("Partial content received successfully!")
            print("First chunk size:", len(first_chunk), "bytes")
            print("Content-Range:", response.headers.get("Content-Range"))
            print("\nGoogle Drive partial-content test passed.")

        elif response.status_code == 200:
            print("Google Drive returned HTTP 200 instead of 206.")
            print("The server may have ignored the Range header.")
            print("Check the response headers and streaming behavior.")

        else:
            print("Partial-content test failed.")
            print(response.text[:1000])

        response.close()

    except Exception as exc:
        print("Error while testing video content:", exc)


if __name__ == "__main__":
    main()
