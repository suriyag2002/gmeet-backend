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
