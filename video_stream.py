import os
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from google.auth.exceptions import GoogleAuthError

router = APIRouter(prefix="/api/videos", tags=["Videos"])

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = Path(os.getenv("GOOGLE_TOKEN_FILE", str(BASE_DIR / "token.json")))

SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/drive.meet.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_access_token() -> str:
    if not TOKEN_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail="Google Drive credentials are not configured.",
        )

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(GoogleRequest())
                TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            else:
                raise HTTPException(
                    status_code=401,
                    detail="Google Drive authorization has expired. Reconnect Google Drive.",
                )

        if not creds.token:
            raise HTTPException(
                status_code=401,
                detail="No Google access token is available. Reconnect Google Drive.",
            )

        return creds.token
    except HTTPException:
        raise
    except (GoogleAuthError, ValueError, OSError) as exc:
        raise HTTPException(
            status_code=401,
            detail="Unable to authorize access to Google Drive. Reconnect Google Drive.",
        ) from exc


@router.get("/{file_id}/stream")
def stream_video(file_id: str, request: Request):
    """Stream a Google Drive file, forwarding byte-range requests for video seeking."""
    if not file_id or "/" in file_id or "\\" in file_id:
        raise HTTPException(status_code=400, detail="Invalid video file ID.")

    headers = {"Authorization": f"Bearer {get_access_token()}"}
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    upstream = None
    try:
        upstream = requests.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"alt": "media"},
            headers=headers,
            stream=True,
            timeout=(15, 120),
        )

        if upstream.status_code == 404:
            upstream.close()
            raise HTTPException(status_code=404, detail="Video not found in Google Drive.")
        if upstream.status_code in (401, 403):
            upstream.close()
            raise HTTPException(
                status_code=403,
                detail="Google Drive denied access to this video. Check file permissions and OAuth authorization.",
            )
        if upstream.status_code not in (200, 206, 416):
            upstream.close()
            raise HTTPException(
                status_code=502,
                detail=f"Google Drive video request failed (HTTP {upstream.status_code}).",
            )

        response_headers = {
            "Accept-Ranges": upstream.headers.get("Accept-Ranges", "bytes"),
            "Cache-Control": "private, no-store",
        }
        for header in ("Content-Range", "Content-Length", "Content-Type"):
            value = upstream.headers.get(header)
            if value:
                response_headers[header] = value

        if upstream.status_code == 416:
            upstream.close()
            return Response(status_code=416, headers=response_headers)

        content_type = upstream.headers.get("Content-Type", "video/mp4")

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
            media_type=content_type,
            headers=response_headers,
        )
    except HTTPException:
        raise
    except requests.RequestException as exc:
        if upstream is not None:
            upstream.close()
        raise HTTPException(
            status_code=502,
            detail="Could not connect to Google Drive to stream this video.",
        ) from exc
