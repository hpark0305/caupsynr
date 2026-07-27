"""Google Drive integration for the researcher portal.

Reads a lab Google Drive (via a service account) so files worked on in Drive
show up in the portal automatically — no manual re-upload. Read-only.

Config (environment variables):
  GOOGLE_SERVICE_ACCOUNT_JSON  — the full service-account JSON key (a string).
                                 NEVER commit this; set it as a host secret.
  GOOGLE_DRIVE_FOLDER_IDS      — optional comma-separated folder IDs override.

The Google client libraries are imported lazily so the app still boots (and
every other page works) even if they're missing or the key isn't set yet.
"""
import os
import io
import json

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Google-native files (Docs/Sheets/Slides) can't be downloaded directly — they
# must be exported. Map each to a sensible download format.
_EXPORT = {
    "application/vnd.google-apps.document":     ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet":  ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing":      ("image/png", ".png"),
}


class DriveNotConfigured(Exception):
    """Raised when the Drive credentials / config are missing or invalid."""


def is_configured():
    return bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip())


def _credentials():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise DriveNotConfigured("GOOGLE_SERVICE_ACCOUNT_JSON 환경변수가 설정되지 않았어요.")
    try:
        info = json.loads(raw)
    except Exception as e:
        raise DriveNotConfigured("GOOGLE_SERVICE_ACCOUNT_JSON 값이 올바른 JSON이 아니에요.") from e
    try:
        from google.oauth2 import service_account
    except ImportError as e:
        raise DriveNotConfigured("google-auth 라이브러리가 설치되지 않았어요.") from e
    return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)


def _service():
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        raise DriveNotConfigured("google-api-python-client 라이브러리가 설치되지 않았어요.") from e
    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def _folder_name(svc, folder_id):
    try:
        meta = svc.files().get(fileId=folder_id, fields="name", supportsAllDrives=True).execute()
        return meta.get("name", folder_id)
    except Exception:
        return folder_id


def _list_folder(svc, folder_id):
    files, page_token = [], None
    q = "'%s' in parents and trashed=false" % folder_id
    while True:
        resp = svc.files().list(
            q=q,
            fields="nextPageToken, files(id,name,mimeType,modifiedTime,size,iconLink,webViewLink)",
            pageSize=200,
            orderBy="folder,name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageToken=page_token,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return {"id": folder_id, "name": _folder_name(svc, folder_id), "files": files, "error": None}


def list_all(folder_ids):
    """Return [{id, name, files:[...], error}] for each folder, live from Drive."""
    svc = _service()
    out = []
    for fid in folder_ids:
        try:
            out.append(_list_folder(svc, fid))
        except Exception as e:
            out.append({"id": fid, "name": fid, "files": [], "error": str(e)})
    return out


def get_file(file_id):
    """Download (or export) a Drive file. Returns (bytes, mimetype, filename)."""
    svc = _service()
    meta = svc.files().get(fileId=file_id, fields="name,mimeType", supportsAllDrives=True).execute()
    name, mime = meta["name"], meta.get("mimeType", "")

    from googleapiclient.http import MediaIoBaseDownload
    buf = io.BytesIO()
    if mime in _EXPORT:
        export_mime, ext = _EXPORT[mime]
        req = svc.files().export_media(fileId=file_id, mimeType=export_mime)
        if not name.lower().endswith(ext):
            name += ext
        out_mime = export_mime
    elif mime.startswith("application/vnd.google-apps"):
        raise ValueError("이 형식의 구글 파일은 내려받을 수 없어요: " + mime)
    else:
        req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        out_mime = mime or "application/octet-stream"

    downloader = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return buf.read(), out_mime, name
