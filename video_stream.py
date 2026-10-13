
import os
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest

router = APIRouter(prefix="/api/videos", tags=["Videos"])

# Render Secret File path. Override using an environment variable if needed.
TOKEN_FILE = Path(
    os.getenv("GOOGLE_TOKEN_FILE", "/etc/secrets/token.json")
)

# Exact Google Drive file ID
VIDEO_ID = "106erqKvsZIKkSOni8SrgrtsofXcUONKl"

SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/drive.meet.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_access_token():
    print(f"[VideoStream] Token file path: {TOKEN_FILE}")
    print(f"[VideoStream] Token file exists: {TOKEN_FILE.exists()}")

    if not TOKEN_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail="Google token file not found. Check GOOGLE_TOKEN_FILE in Render.",
        )

    try:
        creds = Credentials.from_authorized_user_file(
            str(TOKEN_FILE),
            SCOPES,
        )

        print("[VideoStream] Credentials loaded successfully")
        print(f"[VideoStream] Credentials valid: {creds.valid}")
        print(f"[VideoStream] Credentials expired: {creds.expired}")
        print(
            "[VideoStream] Refresh token available: "
            f"{bool(creds.refresh_token)}"
        )

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                print("[VideoStream] Attempting Google token refresh")

                creds.refresh(GoogleRequest())

                print("[VideoStream] Google token refresh successful")
                # Do not write to TOKEN_FILE: Render Secret Files are read-only.
            else:
                raise HTTPException(
                    status_code=401,
                    detail=(
                        "Google authorization is invalid or the refresh "
                        "token is missing. Reconnect Google."
                    ),
                )

        if not creds.token:
            raise HTTPException(
                status_code=401,
                detail="Google access token is unavailable.",
            )

        return creds.token

    except HTTPException:
        raise

    except Exception as exc:
        # Never log access tokens, refresh tokens, or client secrets.
        print(
            f"[VideoStream] Credential error: "
            f"{type(exc).__name__}: {exc}"
        )

        raise HTTPException(
            status_code=401,
            detail=(
                f"Google credential error: {type(exc).__name__}: {exc}"
            ),
        )


@router.get("/{file_id}/stream")
def stream_video(file_id: str, request: Request):
    print(f"[VideoStream] Requested file ID: {file_id}")

    if file_id != VIDEO_ID:
        raise HTTPException(
            status_code=404,
            detail="Video not found.",
        )

    access_token = get_access_token()

    headers = {
        "Authorization": f"Bearer {access_token}",
    }

    # Forward browser byte-range requests for video seeking.
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header
        print(f"[VideoStream] Range request: {range_header}")

    try:
        upstream = requests.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"alt": "media"},
            headers=headers,
            stream=True,
            timeout=(15, 120),
        )

    except requests.RequestException as exc:
        print(f"[VideoStream] Google Drive connection error: {exc}")

        raise HTTPException(
            status_code=502,
            detail="Could not connect to Google Drive.",
        )

    print(
        f"[VideoStream] Google Drive HTTP status: "
        f"{upstream.status_code}"
    )

    if upstream.status_code not in (200, 206, 416):
        status = upstream.status_code

        # Google may return useful error details. Do not log credentials.
        try:
            error_body = upstream.text[:1000]
            print(f"[VideoStream] Google Drive error: {error_body}")
        finally:
            upstream.close()

        raise HTTPException(
            status_code=502,
            detail=(
                f"Google Drive returned HTTP {status}. "
                "Check token permissions and file access."
            ),
        )

    response_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
    }

    for name in (
        "Content-Range",
        "Content-Length",
        "Content-Type",
    ):
        value = upstream.headers.get(name)
        if value:
            response_headers[name] = value

    if upstream.status_code == 416:
        upstream.close()
        return Response(
            status_code=416,
            headers=response_headers,
        )

    def video_chunks():
        try:
            for chunk in upstream.iter_content(
                chunk_size=256 * 1024
            ):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        video_chunks(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get(
            "Content-Type",
            "video/mp4",
        ),
        headers=response_headers,
    )
