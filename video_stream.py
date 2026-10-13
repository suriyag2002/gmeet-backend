
import os
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest

router = APIRouter(prefix="/api/videos", tags=["Videos"])

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / "token.json"

# Use the exact ID from your successful Python test.
VIDEO_ID = "106erqKvsZIKKS0ni8SrgrtsofXCUONK1"

SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/drive.meet.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_access_token():
    if not TOKEN_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail="Google Drive credentials are not configured.",
        )

    creds = Credentials.from_authorized_user_file(
        str(TOKEN_FILE),
        SCOPES,
    )

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
        else:
            raise HTTPException(
                status_code=500,
                detail="Google Drive authorization has expired.",
            )

        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return creds.token


@router.get("/{file_id}/stream")
def stream_video(file_id: str, request: Request):
    # For this first test, allow only your known video.
    # Later, look up authorized videos in your database.
    if file_id != VIDEO_ID:
        raise HTTPException(status_code=404, detail="Video not found.")

    headers = {
        "Authorization": f"Bearer {get_access_token()}",
    }

    # Forward the browser's requested byte range.
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    upstream = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}",
        params={"alt": "media"},
        headers=headers,
        stream=True,
        timeout=(15, 120),
    )

    if upstream.status_code not in (200, 206, 416):
        upstream.close()
        raise HTTPException(
            status_code=502,
            detail="Google Drive could not provide the video.",
        )

    response_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
    }

    for header in (
        "Content-Range",
        "Content-Length",
        "Content-Type",
    ):
        value = upstream.headers.get(header)
        if value:
            response_headers[header] = value

    if upstream.status_code == 416:
        upstream.close()
        return Response(
            status_code=416,
            headers=response_headers,
        )

    def video_chunks():
        try:
            for chunk in upstream.iter_content(chunk_size=256 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        video_chunks(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type", "video/mp4"),
        headers=response_headers,
    )
