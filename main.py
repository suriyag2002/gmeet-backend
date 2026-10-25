import os
import json
import logging
from pathlib import Path
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse, Response
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("studpro.drive")

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
# Render Secret Files and Persistent Disk paths can be configured with environment variables.
# Local development falls back to files beside main.py.
PKCE_VERIFIER_FILE = Path(os.getenv("OAUTH_PKCE_FILE", str(BASE_DIR / "oauth_code_verifier.txt")))
CREDENTIALS_FILE = Path(
    os.getenv(
        "GOOGLE_CREDENTIALS_FILE",
        str(Path("/etc/secrets/credentials.json") if Path("/etc/secrets/credentials.json").exists()
            else BASE_DIR / "credentials.json"),
    )
)
# Render Secret Files are read-only. Read the secret token as a fallback, but
# write newly authorized/refreshed credentials to a writable path.
TOKEN_SECRET_FILE = Path(
    os.getenv(
        "GOOGLE_TOKEN_FILE",
        str(Path("/etc/secrets/token.json") if Path("/etc/secrets/token.json").exists()
            else BASE_DIR / "token.json"),
    )
)
TOKEN_WRITE_FILE = Path(
    os.getenv("GOOGLE_TOKEN_WRITE_FILE", str(BASE_DIR / "token.json"))
)
STATE_FILE = Path(os.getenv("OAUTH_STATE_FILE", str(BASE_DIR / "oauth_state.txt")))
FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "http://localhost:5500",
).rstrip("/")
BACKEND_URL = os.getenv(
    "BACKEND_URL",
    "http://localhost:8000",
).rstrip("/")
REDIRECT_URI = f"{BACKEND_URL}/auth/callback"
print("=" * 55)
print("LMS GOOGLE MEET BACKEND")
print(f"Frontend URL: {FRONTEND_URL}")
print(f"Backend URL:  {BACKEND_URL}")
print(f"OAuth redirect URI: {REDIRECT_URI}")
print("=" * 55)

SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.created",
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/drive.meet.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

app = FastAPI(title="LMS Google Meet and Drive API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
    "https://studpro-lms.netlify.app",
    "https://gmeet-test.netlify.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
def get_flow(state=None):
    if not CREDENTIALS_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail=(
                "credentials.json not found. "
                "Place your Google OAuth Web application "
                "credentials in the backend folder."
            ),
        )
    try:
        flow = Flow.from_client_secrets_file(
            str(CREDENTIALS_FILE),
            scopes=SCOPES,
            state=state,
        )
        flow.redirect_uri = REDIRECT_URI
        return flow
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not load Google OAuth credentials: {exc}",
        )
def save_credentials(credentials):
    """Save OAuth credentials only to a writable path, never to a Render Secret File."""
    TOKEN_WRITE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_WRITE_FILE.write_text(
        credentials.to_json(),
        encoding="utf-8",
    )


def get_credentials():
    # Prefer the writable token so refreshed credentials can be reused.
    token_path = TOKEN_WRITE_FILE if TOKEN_WRITE_FILE.exists() else TOKEN_SECRET_FILE

    logger.info("Loading Google credentials")
    logger.info("Selected token path: %s", token_path)
    logger.info("Token file exists: %s", token_path.exists())
    logger.info("OAuth client file path: %s", CREDENTIALS_FILE)
    logger.info("OAuth client file exists: %s", CREDENTIALS_FILE.exists())

    if not token_path.exists():
        logger.error("No Google token file found")
        raise HTTPException(
            status_code=401,
            detail="Google account is not connected. Open /auth/google to authorize.",
        )

    try:
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        logger.info(
            "Token loaded; valid=%s expired=%s refresh_token_present=%s",
            credentials.valid,
            credentials.expired,
            bool(credentials.refresh_token),
        )
        # Never log access tokens, refresh tokens, or client secrets.
        logger.debug("Scopes stored in token: %s", credentials.scopes)

        # Safe diagnostic: compare OAuth client IDs without printing either ID.
        try:
            with CREDENTIALS_FILE.open("r", encoding="utf-8") as client_file:
                client_config = json.load(client_file)
            oauth_config = client_config.get("web") or client_config.get("installed") or {}
            configured_client_id = oauth_config.get("client_id")
            token_client_id = getattr(credentials, "client_id", None)
            logger.info(
                "OAuth client ID matches saved token: %s",
                bool(configured_client_id and token_client_id and configured_client_id == token_client_id),
            )
        except Exception as diagnostic_exc:
            logger.warning(
                "Could not compare OAuth client IDs; diagnostic_type=%s",
                type(diagnostic_exc).__name__,
            )

        if credentials.expired and credentials.refresh_token:
            logger.info("Google credentials expired; attempting refresh")
            credentials.refresh(GoogleAuthRequest())
            save_credentials(credentials)
            logger.info("Google credentials refreshed and saved to %s", TOKEN_WRITE_FILE)

        if not credentials.valid:
            logger.error("Google credentials remain invalid")
            raise HTTPException(
                status_code=401,
                detail="Google credentials are invalid or expired. Reconnect your Google account.",
            )
        return credentials
    except HTTPException:
        raise
    
    except Exception as exc:
        logger.exception(
            "Google credential operation failed; exception_type=%s",
            type(exc).__name__,
        )
    
        # Log safe diagnostics only. Never log tokens or client secrets.
        logger.error("Credential file exists: %s", CREDENTIALS_FILE.exists())
        logger.error("Token source exists: %s", token_path.exists())
    
        if isinstance(exc, Exception):
            logger.error("Google credential error: %s", str(exc))
    
        raise HTTPException(
            status_code=401,
            detail="Google credentials could not be refreshed. Check backend logs.",
        ) from exc



def google_request(method, url, **kwargs):
    credentials = get_credentials()
    headers = kwargs.pop("headers", {}).copy()
    headers["Authorization"] = f"Bearer {credentials.token}"
    params_for_log = kwargs.get("params", {})
    logger.info("Google API request: %s %s", method, url)
    logger.debug("Google API params: %s", params_for_log)

    try:
        response = requests.request(
            method, url, headers=headers, timeout=30, **kwargs
        )
        logger.info("Google API response status: %s (%s)", response.status_code, url)
        if not response.ok:
            logger.error("Google API error body: %s", response.text[:1500])
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()
    except requests.HTTPError as exc:
        detail = exc.response.text[:1500] if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"Google API error: {detail}")
    except requests.RequestException as exc:
        logger.exception("Google API connection failed")
        raise HTTPException(
            status_code=502,
            detail=f"Google API connection failed: {type(exc).__name__}",
        )

# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------
@app.get("/")
def home():
    return {
        "message": "LMS Google Meet backend is running",
        "frontend_url": FRONTEND_URL,
        "backend_url": BACKEND_URL,
        "redirect_uri": REDIRECT_URI,
        "docs": f"{BACKEND_URL}/docs",
    }
@app.get("/auth/google")
def auth_google():
    flow = Flow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        autogenerate_code_verifier=True,
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    PKCE_VERIFIER_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PKCE_VERIFIER_FILE.write_text(
        flow.code_verifier or "",
        encoding="utf-8",
    )
    STATE_FILE.write_text(state, encoding="utf-8")
    return RedirectResponse(authorization_url)
@app.get("/auth/callback")
def auth_callback(request: Request):
    state = request.query_params.get("state")
    saved_state = STATE_FILE.read_text(encoding="utf-8").strip()
    if state != saved_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    flow = Flow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        scopes=SCOPES,
        state=saved_state,
        redirect_uri=REDIRECT_URI,
        autogenerate_code_verifier=False,
    )
    flow.code_verifier = PKCE_VERIFIER_FILE.read_text(
        encoding="utf-8"
    ).strip()
    authorization_response = str(request.url)
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    save_credentials(credentials)
    return RedirectResponse(FRONTEND_URL)
@app.get("/auth/status")
def auth_status():
    logger.info("Checking local Google auth status")
    if not TOKEN_WRITE_FILE.exists() and not TOKEN_SECRET_FILE.exists():
        logger.warning("No token file exists at either configured token path")
        return {"connected": False}
    try:
        credentials = get_credentials()
        return {"connected": credentials.valid, "account_authorized": True}
    except HTTPException as exc:
        logger.warning("Auth status check failed: HTTP %s", exc.status_code)
        return {"connected": False}
@app.post("/meetings")
def create_meeting():
    space = google_request(
        "POST",
        "https://meet.googleapis.com/v2/spaces",
        json={},
    )
    meeting_uri = space.get("meetingUri")
    if not meeting_uri:
        raise HTTPException(
            status_code=502,
            detail="Google did not return a meeting URI.",
        )
    return {
        "space_name": space.get("name"),
        "meeting_uri": meeting_uri,
        "meeting_code": space.get("meetingCode"),
        "message": "Google Meet space created successfully.",
    }
@app.get("/meetings/{space_id}/recordings")
def get_recordings(space_id: str):
    if not space_id or "/" in space_id or ".." in space_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid Meet space ID.",
        )
    space_name = f"spaces/{space_id}"
    conference_data = google_request(
        "GET",
        "https://meet.googleapis.com/v2/conferenceRecords",
        params={
            "filter": f'space.name = "{space_name}"',
            "pageSize": 100,
        },
    )
    results = []
    for conference in conference_data.get("conferenceRecords", []):
        record_name = conference.get("name")
        if not record_name:
            continue
        recording_data = google_request(
            "GET",
            f"https://meet.googleapis.com/v2/{record_name}/recordings",
            params={"pageSize": 100},
        )
        for recording in recording_data.get("recordings", []):
            destination = recording.get("driveDestination") or {}
            results.append({
                "recording_name": recording.get("name"),
                "state": recording.get("state"),
                "drive_file_id": destination.get("file"),
                "export_uri": destination.get("exportUri"),
            })
    return {
        "space_name": space_name,
        "recordings": results,
        "message": (
            "Recording metadata retrieved."
            if results
            else (
                "No recording artifacts found yet. "
                "The meeting may not have been recorded, "
                "or Google may still be processing it."
            )
        ),
    }
@app.get("/drive/meet-recordings")
def list_drive_recordings():
    """Diagnostic listing: list non-trashed files and log metadata, not secrets."""
    logger.info("========== DRIVE RECORDINGS DEBUG START ==========")
    logger.info("Backend directory: %s", BASE_DIR)
    logger.info("Credentials file exists: %s", CREDENTIALS_FILE.exists())
    logger.info("Token write path exists: %s", TOKEN_WRITE_FILE.exists())
    logger.info("Token secret/fallback path exists: %s", TOKEN_SECRET_FILE.exists())

    result = google_request(
        "GET",
        "https://www.googleapis.com/drive/v3/files",
        params={
            # Temporary diagnostic query: remove MIME-type filtering to see
            # whether Meet recordings are stored with a different MIME type.
            "q": "trashed = false",
            "pageSize": 100,
            "orderBy": "createdTime desc",
            "fields": "nextPageToken,files(id,name,mimeType,createdTime,webViewLink,size)",
        },
    )

    files = result.get("files", [])
    logger.info("Drive files returned on this page: %d", len(files))
    logger.info("More pages available: %s", bool(result.get("nextPageToken")))
    for item in files:
        logger.info(
            "Drive file: name=%r | id=%s | mimeType=%s | createdTime=%s | size=%s",
            item.get("name"), item.get("id"), item.get("mimeType"),
            item.get("createdTime"), item.get("size"),
        )

    video_files = [f for f in files if (f.get("mimeType") or "").startswith("video/")]
    logger.info("Video files on this page: %d", len(video_files))
    logger.info("========== DRIVE RECORDINGS DEBUG END ==========")

    return {"files": files, "nextPageToken": result.get("nextPageToken")}

@app.get("/drive/folders/{folder_id}/videos")
def list_folder_videos(folder_id: str):
    """List all video files directly inside a Google Drive folder."""
    if not folder_id or "/" in folder_id or ".." in folder_id:
        raise HTTPException(status_code=400, detail="Invalid Google Drive folder ID.")

    logger.info("Listing videos in Google Drive folder: %s", folder_id)
    all_files = []
    page_token = None
    page_number = 0

    while True:
        page_number += 1
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "pageSize": 100,
            "orderBy": "name",
            "fields": "nextPageToken,files(id,name,mimeType,createdTime,size,webViewLink)",
        }
        if page_token:
            params["pageToken"] = page_token

        result = google_request(
            "GET",
            "https://www.googleapis.com/drive/v3/files",
            params=params,
        )
        page_files = result.get("files", [])
        logger.info(
            "Folder %s page %d returned %d files",
            folder_id, page_number, len(page_files),
        )
        all_files.extend(page_files)
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    videos = []
    for item in all_files:
        mime_type = item.get("mimeType", "")
        if not mime_type.startswith("video/"):
            continue
        videos.append({
            "id": item["id"],
            "name": item.get("name", "Untitled video"),
            "mimeType": mime_type,
            "createdTime": item.get("createdTime"),
            "size": item.get("size"),
            "streamUrl": f"/api/videos/{item['id']}/stream",
        })

    logger.info(
        "Folder %s: %d total files, %d videos",
        folder_id, len(all_files), len(videos),
    )
    return {
        "folder_id": folder_id,
        "count": len(videos),
        "videos": videos,
    }


@app.get("/api/videos/{file_id}/stream")
def stream_drive_video(file_id: str, request: Request):
    """
    Stream a Google Drive video to the browser's native <video> player.
    Supports HTTP Range requests so students can seek within the video.
    """
    if not file_id or "/" in file_id or ".." in file_id:
        raise HTTPException(status_code=400, detail="Invalid Drive file ID.")
    logger.info("Stream request received for Drive file ID: %s", file_id)
    logger.info("Browser Range header present: %s", bool(request.headers.get("range")))
    credentials = get_credentials()
    if not credentials.valid or not credentials.token:
        raise HTTPException(
            status_code=401,
            detail="Google credentials are invalid. Reconnect your Google account.",
        )
    drive_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    headers = {"Authorization": f"Bearer {credentials.token}"}
    # Forward the browser's byte-range request for seeking and partial playback.
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header
    try:
        upstream = requests.get(
            drive_url,
            params={"alt": "media"},
            headers=headers,
            stream=True,
            timeout=(15, 300),
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not connect to Google Drive: {exc}",
        )
    logger.info("Google Drive stream response status=%s content_type=%s content_length=%s",
                upstream.status_code, upstream.headers.get("Content-Type"),
                upstream.headers.get("Content-Length"))
    if upstream.status_code not in (200, 206, 416):
        try:
            detail = upstream.text[:1500]
            logger.error(
                "Google Drive stream returned HTTP %s for file %s: %s",
                upstream.status_code, file_id, detail,
            )
        finally:
            upstream.close()
        raise HTTPException(
            status_code=502,
            detail=f"Google Drive returned HTTP {upstream.status_code}: {detail}",
        )
    response_headers = {
        "Accept-Ranges": upstream.headers.get("Accept-Ranges", "bytes"),
        "Content-Type": upstream.headers.get("Content-Type", "video/mp4"),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    for header_name in ("Content-Range", "Content-Length"):
        header_value = upstream.headers.get(header_name)
        if header_value:
            response_headers[header_name] = header_value
    if upstream.status_code == 416:
        upstream.close()
        return Response(
            status_code=416,
            headers=response_headers,
        )
    def video_chunks():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    yield chunk
        except Exception as exc:
            logger.exception(
                "Error while reading Google Drive stream for file %s", file_id
            )
            raise
        finally:
            upstream.close()
    return StreamingResponse(
        video_chunks(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=response_headers["Content-Type"],
    )
