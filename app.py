from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response, abort
from collections import OrderedDict
from functools import wraps
from dotenv import load_dotenv
import os, sqlite3, uuid as _uuid, json, csv, io, re, hashlib, hmac, time
import requests as _requests
from urllib.parse import quote, parse_qsl
from werkzeug.exceptions import HTTPException
from zipfile import BadZipFile
from file_storage import PrivateStorage, StorageUnavailable
import drive_sync
from security_utils import DOCUMENT_EXTENSIONS, validate_archive, validate_image, SafeCSVWriter, neutralize_excel_formulas, validate_production_settings, password_policy_error
from scipy import stats as _scipy_stats
import numpy as _np
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv()

APP_API_KEY = os.getenv("APP_API_KEY", "")
if not APP_API_KEY:
    raise SystemExit(
        "APP_API_KEY 환경변수가 설정되지 않았습니다. "
        "외부 앱(Wardy/NeuroBreeze/Mind Mate)이 쓸 API 키를 .env 또는 배포 환경변수에 설정한 뒤 다시 시작하세요."
    )

MINDMATE_PEPPER = os.getenv("MINDMATE_PEPPER", "")
if not MINDMATE_PEPPER:
    raise SystemExit(
        "MINDMATE_PEPPER 환경변수가 설정되지 않았습니다. "
        "Mind Mate 참여자 식별에 쓸 HMAC pepper 값을 .env 또는 배포 환경변수에 설정한 뒤 다시 시작하세요."
    )

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
validate_production_settings(os.environ)
_USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)
if _USE_SUPABASE and not SUPABASE_URL.startswith('https://'):
    raise SystemExit('SUPABASE_URL은 HTTPS 주소여야 합니다.')

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "")
if len(app.secret_key) < 32 or app.secret_key == "tsl-dev-secret-2025":
    raise SystemExit("SECRET_KEY에 32자 이상의 무작위 비밀값을 설정하세요. 기본값은 사용할 수 없습니다.")
app.config.update(
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "true").lower() != "false",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_REFRESH_EACH_REQUEST=False,
    MAX_FORM_PARTS=2000,
    MAX_FORM_MEMORY_SIZE=512 * 1024,
)
ADMIN_EMAILS = {s.strip().lower() for s in os.getenv("PORTAL_ADMIN_EMAILS", "").split(",") if s.strip()}
app.config['WTF_CSRF_TIME_LIMIT'] = 3600
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB upload limit
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)  # keep researchers logged in
csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[],
                  storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"))

DB_PATH = os.getenv("PORTAL_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), 'portal.db'))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), 'portal_files'))
os.makedirs(DATA_DIR, exist_ok=True)
PORTAL_FILES_DIR = DATA_DIR  # alias kept for backward compat

GALLERY_FOLDER    = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'images', 'gallery')
GALLERY_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gallery_data.json')
APPS_EXTRA_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'apps_extra.json')
PUBS_EXTRA_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pubs_extra.json')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'avif', 'webp'}

def allowed_file(f): return '.' in f and f.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def _q(value):
    """Encode one query value, never a whole PostgREST query fragment."""
    return quote(str(value), safe="")

def _stored_path(root, *parts):
    root = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root, *parts))
    if path == root or os.path.commonpath([root, path]) != root:
        abort(400)
    return path

@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if app.config['SESSION_COOKIE_SECURE']:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    if request.path.startswith(('/portal', '/api', '/login', '/reset-password')) or session.get('researcher'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.errorhandler(HTTPException)
def safe_http_error(error):
    messages = {400: '요청을 확인해주세요. 페이지를 새로고침한 뒤 다시 시도해주세요.',
                403: '접근 권한이 없습니다.', 404: '요청한 항목을 찾을 수 없습니다.',
                405: '허용되지 않는 요청 방식입니다.', 413: '파일 또는 요청이 너무 큽니다.',
                429: '요청이 너무 많습니다. 잠시 후 다시 시도해주세요.'}
    response = error.get_response()
    response.data = json.dumps({'ok': False, 'error': messages.get(error.code, '요청을 처리하지 못했습니다.')}, ensure_ascii=False)
    response.content_type = 'application/json; charset=utf-8'
    return response

@app.errorhandler(500)
def safe_server_error(error):
    return jsonify(ok=False, error='서버 처리 중 오류가 발생했습니다.'), 500

@app.before_request
def validate_api_body():
    if not request.path.startswith('/api/') or request.method != 'POST':
        return
    request.max_content_length = 2 * 1024 * 1024
    if not hmac.compare_digest(request.headers.get('X-API-Key', '').encode(), APP_API_KEY.encode()):
        return jsonify(error='Unauthorized'), 401
    if request.content_length and request.content_length > 2 * 1024 * 1024:
        abort(413)
    if request.is_json:
        body = request.get_json(silent=True)
        batch = body if isinstance(body, list) else [body]
        if not batch or len(batch) > 500 or any(not isinstance(row, dict) for row in batch):
            abort(400)
        if request.endpoint in ('api_receive_session', 'api_mindmate_followup') and not isinstance(body, dict):
            abort(400)

def _file_store():
    return PrivateStorage(SUPABASE_URL, SUPABASE_KEY)

@app.errorhandler(StorageUnavailable)
def storage_unavailable(error):
    app.logger.warning('Persistent file storage operation failed')
    return Response('파일 저장소에 연결하지 못했습니다. 저장 완료로 처리하지 않았습니다. 잠시 후 다시 시도해주세요.',
                    status=503, content_type='text/plain; charset=utf-8')

def _load_site_json(path):
    if _USE_SUPABASE:
        content = _file_store().get('site/' + os.path.basename(path))
        if content is not None:
            try:
                value = json.loads(content)
                if not isinstance(value, list): raise ValueError('Expected list')
                return value
            except ValueError as exc:
                raise StorageUnavailable('Invalid site metadata') from exc
    if not os.path.exists(path): return []
    with open(path, encoding='utf-8') as f: return json.load(f)

def _save_site_json(path, data):
    if _USE_SUPABASE:
        _file_store().put('site/' + os.path.basename(path), json.dumps(data, ensure_ascii=False).encode(),
                          content_type='application/json', replace=True)
    else:
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)

def _save_portal_upload(upload, project_id=None):
    name = str(_uuid.uuid4()) + os.path.splitext(upload.filename)[1].lower()
    upload.seek(0)
    if _USE_SUPABASE:
        content = upload.read()
        _file_store().put('portal/' + name, content)
        return 'storage/' + name, len(content)
    folder = _stored_path(DATA_DIR, f'project_{project_id}') if project_id else DATA_DIR
    os.makedirs(folder, exist_ok=True)
    path = _stored_path(folder, name)
    upload.save(path)
    return name, os.path.getsize(path)

def _portal_storage_key(filename):
    if not re.fullmatch(r'storage/[0-9a-f-]{36}\.[a-z0-9]+', filename):
        abort(400)
    return 'portal/' + filename.split('/', 1)[1]

def load_events():
    return _load_site_json(GALLERY_DATA_FILE)

def save_events(events):
    _save_site_json(GALLERY_DATA_FILE, events)

def event_photos(key):
    folder = _stored_path(GALLERY_FOLDER, key)
    photos = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder,f)) and allowed_file(f)] if os.path.isdir(folder) else []
    photos.sort(key=lambda f: os.path.getmtime(os.path.join(folder,f)))
    if _USE_SUPABASE:
        photos += [row['name'] for row in _file_store().list('gallery/' + key)
                   if row.get('id') and row.get('name') and allowed_file(row['name'])
                   and secure_filename(row['name']) == row['name'] and row['name'] not in photos]
        event = next((e for e in load_events() if e['key'] == key), {})
        photos = [p for p in photos if p not in event.get('hidden_photos', [])]
    return photos

def load_extra_apps():
    return _load_site_json(APPS_EXTRA_FILE)

def save_extra_apps(data):
    _save_site_json(APPS_EXTRA_FILE, data)

def load_extra_pubs():
    return _load_site_json(PUBS_EXTRA_FILE)

def save_extra_pubs(data):
    _save_site_json(PUBS_EXTRA_FILE, data)

def init_db():
    os.makedirs(PORTAL_FILES_DIR, exist_ok=True)
    if _USE_SUPABASE:
        return  # Tables managed in Supabase; skip local SQLite init
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS portal_auth_sessions (
                id TEXT PRIMARY KEY,
                researcher_email TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                last_seen INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                app_type TEXT,
                researcher_email TEXT NOT NULL,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS project_participants (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                code TEXT NOT NULL,
                gender TEXT,
                age INTEGER,
                group_name TEXT,
                enrolled_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS measurements (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                phase TEXT,
                notes TEXT,
                data TEXT DEFAULT '{}',
                measured_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS project_variables (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                label TEXT,
                var_type TEXT DEFAULT 'number',
                unit TEXT
            );
            CREATE TABLE IF NOT EXISTS portal_files (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL,
                size INTEGER DEFAULT 0,
                researcher_email TEXT NOT NULL,
                uploaded_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS accounts (
                email TEXT PRIMARY KEY,
                password TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS news (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT,
                researcher_email TEXT NOT NULL,
                published INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS research_topics_extra (
                key TEXT PRIMARY KEY,
                title TEXT,
                summary TEXT,
                detail TEXT
            );
            CREATE TABLE IF NOT EXISTS contact_messages (
                id TEXT PRIMARY KEY,
                name TEXT,
                email TEXT,
                subject TEXT,
                message TEXT NOT NULL,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                participant_id TEXT,
                phase TEXT,
                notes TEXT,
                data TEXT DEFAULT '{}',
                received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                researcher TEXT,
                action TEXT NOT NULL,
                target_table TEXT,
                target_id TEXT,
                detail TEXT,
                before_val TEXT,
                after_val TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS project_collaborators (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                researcher_email TEXT NOT NULL,
                role TEXT DEFAULT 'editor',
                invited_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS reset_tokens (
                token TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS project_protocols (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                phase_name TEXT NOT NULL,
                description TEXT,
                due_offset_days INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                title TEXT NOT NULL,
                description TEXT,
                assigned_to TEXT,
                due_date TEXT,
                status TEXT DEFAULT 'todo',
                priority TEXT DEFAULT 'normal',
                created_by TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS wardy_events (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                user_name TEXT NOT NULL,
                seq INTEGER,
                event_time TEXT,
                state TEXT NOT NULL,
                game_level TEXT,
                data TEXT DEFAULT '{}',
                received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS neurobreeze_eeg (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                user_name TEXT NOT NULL,
                session_num INTEGER,
                recorded_at TEXT,
                left_alpha_power REAL,
                right_alpha_power REAL,
                alpha_power REAL,
                left_alpha_indicator REAL,
                right_alpha_indicator REAL,
                alpha_indicator REAL,
                received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS neurobreeze_meditation (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                user_name TEXT NOT NULL,
                session_num INTEGER,
                start_time TEXT,
                end_time TEXT,
                feedback_count INTEGER,
                breath_mode TEXT,
                left_alpha_power REAL,
                right_alpha_power REAL,
                alpha_power REAL,
                left_alpha_indicator REAL,
                right_alpha_indicator REAL,
                alpha_indicator REAL,
                start_alpha_power_avg REAL,
                end_alpha_power_avg REAL,
                start_alpha_indicator_avg REAL,
                end_alpha_indicator_avg REAL,
                alpha_power_increase REAL,
                alpha_indicator_increase REAL,
                alpha_progress_rate REAL,
                received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS mindmate_followup (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                user_name TEXT,
                followup_round TEXT,
                submitted_at TEXT,
                source TEXT,
                data TEXT DEFAULT '{}',
                received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS mindmate_music_log (
                id TEXT PRIMARY KEY,
                participant_key TEXT NOT NULL,
                date TEXT,
                start_time TEXT,
                end_time TEXT,
                music_name TEXT,
                music_type TEXT,
                raw TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS mindmate_daily_survey (
                id TEXT PRIMARY KEY,
                participant_key TEXT NOT NULL,
                date TEXT NOT NULL,
                work_type TEXT,
                is_survey_daily TEXT,
                servey_daily_1 TEXT, servey_daily_2 TEXT, servey_daily_3 TEXT,
                is_mind_healing TEXT,
                servey_mindhealing_1 TEXT,
                is_five_days TEXT,
                servey_five_1 TEXT, servey_five_2 TEXT, servey_five_3 TEXT, servey_five_4 TEXT,
                servey_five_5 TEXT, servey_five_6 TEXT, servey_five_7 TEXT, servey_five_8 TEXT,
                servey_five_9 TEXT, servey_five_10 TEXT, servey_five_11 TEXT, servey_five_12 TEXT,
                servey_five_result TEXT,
                source TEXT,
                raw TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS mindmate_app_open (
                id TEXT PRIMARY KEY,
                participant_key TEXT NOT NULL,
                raw TEXT DEFAULT '{}',
                received_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
            );
        ''')
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        # Seed default accounts via environment variables only
        existing = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        if existing == 0:
            seed_email = os.getenv("SEED_ADMIN_EMAIL","")
            seed_pw    = os.getenv("SEED_ADMIN_PASSWORD","")
            if seed_email and not password_policy_error(seed_pw, seed_email):
                conn.execute("INSERT INTO accounts (email,password) VALUES (?,?)",
                             (seed_email, generate_password_hash(seed_pw)))
                conn.commit()
    # audit_log used to have an INTEGER PRIMARY KEY AUTOINCREMENT id locally, but audit()
    # always writes a UUID string for id (matching the Supabase text-id schema) — so every
    # insert failed with "datatype mismatch" and was silently swallowed. Rebuild onto a
    # TEXT id if an old-schema table is still around.
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchone()
        if row and row[0] and "INTEGER PRIMARY KEY" in row[0]:
            try:
                old_cols = {r[1] for r in conn.execute("PRAGMA table_info(audit_log)")}
                copy_cols = [c for c in ("researcher","action","target_table","target_id",
                                          "detail","before_val","after_val","created_at")
                             if c in old_cols]
                conn.execute("ALTER TABLE audit_log RENAME TO audit_log_old")
                conn.execute('''CREATE TABLE audit_log (
                    id TEXT PRIMARY KEY,
                    researcher TEXT,
                    action TEXT NOT NULL,
                    target_table TEXT,
                    target_id TEXT,
                    detail TEXT,
                    before_val TEXT,
                    after_val TEXT,
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))
                )''')
                conn.execute(
                    f"INSERT INTO audit_log (id, {','.join(copy_cols)}) "
                    f"SELECT lower(hex(randomblob(16))), {','.join(copy_cols)} FROM audit_log_old"
                )
                conn.execute("DROP TABLE audit_log_old")
                conn.commit()
            except Exception as e:
                print(f"audit_log 스키마 이전 실패 (감사 로그는 계속 비활성 상태일 수 있음): {e}")

    # Migrations: add columns if missing
    _migrations = [
        ("measurements",    "ALTER TABLE measurements ADD COLUMN excluded INTEGER DEFAULT 0"),
        ("measurements",    "ALTER TABLE measurements ADD COLUMN source_file_id TEXT"),
        ("portal_files",    "ALTER TABLE portal_files ADD COLUMN column_schema TEXT DEFAULT '[]'"),
        ("portal_files",    "ALTER TABLE portal_files ADD COLUMN row_count INTEGER DEFAULT 0"),
        ("audit_log",       "ALTER TABLE audit_log ADD COLUMN before_val TEXT"),
        ("audit_log",       "ALTER TABLE audit_log ADD COLUMN after_val TEXT"),
        ("mindmate_daily_survey", "ALTER TABLE mindmate_daily_survey ADD COLUMN source TEXT"),
    ]
    with sqlite3.connect(DB_PATH) as conn:
        for _, stmt in _migrations:
            try: conn.execute(stmt); conn.commit()
            except Exception: pass
        # Indexes
        for idx_stmt in [
            "CREATE INDEX IF NOT EXISTS idx_m_project      ON measurements(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_m_participant  ON measurements(participant_id)",
            "CREATE INDEX IF NOT EXISTS idx_pp_project     ON project_participants(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_pv_project     ON project_variables(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_m_measured_at  ON measurements(measured_at)",
            "CREATE INDEX IF NOT EXISTS idx_audit_created  ON audit_log(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_proj_researcher ON projects(researcher_email)",
            "CREATE INDEX IF NOT EXISTS idx_collab_project  ON project_collaborators(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_mm_music_pkey   ON mindmate_music_log(participant_key)",
            "CREATE INDEX IF NOT EXISTS idx_mm_survey_pkey  ON mindmate_daily_survey(participant_key)",
            "CREATE INDEX IF NOT EXISTS idx_mm_open_pkey    ON mindmate_app_open(participant_key)",
            "CREATE INDEX IF NOT EXISTS idx_mm_open_received ON mindmate_app_open(received_at DESC)",
        ]:
            try: conn.execute(idx_stmt)
            except Exception: pass
        # Unique index for participant code per project
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pp_code ON project_participants(project_id, code)")
        except Exception: pass
        conn.commit()

def audit(action, table=None, target_id=None, detail=None, before=None, after=None):
    """Write an entry to the audit_log table. Never raises (a broken audit log
    shouldn't break the action it's logging) — but failures are printed, not swallowed."""
    try:
        researcher = session.get("researcher", "system")
        before_val = json.dumps(before, ensure_ascii=False) if before is not None else None
        after_val  = json.dumps(after,  ensure_ascii=False) if after  is not None else None
        result = sb("POST", "audit_log", data={
            "id": str(_uuid.uuid4()),
            "researcher": researcher,
            "action": action,
            "target_table": table,
            "target_id": target_id,
            "detail": detail,
            "before_val": before_val,
            "after_val": after_val,
        })
        if not result:
            print(f"audit() 저장 실패: action={action}, table={table}, target_id={target_id}")
    except Exception as e:
        print(f"audit() 예외로 기록 실패: action={action}, table={table}, target_id={target_id} — {e}")

_SAFE_IDENT = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
_ALLOWED_TABLES = {
    'portal_auth_sessions',
    'projects','project_participants','measurements','project_variables',
    'portal_files','accounts','news','research_topics_extra','contact_messages',
    'sessions','audit_log','project_collaborators','reset_tokens','project_protocols',
    'wardy_events','tasks',
    'neurobreeze_eeg','neurobreeze_meditation',
    'mindmate_followup', 'mindmate_music_log', 'mindmate_daily_survey', 'mindmate_app_open',
}

def _safe_col(col):
    col = col.strip()
    return col if (col == '*' or _SAFE_IDENT.match(col)) else None

def _parse_sb_params(params):
    filters, order_by, select_fields = {}, None, "*"
    if not params:
        return filters, order_by, select_fields
    for key, val in parse_qsl(params.lstrip('?'), keep_blank_values=True):
        if key == "select":
            cols = [_safe_col(c) for c in val.split(",")]
            if all(cols):
                select_fields = ",".join(cols)
        elif key == "order":
            pieces = val.split(".")
            col = _safe_col(pieces[0])
            if col:
                direction = "DESC" if len(pieces) > 1 and pieces[1].lower() == "desc" else "ASC"
                order_by = f"{col} {direction}"
        elif _SAFE_IDENT.match(key) and val.startswith("eq."):
            filters[key] = val[3:]
    return filters, order_by, select_fields

def _sb_headers(prefer=None):
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h

def sb(method, table, data=None, params="", upsert=False):
    if table not in _ALLOWED_TABLES:
        print(f"DB error: disallowed table '{table}'")
        return []
    if data and any(not _SAFE_IDENT.fullmatch(k) for k in data):
        raise ValueError('Invalid database column')

    # ── Supabase REST API mode ──────────────────────────
    if _USE_SUPABASE:
        url = f"{SUPABASE_URL}/rest/v1/{table}{params}"
        try:
            if method == "GET":
                r = _requests.get(url, headers=_sb_headers(), timeout=10)
                r.raise_for_status()
                return r.json() or []
            elif method == "POST":
                d = dict(data or {})
                if table != "accounts":
                    d.setdefault("id", str(_uuid.uuid4()))
                prefer = ("resolution=merge-duplicates,return=representation"
                          if upsert else "return=representation")
                r = _requests.post(url, headers=_sb_headers(prefer), json=d, timeout=10)
                r.raise_for_status()
                result = r.json()
                return result if isinstance(result, list) else ([result] if result else [])
            elif method == "PATCH":
                r = _requests.patch(url, headers=_sb_headers("return=minimal"),
                                    json=dict(data or {}), timeout=10)
                r.raise_for_status()
                return []
            elif method == "DELETE":
                r = _requests.delete(url, headers=_sb_headers(), timeout=10)
                r.raise_for_status()
                return []
        except Exception as e:
            app.logger.error("Database operation failed: %s %s (%s)", method, table, type(e).__name__)
            if table == 'portal_auth_sessions':
                abort(503)
            return []

    # ── SQLite fallback (local dev) ─────────────────────
    filters, order_by, select_fields = _parse_sb_params(params)
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        if method == "GET":
            sql = f"SELECT {select_fields} FROM {table}"
            vals = []
            if filters:
                sql += " WHERE " + " AND ".join(f"{k}=?" for k in filters)
                vals = list(filters.values())
            if order_by:
                sql += f" ORDER BY {order_by}"
            paging = dict(parse_qsl(params.lstrip('?')))
            if 'limit' in paging:
                limit = max(0, min(int(paging['limit']), 10000))
                offset = max(0, int(paging.get('offset', '0')))
                sql += ' LIMIT ? OFFSET ?'
                vals.extend([limit, offset])
            rows = conn.execute(sql, vals).fetchall()
            result = []
            for row in rows:
                r = dict(row)
                if "data" in r and isinstance(r["data"], str):
                    try: r["data"] = json.loads(r["data"])
                    except: pass
                result.append(r)
            return result
        elif method == "POST":
            if not data: return []
            d = dict(data)
            if table != "accounts":
                d.setdefault("id", str(_uuid.uuid4()))
            if "data" in d and isinstance(d["data"], dict):
                d["data"] = json.dumps(d["data"], ensure_ascii=False)
            cols = list(d.keys())
            if upsert:
                pk = "email" if table == "accounts" else "id"
                non_pk = [c for c in cols if c != pk]
                conn.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?'*len(cols))}) "
                    f"ON CONFLICT({pk}) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in non_pk)}",
                    [d[c] for c in cols])
            else:
                conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                             [d[c] for c in cols])
            conn.commit()
            pk_val = d.get("email") if table == "accounts" else d.get("id")
            pk_col = "email" if table == "accounts" else "id"
            row = conn.execute(f"SELECT * FROM {table} WHERE {pk_col}=?", [pk_val]).fetchone()
            if row:
                r = dict(row)
                if "data" in r and isinstance(r["data"], str):
                    try: r["data"] = json.loads(r["data"])
                    except: pass
                return [r]
            return []
        elif method == "PATCH":
            if not data or not filters: return []
            d = dict(data)
            if "data" in d and isinstance(d["data"], dict):
                d["data"] = json.dumps(d["data"], ensure_ascii=False)
            sql = f"UPDATE {table} SET {', '.join(f'{k}=?' for k in d)} WHERE {' AND '.join(f'{k}=?' for k in filters)}"
            conn.execute(sql, list(d.values()) + list(filters.values()))
            conn.commit()
            return []
        elif method == "DELETE":
            if not filters: return []
            sql = f"DELETE FROM {table} WHERE {' AND '.join(f'{k}=?' for k in filters)}"
            conn.execute(sql, list(filters.values()))
            conn.commit()
            return []
    except Exception as e:
        app.logger.error("Database operation failed: %s %s (%s)", method, table, type(e).__name__)
        if table == 'portal_auth_sessions':
            abort(503)
        return []
    finally:
        if conn: conn.close()

def _sb_count(table, params=""):
    """Return total row count for a table. Used for pagination."""
    if table not in _ALLOWED_TABLES:
        raise ValueError('Invalid database table')
    if _USE_SUPABASE:
        url = f"{SUPABASE_URL}/rest/v1/{table}{params if params else '?select=id'}"
        if "select=" not in url:
            url = url + ("&" if "?" in url else "?") + "select=id"
        try:
            r = _requests.get(url, headers={**_sb_headers(), "Prefer": "count=exact"}, timeout=10)
            cr = r.headers.get("Content-Range", "0/0")
            return int(cr.split("/")[-1]) if "/" in cr else 0
        except Exception:
            return 0
    else:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            return 0

def _get_account(email):
    rows = sb("GET", "accounts", params=f"?email=eq.{_q(email)}&select=password")
    return rows[0]["password"] if rows else None

def _set_account(email, password, already_hashed=False):
    if not already_hashed and password_policy_error(password, email):
        raise ValueError('Password does not meet policy')
    hashed = password if already_hashed else generate_password_hash(password, method='pbkdf2:sha256')
    if not sb("POST", "accounts", data={"email": email, "password": hashed}, upsert=True):
        abort(503)

def _account_exists(email):
    rows = sb("GET", "accounts", params=f"?email=eq.{_q(email)}&select=email")
    return bool(rows)

def _seed_supabase_account():
    """Bootstrap only explicitly configured credentials; never reset on a failed read."""
    email, password = os.getenv('PORTAL_EMAIL', ''), os.getenv('PORTAL_PASSWORD', '')
    if not (_USE_SUPABASE and email and not password_policy_error(password, email)):
        return
    response = _requests.get(f'{SUPABASE_URL}/rest/v1/accounts?select=email&limit=1',
                             headers=_sb_headers(), timeout=10)
    response.raise_for_status()
    if response.json() == []:
        _set_account(email, password)

init_db()
_seed_supabase_account()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        email = session.get('researcher')
        stored = _get_account(email) if email else None
        if not stored or not hmac.compare_digest(session.get('auth_stamp', ''), _auth_stamp(stored)) or not _valid_login_session():
            session.clear()
            if request.is_json:
                return jsonify(ok=False, error='Login required'), 401
            return redirect(url_for("login"))
        _authorize_portal_request()
        return f(*args, **kwargs)
    return decorated

def _auth_stamp(password_hash):
    return hmac.new(app.secret_key.encode(), password_hash.encode(), hashlib.sha256).hexdigest()

def _start_login_session(email, password_hash):
    now = int(time.time())
    sid = str(_uuid.uuid4())
    if not sb('POST', 'portal_auth_sessions', data={
        'id': sid, 'researcher_email': email, 'created_at': now, 'last_seen': now,
    }):
        abort(503)
    session.clear()
    session.permanent = True
    session.update(researcher=email, auth_stamp=_auth_stamp(password_hash), auth_session_id=sid)

def _valid_login_session():
    sid = session.get('auth_session_id')
    if not sid:
        return False
    query = f"?id=eq.{_q(sid)}&researcher_email=eq.{_q(session['researcher'])}"
    rows = sb('GET', 'portal_auth_sessions', params=query)
    if not rows:
        return False
    now = int(time.time())
    if now - int(rows[0]['created_at']) >= 8 * 3600 or now - int(rows[0]['last_seen']) >= 30 * 60:
        sb('DELETE', 'portal_auth_sessions', params=query)
        return False
    if now - int(rows[0]['last_seen']) >= 60:
        sb('PATCH', 'portal_auth_sessions', data={'last_seen': now}, params=query)
    return True

def _is_admin():
    return session.get('researcher', '').lower() in ADMIN_EMAILS

def _project_role(project_id):
    email = session['researcher']
    projects = sb('GET', 'projects', params=f'?id=eq.{_q(project_id)}')
    if not projects:
        return None
    if projects[0].get('researcher_email') == email:
        return 'owner'
    rows = sb('GET', 'project_collaborators',
              params=f'?project_id=eq.{_q(project_id)}&researcher_email=eq.{_q(email)}')
    return rows[0].get('role') if rows else None

def _authorize_project(project_id, write=False, owner_only=False):
    roles = {'owner'} if owner_only else ({'owner', 'editor'} if write else {'owner', 'editor', 'viewer'})
    if not project_id or _project_role(project_id) not in roles:
        abort(403)

def _authorize_portal_request():
    """Check URL and hidden-field IDs before any authenticated handler runs."""
    endpoint = request.endpoint or ''
    write = request.method not in ('GET', 'HEAD', 'OPTIONS')
    admin_routes = {'portal_add_account', 'portal_db_backup', 'portal_db_restore',
                    'portal_contacts', 'portal_audit', 'portal_drive', 'portal_drive_file',
                    'pubs_new', 'publications_new', 'apps_new', 'portal_research_edit'}
    if (endpoint in admin_routes or endpoint.startswith(('gallery_', 'portal_news', 'portal_mindmate'))) and not _is_admin():
        abort(403)
    # Restoring an arbitrary database replaces credentials and all project ACLs.
    if endpoint == 'portal_db_restore':
        abort(403)
    if endpoint == 'portal_db_backup' and _USE_SUPABASE:
        abort(404)
    values = request.view_args or {}
    project_id = values.get('project_id') or request.values.get('project_id')
    if project_id:
        _authorize_project(project_id, write=write, owner_only=endpoint.endswith('_delete') or 'delete_' in endpoint)
        for key, table in (('participant_id', 'project_participants'), ('measurement_id', 'measurements'),
                           ('m_id', 'measurements'), ('var_id', 'project_variables'), ('file_id', 'portal_files')):
            rid = values.get(key) or request.values.get(key)
            if rid and not sb('GET', table, params=f'?id=eq.{_q(rid)}&project_id=eq.{_q(project_id)}'):
                abort(404)
    if endpoint in ('portal_merge_preview', 'portal_merge_execute'):
        keys = ('project1_id', 'project2_id') if endpoint.endswith('preview') else ('p1_id', 'p2_id')
        for key in keys:
            _authorize_project(request.form.get(key), owner_only=True)
    if values.get('task_id'):
        rows = sb('GET', 'tasks', params=f"?id=eq.{_q(values['task_id'])}")
        if not rows:
            abort(404)
        row = rows[0]
        if row.get('project_id'):
            _authorize_project(row['project_id'], write=True, owner_only=endpoint.endswith('_delete'))
        elif row.get('created_by') != session['researcher']:
            abort(403)
    if request.is_json and write and not isinstance(request.get_json(silent=True), dict):
        abort(400)
    if endpoint in ('portal_task_new', 'portal_task_edit'):
        if request.form.get('status', 'todo') not in ('todo', 'doing', 'done'):
            abort(400)
        if request.form.get('priority', 'normal') not in ('low', 'normal', 'high'):
            abort(400)
        due = request.form.get('due_date', '')
        if due:
            try:
                datetime.strptime(due, '%Y-%m-%d')
            except ValueError:
                abort(400)
    if endpoint in ('portal_project_upload', 'portal_project_upload_sheets', 'portal_files_upload'):
        upload = request.files.get('file')
        if upload and upload.filename:
            ext = os.path.splitext(upload.filename)[1].lower()
            allowed = DOCUMENT_EXTENSIONS if endpoint == 'portal_files_upload' else {'.csv', '.xls', '.xlsx'}
            if ext not in allowed:
                abort(400)
            if ext in {'.xlsx', '.docx', '.pptx', '.hwpx'}:
                try:
                    validate_archive(upload.stream)
                except (ValueError, OSError, BadZipFile):
                    abort(400)

def _project_rows(table, params=''):
    """Query only accessible projects; unassigned rows are never implicitly public."""
    selected = request.args.get('project_id')
    projects = [{'id': selected}] if selected else _accessible_projects(session['researcher'])
    rows = []
    for project in projects:
        pid = project['id']
        _authorize_project(pid)
        # The caller's selected project filter is redundant, not a second filter.
        pairs = [(k, v) for k, v in parse_qsl(params.lstrip('?')) if k != 'project_id']
        from urllib.parse import urlencode
        query = '?' + urlencode(pairs + [('project_id', 'eq.' + pid)])
        rows.extend(sb('GET', table, params=query) or [])
    return rows

def _require_project_owner(project_id):
    """Returns (project_dict, None) if OK, (None, redirect_response) if unauthorized."""
    proj = sb("GET","projects",params=f"?id=eq.{_q(project_id)}")
    if not proj or not isinstance(proj,list):
        flash("프로젝트를 찾을 수 없어요.", "error")
        return None, redirect(url_for("portal"))
    owner = proj[0].get("researcher_email","")
    email = session.get("researcher","")
    # Allow owner OR collaborator with any role
    if owner != email:
        row = sb("GET", "project_collaborators",
                 params=f"?project_id=eq.{_q(project_id)}&researcher_email=eq.{_q(email)}&select=role")
        if not row:
            flash("이 프로젝트에 접근 권한이 없어요.", "error")
            return None, redirect(url_for("portal"))
    return proj[0], None

def _project_access_ok(project_id, email):
    """Same check as _require_project_owner but returns a bool (no flash/redirect) — for JSON endpoints."""
    proj = sb("GET","projects",params=f"?id=eq.{_q(project_id)}")
    if not proj or not isinstance(proj,list):
        return False
    if proj[0].get("researcher_email","") == email:
        return True
    row = sb("GET", "project_collaborators",
             params=f"?project_id=eq.{_q(project_id)}&researcher_email=eq.{_q(email)}&select=role")
    return bool(row)

def _accessible_projects(email):
    """Projects `email` owns or collaborates on, deduped, as [{'id':..,'name':..}, ...]."""
    projects = sb("GET", "projects", params=f"?researcher_email=eq.{_q(email)}&select=id,name") or []
    have = {p["id"] for p in projects}
    collab_rows = sb("GET", "project_collaborators", params=f"?researcher_email=eq.{_q(email)}&select=project_id") or []
    for row in collab_rows:
        pid = row.get("project_id")
        if pid and pid not in have:
            r = sb("GET", "projects", params=f"?id=eq.{_q(pid)}&select=id,name")
            if r:
                projects.append(r[0])
                have.add(pid)
    return projects

def _delete_owned_rows(table, ids, redirect_endpoint, **redirect_kwargs):
    """Delete app rows only as the project owner. Unassigned rows are protected."""
    email = session["researcher"]
    deleted, skipped = 0, 0
    for rid in ids:
        rows = sb("GET", table, params=f"?id=eq.{_q(rid)}&select=id,project_id")
        if not rows:
            continue
        pid = rows[0].get("project_id")
        if not pid or _project_role(pid) != 'owner':
            skipped += 1
            continue
        sb("DELETE", table, params=f"?id=eq.{_q(rid)}")
        deleted += 1
    if skipped:
        flash(f"{deleted}건 삭제됐습니다. ({skipped}건은 권한이 없어 건너뛰었어요.)")
    else:
        flash(f"{deleted}건 삭제됐습니다.")
    return redirect(url_for(redirect_endpoint, **redirect_kwargs))

# ─── Site-wide ───────────────────────────────────────────────
PROFESSOR = {
    "name":      "Yun-Jung Choi",
    "title":     "Professor",
    "bio":       "Professor Yun-Jung Choi is a nurse educator and researcher at the Red Cross College of Nursing, Chung-Ang University, Seoul, Korea.",
    "email":     "yunjungchoi@cau.ac.kr",
    "lab_phone": "+82-2-820-5986",
    "fax":       "02-824-7961",
    "address":   "Red Cross College of Nursing, Chung-Ang University, Seoul, South Korea",
    "photo":     "images/professor-placeholder.jpg",
}

RESEARCH_TOPICS = [
    {"key": "brainwave",   "title": "Brain Wave",              "summary": "Focused on Alpha waves to stabilise the mind, relieve stress, and improve learning efficiency.",         "image": "images/brain_wave.avif",  "gradient": "linear-gradient(135deg,#004d40 0%,#26a69a 100%)"},
    {"key": "simulation",  "title": "Simulation Education",    "summary": "Developing simulations using standardised patients and interactive PFA serious games.",                   "image": "images/simulation.avif",  "gradient": "linear-gradient(135deg,#1a237e 0%,#5c6bc0 100%)"},
    {"key": "binaural",    "title": "Binaural Beat",           "summary": "Developing binaural beats and modulating brainwaves to promote mental wellbeing.",                       "image": "images/binaural.avif",    "gradient": "linear-gradient(135deg,#4a148c 0%,#ab47bc 100%)"},
    {"key": "app",         "title": "Developing Applications", "summary": "Building evidence-based mobile apps for disaster survivors and healthcare workers.",                     "image": "images/app.avif",         "gradient": "linear-gradient(135deg,#0F6B6B 0%,#1ec0c0 100%)"},
]

TEAM = {
    "phd": [
        {"name": "Hae-In Namgung",  "interest": "Simulation education, intervention research, RCT, PTSD, Artificial intelligence (AI)", "image": "images/member-haein-placeholder.jpg"},
        {"name": "Joo-Young Jin",   "interest": "Nursing simulation, disaster nursing, psychological safety",                            "image": "images/member-jooyoung-placeholder.jpg"},
        {"name": "Ji-Young Yeon",   "interest": "",                                                                                       "image": "images/ji-young_yeon.jpg"},
    ],
    "ma": [
        {"name": "Chae-Young Lee",  "interest": "Anxiety, PTSD, depression, suicide, addiction",                                       "image": "images/member-chaeyoung-placeholder.jpg"},
    ],
    "intern": [
        {"name": "Hyun Park", "interest": "", "image": "images/member-placeholder.jpg"},
        {"name": "Mi-So Kim", "interest": "", "image": "images/member-placeholder.jpg"},
    ],
    "alumni": [
        {"name": "Run-Ju Choi",   "image": "images/run-ju_choi.avif",    "current_position": ""},
        {"name": "Eun-Jung Ko",   "image": "images/eunjung_ko.avif",     "current_position": ""},
        {"name": "Younjoo Um",    "image": "images/younjoo_um.avif",     "current_position": ""},
        {"name": "Dong-Hee Cho",  "image": "images/dong-hee_cho.avif",   "current_position": ""},
        {"name": "Hee-Won Song",  "image": "images/hee-won_song.avif",   "current_position": ""},
    ],
}

APPS = [
    {
        "key":       "pls",
        "icon":      "🫀",
        "gradient":  "linear-gradient(135deg,#00695c 0%,#26a69a 100%)",
        "name":      "PLS",
        "full_name": "Psychological Life Support",
        "label":     "Mobile PFA Application",
        "desc_kr":   "재난구호자를 위한 심리적인명구조술 PLS는\n재난상황에 대한 정보제공 및 심리적응급처치 기술 적용,\n재난 후 구호요원들의 마음회복을 돕기위한 앱입니다.",
        "desc_en":   "PLS (Psychological Life Skill) for disaster relief workers is providing information on disaster situations and application of psychological first aid techniques. It is a mobile app to help rescue workers recover their minds after a disaster.",
        "tags":      ["Disaster Workers", "Mental Health", "Psychological First Aid"],
        "screenshot": None,
        "content_img_kr": "images/pls_kr_content.avif",
        "content_img_en": "images/pls_en_content.avif",
        "dl_kr":  "https://m.onestore.co.kr/ko-kr/apps/appsDetail.omp?prodId=0000747597",
        "dl_en":  "https://m.onestore.co.kr/ko-kr/apps/appsDetail.omp?prodId=0000747600",
        "subsections": [],
    },
    {
        "key":       "tls",
        "icon":      "🌿",
        "gradient":  "linear-gradient(135deg,#1b5e20 0%,#66bb6a 100%)",
        "name":      "TLS",
        "full_name": "Training of Life Skills",
        "label":     "Mobile PFA Application",
        "desc_kr":   "재난경험자를 위한 마음치유기술 TLS (Training for Life Skills)는\n재난피해로 심리적 상처를 입은 분들의 심리회복을 돕기 위한 앱입니다.",
        "desc_en":   "TLS (Training for Life Skills) for disaster experienced people is an app to help the psychological recovery of those who have been psychologically wounded by disaster damage.",
        "tags":      ["Disaster Survivors", "Trauma Recovery", "Skills Training"],
        "screenshot": None,
        "content_img_kr": "images/tls_kr_content.avif",
        "content_img_en": "images/tls_en_content.avif",
        "dl_kr":  "https://m.onestore.co.kr/ko-kr/apps/appsDetail.omp?prodId=0000747598",
        "dl_en":  "https://m.onestore.co.kr/ko-kr/apps/appsDetail.omp?prodId=0000747605",
        "subsections": [],
    },
    {
        "key":       "mind_therapy",
        "icon":      "🧠",
        "gradient":  "linear-gradient(135deg,#311b92 0%,#9c27b0 100%)",
        "name":      "Mind Therapy",
        "full_name": "Neurofeedback Stress Management",
        "label":     "",
        "desc_kr":   "",
        "desc_en":   "The 'Mind Therapy' application is designed to help people recover from traumatic events. Developed by applying EEG and heart rate variability technology, it can measure left-right brain balance, brain activity, and stress recovery through real-time EEG/heart rate variability tests.",
        "tags":      ["Neurofeedback", "Binaural Beat", "Stress & PTSD"],
        "screenshot": "images/app_screenshot3.png",
        "content_img_kr": None,
        "content_img_en": None,
        "dl_kr":  None,
        "dl_en":  None,
        "subsections": [
            {
                "title": "Brain Therapy",
                "image": "images/mt_brain_therapy.avif",
                "desc":  "We offer a meditation program with neurofeedback and binaural beats.",
                "links": [
                    {"text": "Neurofeedback",  "url": None},
                    {"text": "Binaural beats", "url": None},
                ],
            },
            {
                "title": "Autonomic Nerve Therapy",
                "image": "images/mt_autonomic.avif",
                "desc":  "The deep breathing and butterfly hug programs are prepared for your autonomic nervous system stabilization. Several options for deep breathing are provided — choose whatever you feel comfortable with. A video guide for the butterfly hug is also included.",
                "links": [],
            },
            {
                "title": "Brain Game",
                "image": "images/mt_brain_game.avif",
                "desc":  "We visually represent the response when alpha waves are enhanced to help train the brain waves. The balloon's face will change along with your alpha waves. Enhance alpha waves through an exciting game!",
                "links": [],
            },
        ],
    },
    {
        "key":       "wardy",
        "icon":      "🩺",
        "gradient":  "linear-gradient(135deg,#2563eb 0%,#7dd3fc 100%)",
        "name":      "Wardy (TSL)",
        "full_name": "Nursing Clinical Simulation",
        "label":     "Nursing Education Application",
        "desc_kr":   "Wardy는 신규 간호사와 간호학생이 가상 임상 상황에서 판단과 처치 과정을 반복 연습할 수 있도록 돕는 간호 시뮬레이션 교육 앱입니다.",
        "desc_en":   "Wardy is a nursing clinical-simulation education app that helps nursing students and newly employed nurses practise clinical judgment and care workflows in fictional scenarios.",
        "tags":      ["Nursing Education", "Clinical Simulation", "Patient Safety"],
        "screenshot": None,
        "content_img_kr": None,
        "content_img_en": None,
        "dl_kr":  None,
        "dl_en":  None,
        "privacy_url": "/privacy/wardy",
        "subsections": [],
    },
    {
        "key":       "neurobreeze",
        "icon":      "🧠",
        "gradient":  "linear-gradient(135deg,#2fbf8b 0%,#8de7a1 100%)",
        "name":      "NeuroBreeze",
        "full_name": "Neurofeedback-based Meditation",
        "label":     "Digital Mental Health Application",
        "desc_kr":   "NeuroBreeze는 뉴로피드백 기반 명상과 호흡·바디스캔 프로그램을 통해 일상 속 마음 돌봄을 지원하는 디지털 멘탈 헬스 앱입니다.",
        "desc_en":   "NeuroBreeze is a digital mental health application that supports everyday self-care through neurofeedback-based meditation, breathing, and body-scan programs.",
        "tags":      ["Neurofeedback", "Meditation", "Self-care"],
        "screenshot": "images/neurobreeze-icon.jpg",
        "content_img_kr": None,
        "content_img_en": None,
        "dl_kr":  None,
        "dl_en":  None,
        "privacy_url": "/privacy/neurobreeze",
        "subsections": [],
    },
    {
        "key":       "mindmate",
        "icon":      "💚",
        "gradient":  "linear-gradient(135deg,#e96d91 0%,#9dc647 100%)",
        "name":      "Mind Mate(TSL)",
        "full_name": "New Nurse Stress Management",
        "label":     "Digital Mental Health Application",
        "desc_kr":   "Mind Mate는 신규간호사가 근무·수면·스트레스 상태를 기록하고, 명상과 바이노럴 비트 등 마음돌봄 활동을 통해 일상 속 스트레스 관리를 돕는 앱입니다.",
        "desc_en":   "Mind Mate is a stress-management application for newly employed nurses, supporting daily work, sleep, and stress records alongside meditation and binaural-beat activities.",
        "tags":      ["Nurse Well-being", "Stress Management", "Self-care"],
        "screenshot": None,
        "content_img_kr": None,
        "content_img_en": None,
        "dl_kr":  None,
        "dl_en":  None,
        "privacy_url": "/privacy/mindmate",
        "subsections": [],
    },
]

# ─── Publications ────────────────────────────────────────────
ARTICLES = [
    # 2026
    {"year": 2026, "title": "Efficacy of a Mobile-Based Intervention for Newly Graduated Nurses: A Randomized Controlled Trial", "authors": "Choi, Y. J., Song, H. W., Namgung, H. I. & Lee, C. Y.", "journal": "Digital Health, 12", "doi": "https://doi.org/10.1177/20552076261450524"},
    {"year": 2026, "title": "Associations of Suicidal Behaviors with Physical Activity Types and Psychosocial Factors among Korean Adolescents: A Secondary Data Analysis", "authors": "Choi, Y. J.", "journal": "International Journal of Mental Health Promotion", "doi": None},
    # 2025
    {"year": 2025, "title": "Blooming as a New Nurse amid Trials and Uncertainty: A Qualitative Content Analysis", "authors": "Choi, Y. J., Namgung, H. I., Song, H. & Lee, N. R.", "journal": "Journal of Nursing Management, 5401225", "doi": "https://doi.org/10.1155/jonm/5401225"},
    {"year": 2025, "title": "Changes in citizens' anxiety, depression, and PTSD after the Seoul Halloween crowd crush in 2022", "authors": "Choi, Y. J. & Song, H.", "journal": "Scientific Reports, 15, 30299", "doi": "https://doi.org/10.1038/s41598-025-15634-0"},
    {"year": 2025, "title": "Using a mobile app comprising neurofeedback-based meditation and binaural beat music to treat PTSD symptoms: A qualitative analysis", "authors": "Choi, Y. J. & Lee, N. R.", "journal": "Digital Health, 11", "doi": "https://doi.org/10.1177/20552076241311053"},
    # 2024
    {"year": 2024, "title": "Feasibility of a Mobile App for Traumatic Stress Management Using Neurofeedback-based Mediation and Binaural Beat Music: A Pilot Randomized Controlled Trial", "authors": "Choi, Y. J., Cho, D. H. & Lee, N. R.", "journal": "Digital Health, 10", "doi": "https://doi.org/10.1177/20552076241308986"},
    {"year": 2024, "title": "Mental Health Status and Related Factors of Citizens 6 Months after Mass Death and Injury Due to Crowd Crush Incident: Focused on the Itaewon Disaster in 2022", "authors": "Choi, Y. J., Song, H., Namgung, H. I. & Lee, N. R.", "journal": "Disaster Medicine and Public Health Preparedness, 19, e11", "doi": "https://doi.org/10.1017/dmp.2024.342"},
    {"year": 2024, "title": "Mediating effect of bicultural acceptability among multicultural adolescents in the relationship between depression and life satisfaction", "authors": "Choi, Y. J. & Um, Y. J.", "journal": "Archives of Psychiatric Nursing, 53", "doi": "https://doi.org/10.1016/j.apnu.2024.10.003"},
    {"year": 2024, "title": "Still in there—citizens' well-being and PTSD after Seoul Halloween crowd crush in Korea: A cross-sectional study", "authors": "Choi, Y. J. & Namgung, H. I.", "journal": "Scientific Reports, 14, 20537", "doi": "https://doi.org/10.1038/s41598-024-71631-9"},
    {"year": 2024, "title": "Development and Effect of an Interactive Simulated Education Program for Psychological First Aid: A Randomized Controlled Trial", "authors": "Choi, E. J. & Choi, Y. J.", "journal": "Journal of Nursing Management, 8806047", "doi": "https://doi.org/10.1155/2024/8806047"},
    {"year": 2024, "title": "Experiences of Family Caregiver of Older People with Dementia in Korea during the COVID-19 Pandemic: A Qualitative Analysis", "authors": "Joh, E. S. & Choi, Y. J.", "journal": "Journal of Gerontological Nursing, 50(10)", "doi": "https://doi.org/10.3928/00989134-20240916-01"},
    {"year": 2024, "title": "The Association between Fear of COVID-19, Obsession with COVID-19, and Post Traumatic Stress Disorder in Korean Emergency Rescue Firefighters", "authors": "Choi, Y. J. & Song, H.", "journal": "International Journal of Mental Health Promotion, 26(6)", "doi": "https://doi.org/10.32604/ijmhp.2024.050824"},
    {"year": 2024, "title": "Citizens' Mental Health Issues and Psychological Trauma Experience due to a Crowd-Crush Disaster in Korea", "authors": "Choi, Y. J., Kwak, J. W. & Namgung, H. I.", "journal": "International Journal of Mental Health Promotion, 26(6)", "doi": "https://doi.org/10.32604/ijmhp.2024.050458"},
    {"year": 2024, "title": "Efficacy of a virtual nursing simulation-based education to provide psychological support for patients affected by infectious disease disasters: a randomized controlled trial", "authors": "Ko, E. & Choi, Y. J.", "journal": "BMC Nursing, 23, 230", "doi": "https://doi.org/10.1186/s12912-024-01901-4"},
    {"year": 2024, "title": "Effectiveness of a fire disaster PFA simulation game: A single-blinded trial", "authors": "Choi, Y. J. & Song, H.", "journal": "Disaster Medicine and Public Health Preparedness, 18, e64", "doi": "https://doi.org/10.1017/dmp.2024.47"},
    {"year": 2024, "title": "The Development, Implementation, and Evaluation of a Geriatric Disaster Nursing Simulation Intervention With Supportive Debriefing", "authors": "Jin, J. Y. & Choi, Y. J.", "journal": "Simulation in Healthcare, 19(5)", "doi": "https://doi.org/10.1097/SIH.0000000000000780"},
    # 2023
    {"year": 2023, "title": "What Influenced Frontline Nurses' Mental Health During the Early Phase of the Covid-19 Pandemic", "authors": "Choi, Y. J., Um, Y. J. & Cho, D. H.", "journal": "International Nursing Review, 70(4)", "doi": "https://doi.org/10.1111/inr.12895"},
    {"year": 2023, "title": "Development of a Multiple-Patient Simulation and its Effectiveness in Clinical Judgment and Practice Readiness: A Randomized Controlled Trial", "authors": "Namgung, H. I., Choi, Y. J. & Kang, J. S.", "journal": "Clinical Simulation in Nursing, 83, 101448", "doi": "https://doi.org/10.1016/j.ecns.2023.101448"},
    {"year": 2023, "title": "Effects of a web-based education for community mental health case managers on physical healthcare for clients with severe mental illness", "authors": "Lee, J. & Choi, Y. J.", "journal": "AIMS Public Health, 10(3)", "doi": "https://doi.org/10.3934/publichealth.2023045"},
    {"year": 2023, "title": "Neurofeedback Effect on Symptoms of Posttraumatic Stress Disorder: A Systematic Review and Meta-Analysis", "authors": "Choi, Y. J., Choi, E. J. & Ko, E.", "journal": "Applied Psychophysiology and Biofeedback, 48", "doi": "https://doi.org/10.1007/s10484-023-09593-3"},
    {"year": 2023, "title": "Inpatient meditation for alcohol use disorder reduces mood dysregulation: A pilot study", "authors": "Choi, Y. J., Cho, D. H. & Lee, N. R.", "journal": "Social Behavior and Personality, 51(10)", "doi": "https://doi.org/10.2224/sbp.12451"},
    {"year": 2023, "title": "Professional quality of life, resilience, posttraumatic stress and leisure activity among intensive care unit nurses", "authors": "Shin, N. & Choi, Y. J.", "journal": "International Nursing Review", "doi": "https://doi.org/10.1111/inr.12850"},
    {"year": 2023, "title": "Effects of a mental health nursing simulation for general ward nurses: A pilot study", "authors": "Lee, M. Y. & Choi, Y. J.", "journal": "Nursing Open, 10(5)", "doi": None},
    {"year": 2023, "title": "Topic Models to Analyze Disaster-Related Newspaper Articles: Focusing on COVID-19", "authors": "Choi, Y. J. & Um, Y. J.", "journal": "International Journal of Mental Health Promotion, 25(3)", "doi": None},
    {"year": 2023, "title": "Disaster Healthcare Workers' Experience of Using the Psychological First Aid Mobile App During Disaster Simulation Training", "authors": "Choi, Y. J., Jung, H. S., Choi, E. J. & Ko, E.", "journal": "Disaster Medicine and Public Health Preparedness, 17, e55", "doi": None},
    {"year": 2023, "title": "The early emotional responses and central issues of people in the epicenter of the COVID-19 pandemic: An analysis from twitter text mining", "authors": "Choi, E. J. & Choi, Y. J.", "journal": "International Journal of Mental Health Promotion", "doi": None},
    # 2022
    {"year": 2022, "title": "Student nurse experiences in public healthcare clinical practice during the COVID-19 pandemic: A qualitative study", "authors": "Choi, Y. J. & Um, Y. J.", "journal": "Nurse Education Today, 119, 105586", "doi": None},
    {"year": 2022, "title": "The effects of a home-visit nursing simulation for older people with dementia on nursing students' communication skills, self-efficacy, and critical thinking propensity", "authors": "Choi, Y. J. & Um, Y. J.", "journal": "Nurse Education Today, 119, 105564", "doi": None},
    {"year": 2022, "title": "Standardized patient experiences study on clinical performance evaluation of nursing college students' ability: A qualitative study", "authors": "Choi, Y. J., Won, M. R. & Yoo, S. Y.", "journal": "Nurse Education Today, 118, 105437", "doi": None},
    {"year": 2022, "title": "Effects of Nursing Care Using Binaural Beat Music on Anxiety, Pain, and Vital Signs in Surgery Patients", "authors": "Jang, Y. & Choi, Y. J.", "journal": "Journal of PeriAnesthesia Nursing, 37(6)", "doi": None},
    {"year": 2022, "title": "A grounded theory on school nursing experiences with major pandemic diseases", "authors": "Um, Y. J. & Choi, Y. J.", "journal": "INQUIRY: The Journal of Health Care Organization, 59", "doi": None},
    {"year": 2022, "title": "A simulation-based nursing education of psychological first aid for adolescents exposed to hazardous chemical disasters", "authors": "Kim, H. W. & Choi, Y. J.", "journal": "BMC Medical Education, 22(1)", "doi": None},
    {"year": 2022, "title": "Simulation-based education for nurses in caring for the psychological well-being of survivors of disaster", "authors": "Yun, S. M. & Choi, Y. J.", "journal": "The Journal of Continuing Education in Nursing, 53(3)", "doi": None},
    {"year": 2022, "title": "Clinical Nurses' Continuing Education Needs in Acute Burn Care", "authors": "Oh, D. & Choi, Y. J.", "journal": "The Journal of Continuing Education in Nursing, 53(2)", "doi": None},
    {"year": 2022, "title": "Efficacy of a Community-Based Trauma Recovery Program after a Fire Disaster", "authors": "Choi, Y. J., Won, M. R. & Cho, D. H.", "journal": "International Journal of Mental Health Promotion, 24", "doi": None},
    {"year": 2022, "title": "The effect of a simulated fire disaster psychological first aid training program on the self-efficacy, competence, and knowledge of mental health practitioners", "authors": "Park, J. S. & Choi, Y. J.", "journal": "Disaster Medicine and Public Health Preparedness, 16(1)", "doi": None},
    # 2021
    {"year": 2021, "title": "COVID-19 and risk factors of anxiety and depression in South Korea", "authors": "Hyun, J. et al.", "journal": "Psychiatry Investigation, 18(9)", "doi": None},
    {"year": 2021, "title": "Explicit and implicit attitudes toward people with COVID-19: Need for community mental health services", "authors": "Choi, Y. J. & Cho, D. H.", "journal": "Social Behavior and Personality, 49(11)", "doi": None},
    {"year": 2021, "title": "Managing traumatic stress using a mental health care mobile app: A pilot study", "authors": "Choi, Y. J., Ko, E. J., Choi, E. J. & Um, Y. J.", "journal": "International Journal of Mental Health Promotion, 23", "doi": None},
    {"year": 2021, "title": "The mediating effect of life satisfaction and the moderated mediating effect of social support on the relationship between depression and suicidal behavior among older adults", "authors": "Won, M. R., Choi, E. J., Ko, E., Um, Y. J. & Choi, Y. J.", "journal": "International Journal of Geriatric Psychiatry, 36(11)", "doi": None},
    {"year": 2021, "title": "Effects of Stress, Depression, and Problem Drinking on Suicidal Ideation among Korean Workers", "authors": "Choi, Y. J., Won, M. R. & Um, Y. J.", "journal": "International Journal of Mental Health Promotion, 23", "doi": None},
    {"year": 2021, "title": "Nursing students' extracurricular activity experiences of suicide prevention volunteering: A qualitative study", "authors": "Yoo, S. Y., Choi, E. J. & Choi, Y. J.", "journal": "Nurse Education Today, 102", "doi": None},
    {"year": 2021, "title": "Effects of a psychological first aid simulated training for pregnant flood victims on disaster relief worker's knowledge, competence, and self-efficacy", "authors": "Kang, J. Y. & Choi, Y. J.", "journal": "Applied Nursing Research, 57", "doi": None},
    # 2020
    {"year": 2020, "title": "The effect of employment status on people with a mental disability and on daily life satisfaction", "authors": "Um, Y. J. & Choi, Y. J.", "journal": "Current Psychology", "doi": None},
    {"year": 2020, "title": "Challenges and growth as a mental health professional from volunteering experiences in the community gambling awareness campaign", "authors": "Yoo, S. Y., Choi, Y. J. & Um, Y. J.", "journal": "International Journal of Mental Health Promotion, 22(2)", "doi": None},
    {"year": 2020, "title": "Debriefing model for psychological safety in nursing simulations: a qualitative study", "authors": "Ko, E. & Choi, Y. J.", "journal": "International Journal of Environmental Research and Public Health, 17(8)", "doi": None},
    {"year": 2020, "title": "The Mediating Role of Job Satisfaction in the Relationship between Disaster Relief Workers' Perception of Survivors' Rights and Their Performance of Human Rights Advocacy", "authors": "Choi, Y. J. & Ko, E.", "journal": "International Journal of Mental Health Promotion, 22", "doi": None},
    {"year": 2020, "title": "Bilingual gatekeepers' experiences of immigrant women's acculturative stress and mental health improvement in Korea: A qualitative analysis", "authors": "Choi, Y. J.", "journal": "Social Behavior and Personality, 48(9)", "doi": None},
    {"year": 2020, "title": "Psychological first-aid experiences of disaster health care workers: a qualitative analysis", "authors": "Choi, Y. J.", "journal": "Disaster Medicine and Public Health Preparedness, 14(4)", "doi": None},
    # 2019
    {"year": 2019, "title": "Relationships between smartphone dependency and aggression among middle school students", "authors": "Um, Y. J., Choi, Y. J. & Yoo, S. Y.", "journal": "International Journal of Environmental Research and Public Health, 16(19)", "doi": None},
    {"year": 2019, "title": "Nurses' Positive Experiences in Caring for Older Adults With Dementia: A Qualitative Analysis", "authors": "Choi, Y. J. & Choi, H. B.", "journal": "Journal of Gerontological Nursing, 45(1)", "doi": None},
    {"year": 2019, "title": "Nursing competency and educational needs for clinical practice of Korean nurses", "authors": "Kim, S. O. & Choi, Y. J.", "journal": "Nurse Education in Practice, 34", "doi": None},
    # 2018
    {"year": 2018, "title": "Relationships of substance use and sexual behavior of female junior high school students in Korea", "authors": "Lee, G. Y., Song, S. H. & Choi, Y. J.", "journal": "Journal of Child & Adolescent Substance Abuse, 27(5-6)", "doi": None},
    {"year": 2018, "title": "Associations among elder abuse, depression and PTSD in South Korean older adults", "authors": "Choi, Y. J., O'Donnell, M., Choi, H. B., Jung, H. S. & Cowlishaw, S.", "journal": "International Journal of Environmental Research and Public Health, 15(9)", "doi": None},
    {"year": 2018, "title": "Three-dimensional needs of standardized patients in nursing simulations and collaboration strategies: A qualitative analysis", "authors": "Jin, H. R. & Choi, Y. J.", "journal": "Nurse Education Today, 68", "doi": None},
    {"year": 2018, "title": "Disaster reintegration model: a qualitative analysis on developing Korean disaster mental health support model", "authors": "Choi, Y. J., Choi, H. B. & O'Donnell, M.", "journal": "International Journal of Environmental Research and Public Health, 15(2)", "doi": None},
    {"year": 2018, "title": "The value of psychosocial group activity in nursing education: A qualitative analysis", "authors": "Choi, Y. J.", "journal": "Nurse Education Today, 64", "doi": None},
    # 2017
    {"year": 2017, "title": "Effects of a program to improve mental health literacy for married immigrant women in Korea", "authors": "Choi, Y. J.", "journal": "Archives of Psychiatric Nursing, 31(4)", "doi": None},
    {"year": 2017, "title": "Analysis of Korean adolescents' sexual experience and substance use", "authors": "Lee, G. Y. & Choi, Y. J.", "journal": "Social Behavior and Personality, 45(5)", "doi": None},
    {"year": 2017, "title": "Family stress and coping from hospitalization of clients with severe alcohol use disorder in Korea", "authors": "Park, G. H. & Choi, Y. J.", "journal": "Journal of Addictions Nursing, 28(1)", "doi": None},
    {"year": 2017, "title": "Factors associated with perceived depression of Korean adults: secondary data from the Korean Community Health Survey", "authors": "Won, M. R., Ahn, M. S. & Choi, Y. J.", "journal": "Community Mental Health Journal, 53", "doi": None},
    {"year": 2017, "title": "Undergraduate nursing student mentors' experiences of peer mentoring in Korea: A qualitative analysis", "authors": "Won, M. R. & Choi, Y. J.", "journal": "Nurse Education Today, 51", "doi": None},
    # 2016
    {"year": 2016, "title": "Immigrant women's acculturation stress and coping strategies in Korea: A qualitative analysis", "authors": "Choi, Y. J.", "journal": "International Journal of Intercultural Relations, 55", "doi": None},
    {"year": 2016, "title": "Evaluation of a program on self-esteem and ego-identity for Korean nursing students", "authors": "Choi, Y. J.", "journal": "Nursing & Health Sciences, 18(3)", "doi": None},
    {"year": 2016, "title": "Associations among acculturation stress, mental health literacy, and mental health of married immigrant women in Korea", "authors": "Choi, Y. J. & Park, G. H.", "journal": "International Journal of Mental Health Promotion, 18(4)", "doi": None},
    {"year": 2016, "title": "Undergraduate students' experiences of an integrated psychiatric nursing curriculum in Korea", "authors": "Choi, Y. J.", "journal": "Issues in Mental Health Nursing, 37(8)", "doi": None},
    {"year": 2016, "title": "Effects of an obesity management mentoring program for Korean children", "authors": "Lee, G. Y. & Choi, Y. J.", "journal": "Applied Nursing Research, 31", "doi": None},
    {"year": 2016, "title": "Mental health problems and acculturative issues among married immigrant women in Korea: A qualitative study", "authors": "Choi, Y. J.", "journal": "Women & Health, 56(6)", "doi": None},
    # 2015
    {"year": 2015, "title": "Association of school, family, and mental health characteristics with suicidal ideation among Korean adolescents", "authors": "Lee, G. Y. & Choi, Y. J.", "journal": "Research in Nursing & Health, 38(4)", "doi": None},
    {"year": 2015, "title": "Mobile phone overuse among elementary school students in Korea: Factors associated with mobile phone use as a behavior addiction", "authors": "Kim, R., Lee, K. J. & Choi, Y. J.", "journal": "Journal of Addictions Nursing, 26(2)", "doi": None},
    {"year": 2015, "title": "The impact of gender, culture, and society on Korean women's mental health", "authors": "Choi, Y. J.", "journal": "Social Behavior and Personality, 43(4)", "doi": None},
    {"year": 2015, "title": "Efficacy of adjunctive treatments added to olanzapine or clozapine for weight control in patients with schizophrenia: a systematic review and meta-analysis", "authors": "Choi, Y. J.", "journal": "The Scientific World Journal, 2015", "doi": None},
    # 2014 and earlier
    {"year": 2014, "title": "Phases of shopping addiction evidenced by experiences of compulsive buyers", "authors": "Sohn, S. H. & Choi, Y. J.", "journal": "International Journal of Mental Health and Addiction, 12", "doi": None},
    {"year": 2013, "title": "Are Koreans prepared for the rapid increase of the single-household elderly? Life satisfaction and depression of the single-household elderly in Korea", "authors": "Won, M. R. & Choi, Y. J.", "journal": "The Scientific World Journal, 2013", "doi": None},
    {"year": 2013, "title": "Standardized patients for Korean psychiatric nursing student simulations", "authors": "Choi, Y. J.", "journal": "Clinical Simulation in Nursing, 9(9)", "doi": None},
    {"year": 2013, "title": "A pilot study on effects of a group program using recreational therapy to improve interpersonal relationships for undergraduate nursing students", "authors": "Choi, Y. J. & Won, M. R.", "journal": "Archives of Psychiatric Nursing, 27(1)", "doi": None},
    {"year": 2012, "title": "A model of compulsive buying: Dysfunctional beliefs and self-regulation of compulsive buyers", "authors": "Sohn, S. H. & Choi, Y. J.", "journal": "Social Behavior and Personality, 40(10)", "doi": None},
    {"year": 2012, "title": "Exploring experiences of psychiatric nursing simulations using standardized patients for undergraduate students", "authors": "Choi, Y. J.", "journal": "Asian Nursing Research, 6(3)", "doi": None},
    {"year": 2012, "title": "Effects of an emotion management nursing program for patients with schizophrenia", "authors": "Won, M. R., Lee, K. J., Lee, J. H. & Choi, Y. J.", "journal": "Archives of Psychiatric Nursing, 26(1)", "doi": None},
    {"year": 2010, "title": "The effect of an anger management program for family members of patients with alcohol use disorders", "authors": "Son, J. Y. & Choi, Y. J.", "journal": "Archives of Psychiatric Nursing, 24(1)", "doi": None},
    {"year": 2009, "title": "Efficacy of treatments for patients with obsessive-compulsive disorder: A systematic review", "authors": "Choi, Y. J.", "journal": "Journal of the American Academy of Nurse Practitioners, 21(4)", "doi": None},
    {"year": 2008, "title": "Experiences and challenges of informal caregiving for Korean immigrants", "authors": "Han, H. R., Choi, Y. J., Kim, M. T., Lee, J. E. & Kim, K. B.", "journal": "Journal of Advanced Nursing, 63(5)", "doi": None},
    {"year": 2007, "title": "Evidence-based nursing: effects of a structured nursing program for the health promotion of Korean women with Hwa-Byung", "authors": "Choi, Y. J. & Lee, K. J.", "journal": "Archives of Psychiatric Nursing, 21(1)", "doi": None},
]

COPYRIGHTS = [
    "재난경험자를 위한 마음 치유 기술",
    "재난구호자를 위한 심리적 인명구조술",
    "Psychological Recovery Skills for Disaster Survivors",
    "Psychological Life Support for Disaster Relief Workers",
    "기능성 게임 기반 심리적 응급처치 교육 프로그램 — 지진",
    "기능성 게임 기반 심리적 응급처치 교육 프로그램 — 화재",
    "기능성 게임 기반 심리적 응급처치 교육 프로그램 — 풍수해",
    "기능성 게임 기반 심리적 응급처치 교육 프로그램 — 감염병",
]

# ─── Homepage marketing content (upsunday-style sections) ─────
# Recognition / impact figures — derived from real lab output.
RECOGNITION = [
    {"metric": "80+", "label": "Peer-reviewed Publications", "note": "SSCI / SCIE journals since 2007"},
    {"metric": "8",   "label": "Registered Software Copyrights", "note": "PFA serious games & mobile apps"},
    {"metric": "3",   "label": "Mental-Health Apps Released", "note": "PLS · TLS · Mind Therapy"},
    {"metric": "10+", "label": "Years of Funded Research", "note": "Disaster trauma & digital health"},
]

# NOTE: Placeholder voices — replace `quote`/`role` with real, attributable
# testimonials before relying on them. Attributions are role-based on purpose
# (no invented individuals).
TESTIMONIALS = [
    {"quote": "The PFA simulation training changed how I approach survivors in the field — I felt calmer and more prepared during a real deployment.",
     "role": "Disaster Relief Worker", "context": "PLS / PFA training participant"},
    {"quote": "Mind Therapy's neurofeedback meditation became part of my daily routine for managing post-traumatic stress.",
     "role": "Trauma Recovery Program Participant", "context": "Mind Therapy user"},
    {"quote": "Standardized-patient simulations filled the gap that lectures never could in psychiatric nursing practice.",
     "role": "Nursing Student", "context": "Simulation education cohort"},
    {"quote": "The web-based case-manager education translated directly into better physical-health support for our clients.",
     "role": "Community Mental Health Case Manager", "context": "Continuing-education program"},
]

# Partner & publishing venues — text wordmarks (no logo assets required).
PARTNERS = [
    "Chung-Ang University", "Red Cross College of Nursing", "Digital Health",
    "Scientific Reports", "BMC Nursing", "Disaster Medicine & Public Health Preparedness",
    "Applied Psychophysiology and Biofeedback", "Simulation in Healthcare",
    "Journal of Nursing Management", "International Nursing Review",
]

# Rotating keyword band.
KEYWORDS = [
    "Disaster Trauma", "Neurofeedback", "Psychological First Aid", "Simulation Education",
    "Binaural Beats", "Digital Mental Health", "PTSD Recovery", "Community Mental Health",
]


def _articles_by_year():
    result = OrderedDict()
    all_pubs = ARTICLES + load_extra_pubs()
    for pub in sorted(all_pubs, key=lambda x: -x["year"]):
        result.setdefault(pub["year"], []).append(pub)
    return result

# ─── Public routes ───────────────────────────────────────────
@app.route("/")
def home():
    recent = sorted(ARTICLES, key=lambda x: -x["year"])[:5]
    news_list = sb("GET","news",params="?published=eq.1&order=created_at.desc") or []
    # Recent gallery events (with a cover photo) power the blog-style cards.
    lab_events = []
    try:
        for ev in load_events():
            photos = event_photos(ev['key'])
            ev['cover_photo'] = ev.get('cover') or (photos[0] if photos else None)
            if ev['cover_photo']:
                lab_events.append(ev)
            if len(lab_events) >= 3:
                break
    except Exception:
        lab_events = []
    return render_template("home.html", research_topics=RESEARCH_TOPICS, recent_pubs=recent, news=news_list[:5],
                           professor=PROFESSOR, team=TEAM, apps=APPS,
                           recognition=RECOGNITION, testimonials=TESTIMONIALS,
                           partners=PARTNERS, keywords=KEYWORDS, lab_events=lab_events)

@app.route("/research")
def research():
    extras = {}
    try:
        rows = sb("GET", "research_topics_extra", params="") or []
        for row in (rows if isinstance(rows, list) else []):
            extras[row['key']] = row
    except: pass
    merged = []
    for r in RESEARCH_TOPICS:
        t = dict(r)
        if t['key'] in extras:
            ex = extras[t['key']]
            if ex.get('summary'): t['summary'] = ex['summary']
            if ex.get('detail'):  t['detail']  = ex['detail']
        merged.append(t)
    return render_template("research.html", research_topics=merged, is_researcher='researcher' in session)

@app.route("/team")
def team():
    return render_template("team.html", professor=PROFESSOR, team=TEAM)

@app.route("/publications")
def publications():
    by_year = _articles_by_year()
    return render_template("publications.html", publications_by_year=by_year,
                           pub_years=list(by_year.keys()), copyrights=COPYRIGHTS,
                           current_year=datetime.now().year)

@app.route("/publications/new", methods=["POST"])
@login_required
def publications_new():
    title = request.form.get('title','').strip()
    if not title: return redirect(url_for('publications'))
    try:
        year = int(request.form.get('year', datetime.now().year))
    except ValueError:
        year = datetime.now().year
    doi_raw = request.form.get('doi','').strip()
    entry = {
        'year': year,
        'title': title,
        'authors': request.form.get('authors','').strip(),
        'journal': request.form.get('journal','').strip(),
        'doi': doi_raw if doi_raw else None,
    }
    extra = load_extra_pubs()
    extra.append(entry)
    save_extra_pubs(extra)
    return redirect(url_for('publications'))

@app.route("/apps")
def apps():
    all_apps = APPS + load_extra_apps()
    return render_template("apps.html", apps=all_apps)

@app.route("/apps/new", methods=["POST"])
@login_required
def apps_new():
    name = request.form.get('name','').strip()
    if not name: return redirect(url_for('apps'))
    key = re.sub(r'[^a-z0-9_]','', name.lower().replace(' ','_'))
    if not key: key = 'app'
    extra = load_extra_apps()
    taken = {a['key'] for a in APPS} | {a['key'] for a in extra}
    base, n = key, 2
    while key in taken: key = f"{base}_{n}"; n += 1
    color_map = {
        'teal':   'linear-gradient(135deg,#0F6B6B 0%,#1ec0c0 100%)',
        'blue':   'linear-gradient(135deg,#1a237e 0%,#5c6bc0 100%)',
        'purple': 'linear-gradient(135deg,#4a148c 0%,#9c27b0 100%)',
        'green':  'linear-gradient(135deg,#1b5e20 0%,#66bb6a 100%)',
        'orange': 'linear-gradient(135deg,#e65100 0%,#ff9800 100%)',
    }
    gradient = color_map.get(request.form.get('color','teal'), color_map['teal'])
    app_entry = {'key':key,'icon':request.form.get('icon','📱'),'gradient':gradient,
        'name':name,'full_name':request.form.get('full_name',''),'label':request.form.get('label',''),
        'desc_kr':'','desc_en':request.form.get('desc_en',''),'tags':[],'screenshot':None,
        'content_img_kr':None,'content_img_en':None,'dl_kr':None,'dl_en':None,'subsections':[],
        'created_at':datetime.now().isoformat()[:10]}
    extra.append(app_entry)
    save_extra_apps(extra)
    return redirect(url_for('app_detail', key=key))

@app.route("/apps/<key>")
def app_detail(key):
    all_apps = APPS + load_extra_apps()
    app_data = next((a for a in all_apps if a.get('key')==key), None)
    if not app_data: return redirect(url_for('apps'))
    return render_template("app_detail.html", app=app_data)

@app.route("/privacy/wardy")
def wardy_privacy():
    return render_template("wardy_privacy.html")

@app.route("/privacy/neurobreeze")
def neurobreeze_privacy():
    return render_template("neurobreeze_privacy.html")

@app.route("/privacy/mindmate")
def mindmate_privacy():
    return render_template("mindmate_privacy.html")

@app.route("/contact", methods=["GET","POST"])
@limiter.limit('5 per minute', methods=['POST'])
def contact():
    success = False
    if request.method == "POST":
        name    = request.form.get("name","").strip()
        email   = request.form.get("email","").strip()
        subject = request.form.get("subject","").strip()
        message = request.form.get("message","").strip()
        if len(name) > 200 or len(email) > 254 or len(subject) > 500 or len(message) > 10000:
            abort(400)
        if message:
            saved = sb("POST", "contact_messages", data={
                "name": name, "email": email, "subject": subject, "message": message
            })
            if not saved:
                flash("문의를 저장하지 못했습니다. 잠시 후 다시 시도해주세요.", "error")
                return render_template("contact.html", success=False), 503
            success = True
        else:
            flash("메시지를 입력해주세요.", "error")
    return render_template("contact.html", success=success)

@app.route("/gallery")
def gallery():
    events = load_events()
    for ev in events:
        photos = event_photos(ev['key'])
        ev['photo_count'] = len(photos)
        ev['cover_photo'] = ev.get('cover') or (photos[0] if photos else None)
    return render_template("gallery.html", events=events)

@app.route("/gallery/new", methods=["POST"])
@login_required
def gallery_new():
    title = request.form.get('title','').strip()
    if not title: return redirect(url_for('gallery'))
    key = re.sub(r'[^a-z0-9_]','', title.lower().replace(' ','_').replace('(','').replace(')',''))
    if not key: key = 'event'
    events = load_events()
    existing = {e['key'] for e in events}
    base, n = key, 2
    while key in existing: key = f"{base}_{n}"; n += 1
    ev = {'key':key,'title':title,'date':request.form.get('date',''),
          'venue':request.form.get('venue',''),'description':request.form.get('description',''),
          'cover':None,'created_at':datetime.now().isoformat()[:10]}
    events.insert(0, ev)
    save_events(events)
    if not _USE_SUPABASE:
        os.makedirs(_stored_path(GALLERY_FOLDER, key), exist_ok=True)
    return redirect(url_for('gallery_event', key=key))

@app.route("/gallery/<key>")
def gallery_event(key):
    events = load_events()
    ev = next((e for e in events if e['key']==key), None)
    if not ev: return redirect(url_for('gallery'))
    photos = event_photos(key)
    return render_template("gallery_event.html", event=ev, photos=photos)

@app.route('/gallery/<key>/photo/<filename>')
def gallery_photo(key, filename):
    # Only published gallery images are public; never expose arbitrary storage keys.
    ev = next((e for e in load_events() if e['key'] == key), None)
    if not ev or not allowed_file(filename) or '/' in filename or '\\' in filename or filename in ev.get('hidden_photos', []):
        abort(404)
    path = _stored_path(GALLERY_FOLDER, key, filename)
    if os.path.isfile(path):
        from flask import send_file
        return send_file(path)
    if not _USE_SUPABASE or secure_filename(filename) != filename:
        abort(404)
    content = _file_store().get('gallery/' + key + '/' + filename)
    if content is None: abort(404)
    from flask import send_file
    return send_file(io.BytesIO(content), download_name=filename, max_age=3600)

@app.route("/gallery/<key>/upload", methods=["POST"])
@login_required
def gallery_event_upload(key):
    events = load_events()
    ev = next((e for e in events if e['key']==key), None)
    if not ev: return redirect(url_for('gallery'))
    folder = _stored_path(GALLERY_FOLDER, key)
    if not _USE_SUPABASE: os.makedirs(folder, exist_ok=True)
    changed = False
    if len(request.files.getlist('photo')) > 20:
        abort(400)
    for file in request.files.getlist('photo'):
        if file and file.filename and allowed_file(file.filename):
            ext = os.path.splitext(file.filename)[1].lower()
            try:
                validate_image(file.stream, ext)
            except Exception:
                abort(400)
            filename = str(_uuid.uuid4()) + ext
            if filename:
                if _USE_SUPABASE:
                    file.seek(0)
                    _file_store().put('gallery/' + key + '/' + filename, file.read(),
                                      content_type='image/' + ('jpeg' if ext in ('.jpg', '.jpeg') else ext[1:]))
                else:
                    file.save(os.path.join(folder, filename))
                if not ev.get('cover'): ev['cover'] = filename; changed = True
    if changed: save_events(events)
    return redirect(url_for('gallery_event', key=key))

@app.route("/gallery/<key>/delete/<filename>", methods=["POST"])
@login_required
def gallery_event_delete(key, filename):
    safe = secure_filename(filename)
    if not any(e['key'] == key for e in load_events()) or safe != filename:
        abort(404)
    path = _stored_path(GALLERY_FOLDER, key, safe)
    events = load_events()
    ev = next((e for e in events if e['key']==key), None)
    if _USE_SUPABASE:
        # Keep bundled originals untouched; persist a hide marker instead.
        if os.path.exists(path):
            ev.setdefault('hidden_photos', []).append(safe)
            save_events(events)
        else:
            _file_store().delete('gallery/' + key + '/' + safe)
    elif os.path.exists(path):
        os.remove(path)
    if ev and ev.get('cover')==safe:
        remaining = event_photos(key)
        ev['cover'] = remaining[0] if remaining else None
        save_events(events)
    return redirect(url_for('gallery_event', key=key))

@app.route("/gallery/<key>/delete-event", methods=["POST"])
@login_required
def gallery_delete_event(key):
    events = load_events()
    save_events([e for e in events if e['key']!=key])
    return redirect(url_for('gallery'))

# ─── Auth ────────────────────────────────────────────────────
@app.route("/login", methods=["GET","POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    error = None
    if request.method == "POST":
        email    = request.form.get("email","").strip()
        password = request.form.get("password","").strip()
        stored = _get_account(email)
        if stored and check_password_hash(stored, password):
            _start_login_session(email, stored)
            return redirect(url_for("portal"))
        error = "이메일 또는 비밀번호가 올바르지 않습니다."
    return render_template("login.html", error=error)

@app.route("/logout", methods=['POST'])
def logout():
    if session.get('auth_session_id') and session.get('researcher'):
        sb('DELETE', 'portal_auth_sessions', params=f"?id=eq.{_q(session['auth_session_id'])}&researcher_email=eq.{_q(session['researcher'])}")
    session.clear()
    return redirect(url_for("home"))

# 프로젝트 기반 포털
# ════════════════════════════════════════════════════

@app.route("/portal")
@login_required
def portal():
    email = session["researcher"]
    # 내 프로젝트와 초대받은 프로젝트를 한 화면에서 관리한다.
    projects = sb("GET", "projects", params=f"?researcher_email=eq.{_q(email)}&order=created_at.desc") or []
    project_ids = {p.get("id") for p in projects if p.get("id")}
    collab_rows = sb("GET", "project_collaborators",
                    params=f"?researcher_email=eq.{_q(email)}&select=project_id") or []
    for collab in collab_rows:
        pid = collab.get("project_id")
        if pid and pid not in project_ids:
            rows = sb("GET", "projects", params=f"?id=eq.{_q(pid)}") or []
            if rows:
                projects.append(rows[0])
                project_ids.add(pid)

    project_map = {p.get("id"): p.get("name") or "이름 없는 프로젝트" for p in projects}
    overdue_tasks, open_tasks, recent_tasks = [], [], []
    today = datetime.utcnow().strftime("%Y-%m-%d")
    missing_measurements = []
    for p in (projects if isinstance(projects,list) else []):
        pid = p.get("id","")
        pcs = sb("GET","project_participants",params=f"?project_id=eq.{_q(pid)}&select=id") or []
        mcs = sb("GET","measurements",params=f"?project_id=eq.{_q(pid)}&select=id") or []
        p["participant_count"] = len(pcs) if isinstance(pcs,list) else 0
        p["measurement_count"] = len(mcs) if isinstance(mcs,list) else 0
        if p["participant_count"] and not p["measurement_count"]:
            missing_measurements.append(p)
        tasks = sb("GET", "tasks", params=f"?project_id=eq.{_q(pid)}&order=due_date.asc") or []
        for task in tasks:
            task["project_name"] = p.get("name") or "프로젝트"
            if task.get("status") != "done":
                open_tasks.append(task)
                if task.get("due_date") and task["due_date"] < today:
                    overdue_tasks.append(task)
                recent_tasks.append(task)

    recent_tasks.sort(key=lambda task: task.get("due_date") or "9999-12-31")

    # 원본 참여자 이름은 홈에 노출하지 않고, 활동 종류와 수신 시각만 보여준다.
    recent_activity = []
    activity_sources = [
        ("wardy_events", "🎮 Wardy", "received_at", True),
        ("neurobreeze_eeg", "🧠 NeuroBreeze EEG", "received_at", True),
        ("neurobreeze_meditation", "🧠 NeuroBreeze 명상", "received_at", True),
        ("mindmate_app_open", "💬 Mind Mate 접속", "received_at", False),
        ("mindmate_music_log", "💬 Mind Mate 음원", "updated_at", False),
        ("mindmate_daily_survey", "💬 Mind Mate 설문", "updated_at", False),
    ]
    for table, label, time_col, has_project in activity_sources:
        if not has_project and not _is_admin():
            continue
        select = f"project_id,{time_col}" if has_project else time_col
        query = f"?select={_q(select)}&order={_q(time_col)}.desc&limit=6"
        rows = _project_rows(table, query) if has_project else sb('GET', table, params=query)
        for row in rows:
            recent_activity.append({
                "label": label,
                "at": row.get(time_col) or "",
                "project_name": project_map.get(row.get("project_id"), "앱 활동") if has_project else "Mind Mate",
            })
    recent_activity.sort(key=lambda item: item["at"], reverse=True)

    # 앱 로그 중 프로젝트가 비어 있는 행은 연구자가 먼저 분류해야 하는 데이터다.
    unassigned_app_records = 0
    for table in ("wardy_events", "neurobreeze_eeg", "neurobreeze_meditation"):
        if not _is_admin():
            continue
        rows = sb("GET", table, params="?select=project_id&limit=5000") or []
        unassigned_app_records += sum(1 for row in rows if not row.get("project_id"))

    total_participants = sum(p.get("participant_count", 0) for p in projects)
    total_measurements = sum(p.get("measurement_count", 0) for p in projects)
    return render_template("portal.html",
        researcher=email,
        projects=projects if isinstance(projects,list) else [],
        total_participants=total_participants,
        total_measurements=total_measurements,
        open_tasks=open_tasks,
        overdue_tasks=overdue_tasks,
        recent_tasks=recent_tasks[:5],
        recent_activity=recent_activity[:8],
        unassigned_app_records=unassigned_app_records,
        missing_measurements=missing_measurements[:5],
        today=today)

@app.route("/portal/projects/new", methods=["POST"])
@login_required
def portal_project_new():
    sb("POST","projects",data={
        "name":            request.form.get("name","").strip(),
        "description":     request.form.get("description","").strip(),
        "researcher_email":session["researcher"],
        "app_type":        request.form.get("app_type","").strip(),
    })
    return redirect(url_for("portal"))

@app.route("/portal/projects/<project_id>")
@login_required
def portal_project(project_id):
    project, err = _require_project_owner(project_id)
    if err: return err
    participants = sb("GET","project_participants",params=f"?project_id=eq.{_q(project_id)}&order=enrolled_at.desc") or []
    measurements_raw = sb("GET","measurements",params=f"?project_id=eq.{_q(project_id)}&order=measured_at.desc") or []
    variables = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    p_map = {p["id"]: {"code": p["code"], "group_name": p.get("group_name") or ""} for p in (participants if isinstance(participants,list) else [])}
    for m in (measurements_raw if isinstance(measurements_raw,list) else []):
        info = p_map.get(m.get("participant_id",""), {})
        m["participant_code"] = info.get("code", "")
        m["group_name"] = info.get("group_name", "")
    for p in (participants if isinstance(participants,list) else []):
        p["mcount"] = sum(1 for m in (measurements_raw if isinstance(measurements_raw,list) else []) if m.get("participant_id")==p["id"])
    collaborators = sb("GET", "project_collaborators",
                       params=f"?project_id=eq.{_q(project_id)}&order=invited_at.asc") or []
    protocols = sb("GET", "project_protocols",
                   params=f"?project_id=eq.{_q(project_id)}&order=sort_order.asc") or []
    is_owner = project.get("researcher_email") == session.get("researcher")
    return render_template("portal_project.html",
        project=project,
        participants=participants if isinstance(participants,list) else [],
        measurements=measurements_raw if isinstance(measurements_raw,list) else [],
        variables=variables if isinstance(variables,list) else [],
        collaborators=collaborators,
        protocols=protocols,
        is_owner=is_owner,
        researcher=session["researcher"])

@app.route("/portal/projects/<project_id>/delete", methods=["POST"])
@login_required
def portal_project_delete(project_id):
    proj, err = _require_project_owner(project_id)
    if err: return err
    pname = proj["name"]
    sb("DELETE","measurements",params=f"?project_id=eq.{_q(project_id)}")
    sb("DELETE","project_participants",params=f"?project_id=eq.{_q(project_id)}")
    sb("DELETE","project_variables",params=f"?project_id=eq.{_q(project_id)}")
    sb("DELETE","projects",params=f"?id=eq.{_q(project_id)}")
    audit("delete_project", "projects", project_id, f"name={pname}")
    flash("프로젝트가 삭제됐습니다.")
    return redirect(url_for("portal"))

@app.route("/portal/projects/<project_id>/participants/add", methods=["POST"])
@login_required
def portal_project_add_participant(project_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    code = request.form.get("code","").strip()
    age_raw = request.form.get("age","").strip()
    # Server-side validation
    if not code or len(code) > 50:
        flash("참여자 코드는 1~50자로 입력해주세요.", "error"); return redirect(url_for("portal_project", project_id=project_id))
    if not re.match(r'^[A-Za-z0-9_\-가-힣]+$', code):
        flash("코드에 사용할 수 없는 문자가 포함됐어요.", "error"); return redirect(url_for("portal_project", project_id=project_id))
    age = None
    if age_raw:
        if not age_raw.isdigit() or not (1 <= int(age_raw) <= 120):
            flash("나이는 1~120 사이 숫자로 입력해주세요.", "error"); return redirect(url_for("portal_project", project_id=project_id))
        age = int(age_raw)
    # Duplicate code check
    existing = sb("GET","project_participants",params=f"?project_id=eq.{_q(project_id)}&code=eq.{_q(code)}")
    if existing:
        flash(f"'{code}' 코드는 이미 이 프로젝트에 존재해요.", "error"); return redirect(url_for("portal_project", project_id=project_id))
    sb("POST","project_participants",data={
        "project_id": project_id,
        "code":       code,
        "gender":     request.form.get("gender","").strip() or None,
        "age":        age,
        "group_name": request.form.get("group_name","").strip() or None,
    })
    return redirect(url_for("portal_project", project_id=project_id))

@app.route("/portal/projects/<project_id>/participants/<participant_id>/delete", methods=["POST"])
@login_required
def portal_project_delete_participant(project_id, participant_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    parts = sb("GET","project_participants",params=f"?id=eq.{_q(participant_id)}")
    code = parts[0]["code"] if parts else participant_id
    sb("DELETE","measurements",params=f"?participant_id=eq.{_q(participant_id)}")
    sb("DELETE","project_participants",params=f"?id=eq.{_q(participant_id)}")
    audit("delete_participant", "project_participants", participant_id, f"code={code},project={project_id}")
    return redirect(url_for("portal_project", project_id=project_id))

@app.route("/portal/projects/<project_id>/participants/<participant_id>/edit", methods=["POST"])
@login_required
def portal_edit_participant(project_id, participant_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    age_raw = request.form.get("age","").strip()
    sb("PATCH","project_participants",
       data={"gender":request.form.get("gender","").strip() or None,
             "age":int(age_raw) if age_raw.isdigit() else None,
             "group_name":request.form.get("group_name","").strip() or None},
       params=f"?id=eq.{_q(participant_id)}")
    return redirect(url_for("portal_participant_detail", project_id=project_id, participant_id=participant_id))

@app.route("/portal/projects/<project_id>/participants/<participant_id>")
@login_required
def portal_participant_detail(project_id, participant_id):
    proj, err = _require_project_owner(project_id)
    if err: return err
    p_list = sb("GET","project_participants",params=f"?id=eq.{_q(participant_id)}")
    if not p_list or not isinstance(p_list,list): return redirect(url_for("portal_project",project_id=project_id))
    participant = p_list[0]
    measurements = sb("GET","measurements",params=f"?participant_id=eq.{_q(participant_id)}&order=measured_at.asc") or []
    variables = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    # Pre→Post changes (first vs last phase for each variable)
    changes = {}
    # Multi-phase delta transitions: list of {from_phase, to_phase, deltas:{vname:{diff,pct}}}
    phase_deltas = []
    if isinstance(measurements,list) and isinstance(variables,list) and measurements:
        # Simple pre/post (first vs last measurement)
        first_m = measurements[0]
        last_m  = measurements[-1] if len(measurements)>1 else None
        pre_data  = first_m.get("data") or {}
        post_data = last_m.get("data") or {} if last_m else {}
        if last_m and first_m["id"] != last_m["id"]:
            for v in variables:
                vn = v["name"]
                try:
                    pre_val  = float(pre_data.get(vn) or 0)
                    post_val = float(post_data.get(vn) or 0)
                    pct = round((post_val-pre_val)/pre_val*100,1) if pre_val else None
                    changes[vn] = {"pre":pre_val,"post":post_val,
                                   "diff":round(post_val-pre_val,2),"pct":pct,
                                   "label":v.get("label") or vn,"unit":v.get("unit","")}
                except: pass
        # Consecutive phase deltas
        for i in range(len(measurements)-1):
            m_a = measurements[i];  m_b = measurements[i+1]
            d_a = m_a.get("data") or {};  d_b = m_b.get("data") or {}
            entry = {"from_phase": m_a.get("phase") or f"측정{i+1}",
                     "to_phase":   m_b.get("phase") or f"측정{i+2}",
                     "deltas": {}}
            for v in variables:
                vn = v["name"]
                try:
                    va = float(d_a.get(vn) or 0);  vb = float(d_b.get(vn) or 0)
                    pct = round((vb-va)/va*100,1) if va else None
                    entry["deltas"][vn] = {"diff":round(vb-va,2),"pct":pct,
                                           "label":v.get("label") or vn,"unit":v.get("unit","")}
                except: pass
            if entry["deltas"]:
                phase_deltas.append(entry)
    return render_template("portal_participant_detail.html",
        project=proj, participant=participant,
        measurements=measurements if isinstance(measurements,list) else [],
        variables=variables if isinstance(variables,list) else [],
        changes=changes, phase_deltas=phase_deltas,
        researcher=session["researcher"])

@app.route("/portal/projects/<project_id>/measurements/add", methods=["POST"])
@login_required
def portal_project_add_measurement(project_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    variables = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    if variables and isinstance(variables,list):
        data_dict = {}
        for v in variables:
            val = request.form.get(f"var_{v['name']}","").strip()
            if val:
                try:    data_dict[v["name"]] = float(val) if v.get("var_type")=="number" else val
                except: data_dict[v["name"]] = val
    else:
        raw = request.form.get("data_json","").strip()
        try:    data_dict = json.loads(raw) if raw else {}
        except: data_dict = {"raw": raw}
    sb("POST","measurements",data={
        "project_id":     project_id,
        "participant_id": request.form.get("participant_id","").strip(),
        "phase":          request.form.get("phase","").strip() or None,
        "notes":          request.form.get("notes","").strip() or None,
        "data":           data_dict,
    })
    return redirect(url_for("portal_project", project_id=project_id))

@app.route("/portal/projects/<project_id>/measurements/<measurement_id>/delete", methods=["POST"])
@login_required
def portal_project_delete_measurement(project_id, measurement_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    sb("DELETE","measurements",params=f"?id=eq.{_q(measurement_id)}")
    audit("delete_measurement", "measurements", measurement_id, f"project={project_id}")
    return redirect(url_for("portal_project", project_id=project_id))

@app.route("/portal/projects/<project_id>/measurements/<measurement_id>/edit", methods=["POST"])
@login_required
def portal_edit_measurement(project_id, measurement_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    variables = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    if variables and isinstance(variables,list):
        data_dict = {}
        for v in variables:
            val = request.form.get(f"var_{v['name']}","").strip()
            if val:
                try:    data_dict[v["name"]] = float(val) if v.get("var_type")=="number" else val
                except: data_dict[v["name"]] = val
    else:
        raw = request.form.get("data_json","").strip()
        try:    data_dict = json.loads(raw) if raw else {}
        except: data_dict = {"raw": raw}
    sb("PATCH","measurements",
       data={"phase":request.form.get("phase","").strip() or None,
             "notes":request.form.get("notes","").strip() or None,
             "data":data_dict},
       params=f"?id=eq.{_q(measurement_id)}")
    return redirect(url_for("portal_participant_detail", project_id=project_id,
                            participant_id=request.form.get("participant_id","")))

@app.route("/portal/projects/<project_id>/variables/add", methods=["POST"])
@login_required
def portal_project_add_variable(project_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    sb("POST","project_variables",data={
        "project_id": project_id,
        "name":       request.form.get("name","").strip(),
        "label":      request.form.get("label","").strip() or None,
        "var_type":   request.form.get("var_type","number"),
        "unit":       request.form.get("unit","").strip() or None,
    })
    return redirect(url_for("portal_project", project_id=project_id))

@app.route("/portal/projects/<project_id>/export")
@login_required
def portal_project_export(project_id):
    proj, err = _require_project_owner(project_id)
    if err: return err
    proj_name = proj["name"]
    participants = sb("GET","project_participants",params=f"?project_id=eq.{_q(project_id)}") or []
    measurements = sb("GET","measurements",params=f"?project_id=eq.{_q(project_id)}&order=measured_at.desc") or []
    p_map = {p["id"]: p for p in (participants if isinstance(participants,list) else [])}
    output = io.StringIO()
    writer = SafeCSVWriter(output)
    writer.writerow(["participant_code","group","gender","age","phase","measured_at","notes","data"])
    for m in (measurements if isinstance(measurements,list) else []):
        p = p_map.get(m.get("participant_id",""), {})
        writer.writerow([p.get("code",""),p.get("group_name",""),p.get("gender",""),p.get("age",""),
                         m.get("phase",""),m.get("measured_at","")[:10],
                         m.get("notes",""),json.dumps(m.get("data",{}),ensure_ascii=False)])
    output.seek(0)
    safe = proj_name.replace(" ","_")
    return Response(output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition":f"attachment;filename={safe}_{datetime.now().strftime('%Y%m%d')}.csv"})

@app.route("/portal/projects/<project_id>/export/range")
@login_required
def portal_project_export_range(project_id):
    proj, err = _require_project_owner(project_id)
    if err: return err
    proj_name = proj["name"]
    participants = sb("GET","project_participants",params=f"?project_id=eq.{_q(project_id)}") or []
    variables    = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    measurements = sb("GET","measurements",params=f"?project_id=eq.{_q(project_id)}&order=measured_at.asc") or []
    p_map = {p["id"]: p for p in (participants if isinstance(participants,list) else [])}
    # Read filter params
    date_from = request.args.get("date_from","").strip()
    date_to   = request.args.get("date_to","").strip()
    phase_f   = request.args.get("phase","").strip()
    group_f   = request.args.get("group","").strip()
    var_f     = request.args.get("var_name","").strip()
    val_min   = request.args.get("val_min","").strip()
    val_max   = request.args.get("val_max","").strip()
    excl_f    = request.args.get("exclude_excl","1")  # "1" = exclude flagged rows by default
    var_names = [v["name"] for v in (variables if isinstance(variables,list) else [])]
    output = io.StringIO()
    writer = SafeCSVWriter(output)
    header = ["participant_code","group","gender","age","phase","measured_at","notes"] + var_names
    writer.writerow(header)
    for m in (measurements if isinstance(measurements,list) else []):
        if excl_f == "1" and m.get("excluded"): continue
        dt = (m.get("measured_at") or "")[:10]
        if date_from and dt < date_from: continue
        if date_to   and dt > date_to:   continue
        if phase_f and m.get("phase","") != phase_f: continue
        p = p_map.get(m.get("participant_id",""), {})
        if group_f and p.get("group_name","") != group_f: continue
        data = m.get("data") or {}
        if var_f and (val_min or val_max):
            try:
                v_val = float(data.get(var_f) or 0)
                if val_min and v_val < float(val_min): continue
                if val_max and v_val > float(val_max): continue
            except: pass
        row = [p.get("code",""), p.get("group_name",""), p.get("gender",""), p.get("age",""),
               m.get("phase",""), dt, m.get("notes","")]
        for vn in var_names:
            row.append(data.get(vn,""))
        writer.writerow(row)
    output.seek(0)
    safe = proj_name.replace(" ","_")
    return Response(output.getvalue(), mimetype="text/csv;charset=utf-8-sig",
        headers={"Content-Disposition":f"attachment;filename={safe}_range_{datetime.now().strftime('%Y%m%d')}.csv"})

# ── Excel 시트 목록 미리보기 (AJAX) ──────────────────
@app.route('/portal/projects/<project_id>/upload/sheets', methods=['POST'])
@login_required
def portal_project_upload_sheets(project_id):
    if not _project_access_ok(project_id, session["researcher"]):
        return jsonify({"error":"Unauthorized"}), 403
    import tempfile, openpyxl
    f = request.files.get('file')
    if not f: return jsonify({"sheets": []})
    fname_l = f.filename.lower()
    if fname_l.endswith('.xlsx'):
        tmp_fd, tmppath = tempfile.mkstemp(suffix='.xlsx')
        os.close(tmp_fd)
        try:
            f.save(tmppath)
            wb = openpyxl.load_workbook(tmppath, read_only=True)
            sheets = wb.sheetnames
            wb.close()
            return jsonify({"sheets": sheets})
        except:
            return jsonify({"sheets": []})
        finally:
            if os.path.exists(tmppath): os.unlink(tmppath)
    elif fname_l.endswith('.xls'):
        import xlrd as _xlrd
        tmp_fd, tmppath = tempfile.mkstemp(suffix='.xls')
        os.close(tmp_fd)
        try:
            f.save(tmppath)
            wb_xls = _xlrd.open_workbook(tmppath)
            return jsonify({"sheets": wb_xls.sheet_names()})
        except:
            return jsonify({"sheets": []})
        finally:
            if os.path.exists(tmppath): os.unlink(tmppath)
    return jsonify({"sheets": []})

@app.route('/portal/projects/<project_id>/upload', methods=['POST'])
@login_required
def portal_project_upload(project_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    f = request.files.get('file')
    if not f or not f.filename:
        flash('파일을 선택해주세요.', 'error')
        return redirect(url_for('portal_project', project_id=project_id))

    original_name = f.filename
    fname_lower = original_name.lower()
    sheet_name = request.form.get('sheet_name', '').strip()
    rows = []
    tmppath = None

    import tempfile, openpyxl

    try:
        if fname_lower.endswith('.csv'):
            content = f.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(content))
            from itertools import islice
            rows = list(islice(reader, 10001))
            if len(rows) > 10000 or any(len(row) > 200 for row in rows):
                raise ValueError('Spreadsheet limit exceeded')
        elif fname_lower.endswith('.xlsx'):
            tmp_fd, tmppath = tempfile.mkstemp(suffix='.xlsx')
            os.close(tmp_fd)
            f.seek(0); f.save(tmppath)
            wb = openpyxl.load_workbook(tmppath, read_only=True, data_only=True)
            # 시트 선택: 지정된 시트명 → 없으면 첫 번째 시트
            if sheet_name and sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
            else:
                ws = wb.active
            if (ws.max_row or 0) > 10001 or (ws.max_column or 0) > 200:
                raise ValueError('Spreadsheet limit exceeded')
            headers = [str(c.value or '').strip() for c in next(ws.iter_rows(max_row=1, max_col=min(ws.max_column or 200, 200)))]
            # 빈 헤더 제거
            headers = [h if h else f'col_{i}' for i, h in enumerate(headers)]
            for row in ws.iter_rows(min_row=2, max_row=min(ws.max_row or 10002, 10002), max_col=len(headers), values_only=True):
                if any(v is not None for v in row):
                    rows.append({headers[i]: (str(v).strip() if v is not None else '') for i, v in enumerate(row) if i < len(headers)})
            wb.close()
            if len(rows) > 10000:
                raise ValueError('Spreadsheet limit exceeded')
        elif fname_lower.endswith('.xls'):
            import xlrd as _xlrd, tempfile as _tf
            tmp_fd2, tmppath2 = _tf.mkstemp(suffix='.xls')
            os.close(tmp_fd2)
            f.seek(0); f.save(tmppath2)
            try:
                wb_xls = _xlrd.open_workbook(tmppath2)
                if sheet_name and sheet_name in wb_xls.sheet_names():
                    ws_xls = wb_xls.sheet_by_name(sheet_name)
                else:
                    ws_xls = wb_xls.sheet_by_index(0)
                if ws_xls.nrows > 10001 or ws_xls.ncols > 200:
                    raise ValueError('Spreadsheet limit exceeded')
                if ws_xls.nrows < 2:
                    flash('데이터가 없어요.', 'error')
                    return redirect(url_for('portal_project', project_id=project_id))
                raw_h = [str(ws_xls.cell_value(0, c)).strip() for c in range(ws_xls.ncols)]
                xls_headers = [h if h else f'col_{i}' for i, h in enumerate(raw_h)]
                for r in range(1, ws_xls.nrows):
                    rv = [ws_xls.cell_value(r, c) for c in range(ws_xls.ncols)]
                    if any(v != '' and v is not None for v in rv):
                        rows.append({xls_headers[i]: (str(v).strip() if v != '' and v is not None else '') for i, v in enumerate(rv) if i < len(xls_headers)})
            finally:
                try: os.unlink(tmppath2)
                except: pass
        else:
            flash('CSV 또는 Excel(.xlsx) 파일만 업로드 가능해요.', 'error')
            return redirect(url_for('portal_project', project_id=project_id))
    except Exception as e:
        flash('파일을 읽을 수 없습니다. 형식과 크기를 확인해주세요.', 'error')
        return redirect(url_for('portal_project', project_id=project_id))
    finally:
        if tmppath and os.path.exists(tmppath):
            try: os.unlink(tmppath)
            except: pass

    if not rows:
        flash('데이터가 없어요.', 'error')
        return redirect(url_for('portal_project', project_id=project_id))

    # 원본 보존 성공 후에만 연구 데이터 가져오기를 시작합니다.
    stored_name, file_size = _save_portal_upload(f, project_id)

    # ── 컬럼 스키마 분석 ─────────────────────────────────────
    all_headers = [h for h in rows[0].keys() if h]

    def _is_numeric_col(header):
        vals = [r.get(header,'') for r in rows[:20]]
        nums = 0
        for v in vals:
            try:
                if str(v).strip(): float(str(v)); nums += 1
            except: pass
        return nums >= max(1, len([v for v in vals if str(v).strip()]) * 0.7)

    col_schema = [{"name": h, "type": "number" if _is_numeric_col(h) else "text"} for h in all_headers]

    # ── portal_files 메타데이터 저장 ────────────────────────
    file_rec = sb('POST', 'portal_files', data={
        'project_id': project_id,
        'filename': stored_name,
        'original_name': original_name,
        'size': file_size,
        'researcher_email': session['researcher'],
        'column_schema': json.dumps(col_schema, ensure_ascii=False),
        'row_count': len(rows),
    })
    file_id = file_rec[0]['id'] if file_rec else None
    if not file_id:
        raise StorageUnavailable('File metadata could not be saved; original retained')

    # ── 컬럼 매핑 ────────────────────────────────────────────
    def slugify(s):
        s = re.sub(r'\s+', '_', s.strip())
        s = re.sub(r'[^\w가-힣]', '', s)
        return s[:40] or 'col'

    code_col = next((h for h in all_headers if h.lower() in ('code','코드','이름','name','참여자')), None)
    known_map = {}
    skip_cols = {code_col} if code_col else set()
    for h in all_headers:
        hl = h.lower()
        if hl in ('gender','성별'):       known_map[h] = 'gender'
        elif hl in ('age','나이','연령'): known_map[h] = 'age'
        elif hl in ('group','그룹','군'): known_map[h] = 'group'
        elif hl in ('phase','단계','차수'): known_map[h] = 'phase'
        elif hl in ('notes','메모','비고'): known_map[h] = 'notes'
        elif hl in ('타임스탬프','timestamp'): skip_cols.add(h)
    skip_cols.update(known_map.keys())
    data_cols = [h for h in all_headers if h not in skip_cols]

    # ── 변수 자동 등록 ────────────────────────────────────────
    existing_vars = sb('GET', 'project_variables', params=f'?project_id=eq.{_q(project_id)}') or []
    existing_var_names = {v['name'] for v in (existing_vars if isinstance(existing_vars,list) else [])}
    for h in data_cols:
        vname = slugify(h)
        if vname and vname not in existing_var_names:
            vtype = next((c['type'] for c in col_schema if c['name']==h), 'text')
            sb('POST', 'project_variables', data={
                'project_id': project_id, 'name': vname,
                'label': h[:80], 'var_type': vtype, 'unit': None,
            })
            existing_var_names.add(vname)

    # ── 참여자 + 측정 데이터 저장 ────────────────────────────
    existing_parts = sb('GET', 'project_participants', params=f'?project_id=eq.{_q(project_id)}') or []
    pmap = {p['code']: p['id'] for p in (existing_parts if isinstance(existing_parts,list) else [])}
    added_p = added_m = 0
    row_errors = []

    def _get_known(row, field):
        h = next((k for k,v in known_map.items() if v==field), None)
        return str(row.get(h,'')).strip() if h else ''

    for i, row in enumerate(rows, start=2):
        if all(not str(v).strip() for v in row.values()): continue
        code = str(row.get(code_col,'')).strip() if code_col else f'P{i-1:03d}'
        if not code: code = f'P{i-1:03d}'
        code = code[:50]

        if code not in pmap:
            age_raw = _get_known(row, 'age')
            age = int(age_raw) if age_raw.isdigit() and 1 <= int(age_raw) <= 120 else None
            newp = sb('POST', 'project_participants', data={
                'project_id': project_id, 'code': code,
                'gender': _get_known(row,'gender') or None,
                'age': age,
                'group_name': _get_known(row,'group') or None,
            })
            if newp and isinstance(newp, list):
                pmap[code] = newp[0]['id']; added_p += 1
            else:
                row_errors.append(f'{i}행: 참여자 등록 실패'); continue

        pid = pmap.get(code)
        if not pid: continue

        datadict = {}
        for h in data_cols:
            vname = slugify(h)
            val = str(row.get(h,'')).strip()
            if val:
                try: datadict[vname] = float(val) if _is_numeric_col(h) else val
                except: datadict[vname] = val

        sb('POST', 'measurements', data={
            'project_id': project_id,
            'participant_id': pid,
            'phase': _get_known(row,'phase') or None,
            'notes': _get_known(row,'notes') or None,
            'data': datadict,
            'source_file_id': file_id,
        })
        added_m += 1

    msg = f'완료! 참여자 {added_p}명 추가, 측정 {added_m}건 저장.'
    if row_errors:
        msg += f' 오류 {len(row_errors)}건: {", ".join(row_errors[:3])}'
    flash(msg)
    if file_id:
        return redirect(url_for('portal_project_data', project_id=project_id, file_id=file_id))
    return redirect(url_for('portal_project', project_id=project_id))

@app.route("/portal/projects/<project_id>/stats")
@login_required
def portal_project_stats(project_id):
    import math
    proj, err = _require_project_owner(project_id)
    if err: return err
    measurements = sb("GET","measurements",params=f"?project_id=eq.{_q(project_id)}") or []
    variables    = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    participants = sb("GET","project_participants",params=f"?project_id=eq.{_q(project_id)}") or []
    stats = {}
    if isinstance(measurements,list) and isinstance(variables,list):
        for v in variables:
            vn = v["name"]
            all_vals = []; by_phase = {}
            for m in measurements:
                val = m.get("data",{}).get(vn) if m.get("data") else None
                if val is not None:
                    try:
                        fval=float(val); all_vals.append(fval)
                        ph=m.get("phase","전체") or "전체"
                        by_phase.setdefault(ph,[]).append(fval)
                    except: pass
            def calc(vals):
                if not vals: return {}
                n=len(vals); avg=sum(vals)/n
                sd=math.sqrt(sum((x-avg)**2 for x in vals)/n) if n>1 else 0
                s=sorted(vals); med=s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2
                return {"n":n,"mean":round(avg,2),"sd":round(sd,2),"median":round(med,2),"min":round(min(vals),2),"max":round(max(vals),2)}
            outliers = []
            pid_to_code = {p["id"]: p.get("code","") for p in (participants if isinstance(participants,list) else [])}
            if len(all_vals)>=4:
                s_sorted=sorted(all_vals); q1=s_sorted[len(s_sorted)//4]; q3=s_sorted[3*len(s_sorted)//4]
                iqr=q3-q1; lb,ub=q1-1.5*iqr,q3+1.5*iqr
                for m in measurements:
                    if m.get("excluded"): continue
                    val=m.get("data",{}).get(vn) if m.get("data") else None
                    if val is not None:
                        try:
                            fval=float(val)
                            if fval<lb or fval>ub:
                                outliers.append({
                                    "measurement_id": m["id"],
                                    "participant_code": pid_to_code.get(m.get("participant_id",""),"?"),
                                    "phase": m.get("phase",""),
                                    "value": fval,
                                    "lb": round(lb,2), "ub": round(ub,2)
                                })
                        except: pass
            missing=[]
            phases=list({m.get("phase","") for m in measurements if m.get("phase")})
            for p in (participants if isinstance(participants,list) else []):
                p_ms=[m for m in measurements if m.get("participant_id")==p["id"]]
                for ph in phases:
                    if not any(m.get("phase")==ph and m.get("data",{}).get(vn) is not None for m in p_ms):
                        missing.append({"code":p["code"],"phase":ph})
            # by_group stats (group_name per participant)
            pid_to_group = {p["id"]: (p.get("group_name") or "미지정") for p in (participants if isinstance(participants,list) else [])}
            by_group = {}
            by_group_phase = {}  # {group: {phase: [vals]}}
            for m in measurements:
                val = m.get("data",{}).get(vn) if m.get("data") else None
                if val is not None:
                    try:
                        fval = float(val)
                        grp = pid_to_group.get(m.get("participant_id",""), "미지정")
                        by_group.setdefault(grp, []).append(fval)
                        ph = m.get("phase","전체") or "전체"
                        by_group_phase.setdefault(grp, {}).setdefault(ph, []).append(fval)
                    except: pass
            stats[vn]={"label":v.get("label") or vn,"unit":v.get("unit",""),
                       "overall":calc(all_vals),"by_phase":{ph:calc(vals) for ph,vals in by_phase.items()},
                       "by_group":{grp:calc(vals) for grp,vals in by_group.items()},
                       "by_group_phase":{grp:{ph:calc(vals) for ph,vals in phases.items()} for grp,phases in by_group_phase.items()},
                       "outliers":outliers,"missing":missing}
    # collect all phases and groups for template
    all_phases = sorted({m.get("phase","") for m in (measurements if isinstance(measurements,list) else []) if m.get("phase")})
    all_groups = sorted({(p.get("group_name") or "미지정") for p in (participants if isinstance(participants,list) else [])})
    return render_template("portal_project_stats.html",
        project=proj,stats=stats,variables=variables if isinstance(variables,list) else [],
        all_phases=all_phases,all_groups=all_groups,
        researcher=session["researcher"])

# ── Bulk outlier exclude ──────────────────────────────
@app.route("/portal/projects/<project_id>/outliers/exclude", methods=["POST"])
@login_required
def portal_outliers_exclude(project_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    ids = request.form.getlist("measurement_ids")
    if ids:
        for mid in ids:
            sb("PATCH", "measurements", data={"excluded": 1},
               params=f"?id=eq.{_q(mid)}&project_id=eq.{_q(project_id)}")
    flash(f"{len(ids)}건의 이상값을 제외 처리했습니다.")
    return redirect(url_for("portal_project_stats", project_id=project_id))

# ── Variable edit / delete ────────────────────────────
@app.route("/portal/projects/<project_id>/variables/<var_id>/edit", methods=["POST"])
@login_required
def portal_variable_edit(project_id, var_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    sb("PATCH", "project_variables",
       data={
           "label":    request.form.get("label","").strip() or None,
           "unit":     request.form.get("unit","").strip() or None,
           "var_type": request.form.get("var_type","number"),
       },
       params=f"?id=eq.{_q(var_id)}")
    return redirect(url_for("portal_project", project_id=project_id, _anchor="tab-variables"))

@app.route("/portal/projects/<project_id>/variables/<var_id>/delete", methods=["POST"])
@login_required
def portal_variable_delete(project_id, var_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    vrows = sb("GET","project_variables",params=f"?id=eq.{_q(var_id)}")
    vname = vrows[0]["name"] if vrows else var_id
    sb("DELETE", "project_variables", params=f"?id=eq.{_q(var_id)}")
    audit("delete_variable", "project_variables", var_id, f"name={vname},project={project_id}")
    return redirect(url_for("portal_project", project_id=project_id, _anchor="tab-variables"))

# ── Measurement exclude toggle ────────────────────────
@app.route("/portal/projects/<project_id>/measurements/<m_id>/toggle-exclude", methods=["POST"])
@login_required
def portal_measurement_toggle_exclude(project_id, m_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    rows = sb("GET", "measurements", params=f"?id=eq.{_q(m_id)}")
    if rows:
        new_val = 0 if rows[0].get("excluded") else 1
        sb("PATCH", "measurements", data={"excluded": new_val}, params=f"?id=eq.{_q(m_id)}")
        audit("toggle_exclude_measurement", "measurements", m_id,
              f"excluded={new_val},project={project_id}")
    return redirect(url_for("portal_project", project_id=project_id, _anchor="tab-measurements"))

# ── Statistical tests page ────────────────────────────
@app.route("/portal/projects/<project_id>/tests")
@login_required
def portal_project_tests(project_id):
    import math
    proj, err = _require_project_owner(project_id)
    if err: return err
    measurements_raw = sb("GET","measurements",params=f"?project_id=eq.{_q(project_id)}") or []
    variables    = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    participants = sb("GET","project_participants",params=f"?project_id=eq.{_q(project_id)}") or []

    # Inject group_name into measurements
    pid_to_group = {p["id"]: (p.get("group_name") or "미지정") for p in (participants if isinstance(participants,list) else [])}
    measurements = [m for m in (measurements_raw if isinstance(measurements_raw,list) else []) if not m.get("excluded")]
    for m in measurements:
        m["group_name"] = pid_to_group.get(m.get("participant_id",""), "미지정")

    groups = sorted({m["group_name"] for m in measurements})
    phases = sorted({m.get("phase","") for m in measurements if m.get("phase")})

    results = {}
    if isinstance(variables,list):
        for v in variables:
            vn = v["name"]
            vres = {"label": v.get("label") or vn, "unit": v.get("unit",""), "tests": [], "paired": []}

            # --- Independent tests between every pair of groups ---
            grp_vals = {}
            for m in measurements:
                val = m.get("data",{}).get(vn) if m.get("data") else None
                if val is not None:
                    try: grp_vals.setdefault(m["group_name"],[]).append(float(val))
                    except: pass

            grp_list = list(grp_vals.keys())

            # Normality tests (Shapiro-Wilk per group)
            normality = {}
            for grp, vals in grp_vals.items():
                if 3 <= len(vals) <= 5000:
                    try:
                        sw_stat, sw_p = _scipy_stats.shapiro(vals)
                        normality[grp] = {"stat": round(float(sw_stat),4), "p": round(float(sw_p),4), "normal": sw_p >= 0.05}
                    except: pass
            vres["normality"] = normality

            for i in range(len(grp_list)):
                for j in range(i+1, len(grp_list)):
                    g1, g2 = grp_list[i], grp_list[j]
                    a, b = grp_vals[g1], grp_vals[g2]
                    if len(a) < 2 or len(b) < 2: continue
                    try:
                        t_stat, t_p = _scipy_stats.ttest_ind(a, b, equal_var=False)
                        u_stat, u_p = _scipy_stats.mannwhitneyu(a, b, alternative="two-sided")
                        # Cohen's d
                        pooled_n = len(a)+len(b)
                        pooled_sd = _np.sqrt(((len(a)-1)*_np.var(a,ddof=1) + (len(b)-1)*_np.var(b,ddof=1)) / (pooled_n-2)) if pooled_n > 2 else 0
                        cohens_d = ((_np.mean(a) - _np.mean(b)) / pooled_sd) if pooled_sd > 0 else 0
                        # Bootstrap 95% CI for Cohen's d
                        d_ci = None
                        try:
                            n_boot = 1000
                            boot_d = []
                            rng = _np.random.default_rng(42)
                            for _ in range(n_boot):
                                ba = rng.choice(a, size=len(a), replace=True)
                                bb = rng.choice(b, size=len(b), replace=True)
                                ps = float(_np.sqrt(((len(ba)-1)*_np.var(ba,ddof=1) + (len(bb)-1)*_np.var(bb,ddof=1)) / (len(ba)+len(bb)-2)))
                                if ps > 0: boot_d.append((float(_np.mean(ba))-float(_np.mean(bb)))/ps)
                            if boot_d:
                                d_ci = [round(float(_np.percentile(boot_d,2.5)),3), round(float(_np.percentile(boot_d,97.5)),3)]
                        except: pass
                        # Rank-biserial r for Mann-Whitney
                        r_rb = 1 - 2*u_stat / (len(a)*len(b))
                        vres["tests"].append({
                            "g1": g1, "g2": g2,
                            "n1": len(a), "n2": len(b),
                            "mean1": round(float(_np.mean(a)),3), "mean2": round(float(_np.mean(b)),3),
                            "sd1": round(float(_np.std(a,ddof=1)),3), "sd2": round(float(_np.std(b,ddof=1)),3),
                            "t": round(float(t_stat),3), "t_p": round(float(t_p),4),
                            "U": round(float(u_stat),1), "u_p": round(float(u_p),4),
                            "d": round(float(cohens_d),3), "r": round(float(r_rb),3),
                            "t_sig": t_p < 0.05, "u_sig": u_p < 0.05,
                            "d_ci": d_ci,
                        })
                    except Exception as e:
                        print(f"test error {vn} {g1} vs {g2}: {e}")

            # ANOVA / Kruskal-Wallis for 3+ groups
            if len(grp_list) >= 3:
                valid_groups = [g for g in grp_list if len(grp_vals[g]) >= 2]
                all_groups_data = [grp_vals[g] for g in valid_groups]
                if len(all_groups_data) >= 3:
                    try:
                        f_stat, f_p = _scipy_stats.f_oneway(*all_groups_data)
                        h_stat, h_p = _scipy_stats.kruskal(*all_groups_data)
                        grand = [x for g in all_groups_data for x in g]
                        grand_mean = float(_np.mean(grand))
                        ss_between = sum(len(g)*(float(_np.mean(g))-grand_mean)**2 for g in all_groups_data)
                        ss_total = sum((x-grand_mean)**2 for x in grand)
                        eta2 = round(ss_between/ss_total, 3) if ss_total > 0 else 0
                        # Tukey HSD post-hoc (only if ANOVA is significant)
                        tukey_pairs = []
                        if f_p < 0.05:
                            try:
                                tukey_res = _scipy_stats.tukey_hsd(*all_groups_data)
                                for ti in range(len(valid_groups)):
                                    for tj in range(ti+1, len(valid_groups)):
                                        gi, gj = valid_groups[ti], valid_groups[tj]
                                        tval = tukey_res.statistic[ti][tj]
                                        tpval = tukey_res.pvalue[ti][tj]
                                        tukey_pairs.append({
                                            "g1": gi, "g2": gj,
                                            "stat": round(float(tval),3),
                                            "p": round(float(tpval),4),
                                            "sig": float(tpval) < 0.05,
                                        })
                            except Exception as te:
                                print(f"tukey error {vn}: {te}")
                        vres["anova"] = {
                            "groups": valid_groups,
                            "F": round(float(f_stat),3), "f_p": round(float(f_p),4), "f_sig": f_p < 0.05,
                            "H": round(float(h_stat),3), "h_p": round(float(h_p),4), "h_sig": h_p < 0.05,
                            "eta2": eta2,
                            "tukey": tukey_pairs,
                        }
                    except Exception as e:
                        print(f"anova error {vn}: {e}")

            # --- Paired t-test (pre vs post within same participant) ---
            if len(phases) >= 2:
                ph_pairs = [(phases[i], phases[i+1]) for i in range(len(phases)-1)]
                for pre_ph, post_ph in ph_pairs:
                    paired_pre, paired_post = [], []
                    for p in (participants if isinstance(participants,list) else []):
                        pre_m  = [m for m in measurements if m.get("participant_id")==p["id"] and m.get("phase")==pre_ph]
                        post_m = [m for m in measurements if m.get("participant_id")==p["id"] and m.get("phase")==post_ph]
                        if pre_m and post_m:
                            pv = pre_m[0].get("data",{}).get(vn)
                            ov = post_m[0].get("data",{}).get(vn)
                            if pv is not None and ov is not None:
                                try: paired_pre.append(float(pv)); paired_post.append(float(ov))
                                except: pass
                    if len(paired_pre) >= 2:
                        try:
                            t_s, t_p = _scipy_stats.ttest_rel(paired_pre, paired_post)
                            diffs = [b-a for a,b in zip(paired_pre,paired_post)]
                            sd_d = float(_np.std(diffs,ddof=1))
                            d_paired = float(_np.mean(diffs)) / sd_d if sd_d > 0 else 0
                            vres["paired"].append({
                                "pre_phase": pre_ph, "post_phase": post_ph,
                                "n": len(paired_pre),
                                "mean_pre": round(float(_np.mean(paired_pre)),3),
                                "mean_post": round(float(_np.mean(paired_post)),3),
                                "mean_diff": round(float(_np.mean(diffs)),3),
                                "sd_diff": round(sd_d,3),
                                "t": round(float(t_s),3), "p": round(float(t_p),4),
                                "d": round(d_paired,3),
                                "sig": t_p < 0.05,
                            })
                        except Exception as e:
                            print(f"paired test error {vn}: {e}")
            results[vn] = vres

    # --- Correlation matrix ---
    corr_matrix = None
    corr_labels = []
    if isinstance(variables,list) and len(variables) >= 2:
        num_vars = [v for v in variables if v.get("var_type","number") == "number"]
        if len(num_vars) >= 2:
            corr_labels = [v.get("label") or v["name"] for v in num_vars]
            col_data = []
            for v in num_vars:
                col_data.append([float(m.get("data",{}).get(v["name"])) for m in measurements
                                  if m.get("data",{}).get(v["name"]) is not None
                                  and str(m.get("data",{}).get(v["name"],"")).replace(".","",1).lstrip("-").isdigit()])
            # Use pairwise complete obs approach
            n_vars = len(num_vars)
            matrix = [[None]*n_vars for _ in range(n_vars)]
            for i in range(n_vars):
                for j in range(n_vars):
                    if i == j:
                        matrix[i][j] = {"r": 1.0, "p": 0.0, "sig": False}
                    else:
                        # Get paired values (rows where both have data)
                        pairs = []
                        for m in measurements:
                            if not m.get("data"): continue
                            vi = m["data"].get(num_vars[i]["name"])
                            vj = m["data"].get(num_vars[j]["name"])
                            if vi is not None and vj is not None:
                                try: pairs.append((float(vi), float(vj)))
                                except: pass
                        if len(pairs) >= 3:
                            xs = [p[0] for p in pairs]
                            ys = [p[1] for p in pairs]
                            try:
                                r, p_val = _scipy_stats.pearsonr(xs, ys)
                                matrix[i][j] = {"r": round(float(r), 3), "p": round(float(p_val), 4), "sig": p_val < 0.05}
                            except: matrix[i][j] = None
            corr_matrix = matrix

    return render_template("portal_project_tests.html",
        project=proj,
        results=results,
        variables=variables if isinstance(variables,list) else [],
        corr_matrix=corr_matrix,
        corr_labels=corr_labels,
        groups=groups,
        phases=phases,
        researcher=session["researcher"])

@app.route("/portal/projects/<project_id>/tests/export")
@login_required
def portal_project_tests_export(project_id):
    """Export statistical test results as CSV."""
    _, err = _require_project_owner(project_id)
    if err: return err
    # Re-run the test logic (lightweight version returning only flat rows)
    measurements_raw = sb("GET","measurements",params=f"?project_id=eq.{_q(project_id)}") or []
    variables    = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    participants = sb("GET","project_participants",params=f"?project_id=eq.{_q(project_id)}") or []
    proj = sb("GET","projects",params=f"?id=eq.{_q(project_id)}")
    proj_name = proj[0]["name"] if proj else "project"
    pid_to_group = {p["id"]: (p.get("group_name") or "미지정") for p in (participants if isinstance(participants,list) else [])}
    measurements = [m for m in (measurements_raw if isinstance(measurements_raw,list) else []) if not m.get("excluded")]
    for m in measurements:
        m["group_name"] = pid_to_group.get(m.get("participant_id",""), "미지정")
    output = io.StringIO()
    writer = SafeCSVWriter(output)
    writer.writerow(["변수","비교유형","그룹1","그룹2","N1","N2","평균1","평균2","t/U/F","p값","유의","Cohen's d","효과크기"])
    for v in (variables if isinstance(variables,list) else []):
        vn = v["name"]; label = v.get("label") or vn
        grp_vals = {}
        for m in measurements:
            val = m.get("data",{}).get(vn)
            if val is not None:
                try: grp_vals.setdefault(m["group_name"],[]).append(float(val))
                except: pass
        grp_list = list(grp_vals.keys())
        for i in range(len(grp_list)):
            for j in range(i+1, len(grp_list)):
                g1, g2 = grp_list[i], grp_list[j]
                a, b = grp_vals[g1], grp_vals[g2]
                if len(a) < 2 or len(b) < 2: continue
                try:
                    t_s, t_p = _scipy_stats.ttest_ind(a, b, equal_var=False)
                    n = len(a)+len(b)
                    ps = _np.sqrt(((len(a)-1)*_np.var(a,ddof=1) + (len(b)-1)*_np.var(b,ddof=1)) / (n-2)) if n > 2 else 0
                    d = (float(_np.mean(a))-float(_np.mean(b)))/float(ps) if ps > 0 else 0
                    eff = "large" if abs(d)>=0.8 else "medium" if abs(d)>=0.5 else "small"
                    writer.writerow([label,"독립표본t",g1,g2,len(a),len(b),round(float(_np.mean(a)),3),round(float(_np.mean(b)),3),round(float(t_s),3),round(float(t_p),4),"*" if t_p<0.05 else "",round(float(d),3),eff])
                except: pass
        if len(grp_list) >= 3:
            gdata = [grp_vals[g] for g in grp_list if len(grp_vals[g]) >= 2]
            if len(gdata) >= 3:
                try:
                    f_s, f_p = _scipy_stats.f_oneway(*gdata)
                    writer.writerow([label,"ANOVA","(전체)","",sum(len(g) for g in gdata),"",
                                     "","",round(float(f_s),3),round(float(f_p),4),"*" if f_p<0.05 else "","",""])
                except: pass
    output.seek(0)
    safe = proj_name.replace(" ","_")
    return Response(output.getvalue(), mimetype="text/csv;charset=utf-8-sig",
        headers={"Content-Disposition":f"attachment;filename={safe}_stats_{datetime.now().strftime('%Y%m%d')}.csv"})

@app.route("/portal/files")
@login_required
def portal_files():
    email = session["researcher"]
    files = sb("GET", "portal_files", params=f"?researcher_email=eq.{_q(email)}&order=uploaded_at.desc") or []
    projects = sb("GET", "projects", params=f"?researcher_email=eq.{_q(email)}&order=created_at.desc") or []
    # Build project name lookup
    proj_map = {p["id"]: p["name"] for p in (projects if isinstance(projects, list) else [])}
    for f in (files if isinstance(files, list) else []):
        f["project_name"] = proj_map.get(f.get("project_id"), "")
    return render_template("portal_files.html", files=files, projects=projects if isinstance(projects, list) else [], researcher=email)

@app.route("/portal/files/upload", methods=["POST"])
@login_required
def portal_files_upload():
    project_id = request.form.get("project_id") or None
    if project_id and not _project_access_ok(project_id, session["researcher"]):
        flash("이 프로젝트에 접근 권한이 없어요.", "error")
        return redirect(url_for("portal_files"))
    f = request.files.get("file")
    if not f or not f.filename:
        flash("파일을 선택해주세요.", "error")
        return redirect(url_for("portal_files"))
    original = f.filename
    safe = secure_filename(original)
    if not safe:
        flash("유효하지 않은 파일명입니다.", "error")
        return redirect(url_for("portal_files"))
    stored, size = _save_portal_upload(f)
    saved = sb("POST", "portal_files", data={
        "filename": stored, "original_name": original,
        "size": size, "researcher_email": session["researcher"],
        "project_id": project_id,
    })
    if not saved:
        raise StorageUnavailable('File metadata could not be saved; original retained')
    flash(f"'{original}' 업로드 완료")
    return redirect(url_for("portal_files"))

@app.route("/portal/files/<file_id>/download")
@login_required
def portal_files_download(file_id):
    rows = sb("GET", "portal_files", params=f"?id=eq.{_q(file_id)}")
    if not rows: return redirect(url_for("portal_files"))
    row = rows[0]
    if row.get("researcher_email") != session["researcher"]:
        flash("접근 권한이 없습니다.", "error"); return redirect(url_for("portal_files"))
    if row.get('project_id'):
        _authorize_project(row['project_id'])
    from flask import send_file
    if row['filename'].startswith('storage/'):
        content = _file_store().get(_portal_storage_key(row['filename']))
        if content is None: abort(404)
        return send_file(io.BytesIO(content), as_attachment=True, download_name=row['original_name'])
    path = _stored_path(PORTAL_FILES_DIR, row["filename"])
    if not os.path.exists(path) and row.get('project_id'):
        path = _stored_path(DATA_DIR, f"project_{row['project_id']}", row['filename'])
    if not os.path.exists(path): abort(404)
    return send_file(path, as_attachment=True, download_name=row["original_name"])

@app.route("/portal/files/<file_id>/delete", methods=["POST"])
@login_required
def portal_files_delete(file_id):
    rows = sb("GET", "portal_files", params=f"?id=eq.{_q(file_id)}")
    if rows and rows[0].get("researcher_email") == session["researcher"]:
        if rows[0].get('project_id'):
            _authorize_project(rows[0]['project_id'], owner_only=True)
        # Delete physical file
        remote = rows[0]['filename'].startswith('storage/')
        if remote:
            _file_store().delete(_portal_storage_key(rows[0]['filename']))
        path = _stored_path(DATA_DIR, rows[0]["filename"])
        if not remote and not os.path.exists(path):
            # also check project subfolder
            proj_id = rows[0].get("project_id","")
            path2 = _stored_path(DATA_DIR, f"project_{proj_id}", rows[0]["filename"])
            if os.path.exists(path2): path = path2
        if not remote and os.path.exists(path):
            try: os.remove(path)
            except: pass
        # Optionally cascade-delete measurements linked to this file
        if request.form.get("delete_measurements") == "1":
            sb("DELETE", "measurements", params=f"?source_file_id=eq.{_q(file_id)}")
        sb("DELETE", "portal_files", params=f"?id=eq.{_q(file_id)}")
        flash("파일이 삭제됐어요.")
    return redirect(url_for("portal_files"))

# ─── Google Drive (lab shared folders → portal, read-only live view) ─────────
_DEFAULT_DRIVE_FOLDERS = [
    "1TdiJdGppW8x_DpexRbxY0swSEf0NuYCw",
    "1e2cbXgiAaGRbM6_Z-BCT3EEpH577DXb7",
    "1-nF47M31C0DBbf9_P3-JKopn7P5bGEot",
    "1jNPjLHeEgkf4eb2JcbZ7nCdaR_VsruaN",
    "1ZIuZqQoQx7CaeQ0WVlN6ASdux9dj6qDR",
    "1tXWy8_7ctRG5-tsdLwD0SSe4SrqrTzUB",
    "1Qjqi-RnxuHXyNDLKUHbdM_yccFrBCsQA",
    "1mHpfo4qOlOJ3NfACSQVetC5-pXPw_OGq",
]
_env_folders = os.getenv("GOOGLE_DRIVE_FOLDER_IDS", "").strip()
DRIVE_FOLDER_IDS = [f.strip() for f in _env_folders.split(",") if f.strip()] if _env_folders else _DEFAULT_DRIVE_FOLDERS

@app.route("/portal/drive")
@login_required
def portal_drive():
    error, folders = None, []
    if not drive_sync.is_configured():
        error = ("Google Drive가 아직 연결되지 않았어요. "
                 "Render 환경변수 GOOGLE_SERVICE_ACCOUNT_JSON을 설정하면 자동으로 연결됩니다.")
    else:
        try:
            folders = drive_sync.list_all(DRIVE_FOLDER_IDS)
        except drive_sync.DriveNotConfigured as e:
            error = str(e)
        except Exception as e:
            error = "드라이브를 불러오지 못했어요. 관리자에게 연결 상태 확인을 요청해주세요."
    return render_template("portal_drive.html",
                           researcher=session.get("researcher", ""),
                           folders=folders, error=error)

@app.route("/portal/drive/file/<file_id>")
@login_required
def portal_drive_file(file_id):
    if not re.match(r"^[A-Za-z0-9_-]+$", file_id or ""):
        flash("잘못된 파일 ID예요.", "error")
        return redirect(url_for("portal_drive"))
    try:
        data, mime, name = drive_sync.get_file(file_id, DRIVE_FOLDER_IDS)
    except Exception as e:
        flash("파일을 불러올 수 없거나 접근 권한이 없습니다.", "error")
        return redirect(url_for("portal_drive"))
    from urllib.parse import quote
    resp = Response(data, mimetype=mime or "application/octet-stream")
    resp.headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(name)
    return resp

@app.route("/portal/settings")
@login_required
def portal_settings():
    return render_template("portal_settings.html", researcher=session["researcher"],
                           is_admin=_is_admin(), use_supabase=_USE_SUPABASE)

@app.route("/portal/settings/password", methods=["POST"])
@login_required
@limiter.limit('10 per minute', methods=['POST'])
def portal_change_password():
    current=request.form.get("current","").strip()
    new_pw=request.form.get("new_pw","").strip()
    confirm=request.form.get("confirm","").strip()
    email=session["researcher"]
    stored = _get_account(email)
    if not stored or not check_password_hash(stored, current): flash("현재 비밀번호가 틀렸어요.", "error"); return redirect(url_for("portal_settings"))
    if new_pw!=confirm: flash("새 비밀번호가 일치하지 않아요.", "error"); return redirect(url_for("portal_settings"))
    error = password_policy_error(new_pw, email)
    if error:
        flash(error, 'error')
        return redirect(url_for('portal_settings'))
    if check_password_hash(stored, new_pw):
        flash('현재와 다른 새 비밀번호를 사용해주세요.', 'error')
        return redirect(url_for('portal_settings'))
    _set_account(email, new_pw)
    sb('DELETE', 'portal_auth_sessions', params=f'?researcher_email=eq.{_q(email)}')
    session.clear()
    flash("비밀번호가 변경됐어요. 다시 로그인해주세요.")
    return redirect(url_for("login"))

@app.route("/portal/settings/add-account", methods=["POST"])
@login_required
def portal_add_account():
    new_email=request.form.get("email","").strip()
    new_pw=request.form.get("password","").strip()
    if not new_email or not new_pw: flash("이메일과 비밀번호를 입력해주세요.", "error"); return redirect(url_for("portal_settings"))
    error = password_policy_error(new_pw, new_email)
    if error:
        flash(error, 'error')
        return redirect(url_for('portal_settings'))
    if _account_exists(new_email): flash("이미 존재하는 계정이에요.", "error"); return redirect(url_for("portal_settings"))
    _set_account(new_email, new_pw)
    flash(f"{new_email} 계정이 추가됐어요.")
    return redirect(url_for("portal_settings"))

@app.route("/portal/settings/backup")
@login_required
def portal_db_backup():
    with sqlite3.connect(DB_PATH) as src, sqlite3.connect(':memory:') as snapshot:
        src.backup(snapshot)
        data = snapshot.serialize()
    return Response(data, mimetype='application/octet-stream',
                    headers={'Content-Disposition': 'attachment;filename=portal_backup.db'})

@app.route("/portal/settings/restore", methods=["POST"])
@login_required
def portal_db_restore():
    # Database restore is an offline administrative operation, not a web upload.
    abort(403)

@app.route("/portal/merge")
@login_required
def portal_merge():
    email=session["researcher"]
    projects=sb("GET","projects",params=f"?researcher_email=eq.{_q(email)}&order=created_at.desc") or []
    return render_template("portal_merge.html",researcher=email,
        projects=projects if isinstance(projects,list) else [])

@app.route("/portal/merge/preview", methods=["POST"])
@login_required
def portal_merge_preview():
    p1_id=request.form.get("project1_id",""); p2_id=request.form.get("project2_id","")
    if not p1_id or not p2_id or p1_id==p2_id: flash("서로 다른 프로젝트를 선택해주세요.", "error"); return redirect(url_for("portal_merge"))
    p1=sb("GET","projects",params=f"?id=eq.{_q(p1_id)}"); p2=sb("GET","projects",params=f"?id=eq.{_q(p2_id)}")
    p1_parts=sb("GET","project_participants",params=f"?project_id=eq.{_q(p1_id)}") or []
    p2_parts=sb("GET","project_participants",params=f"?project_id=eq.{_q(p2_id)}") or []
    p1_codes={p["code"] for p in (p1_parts if isinstance(p1_parts,list) else [])}
    p2_codes={p["code"] for p in (p2_parts if isinstance(p2_parts,list) else [])}
    return render_template("portal_merge.html",researcher=session["researcher"],
        projects=sb("GET","projects",params=f"?researcher_email=eq.{_q(session['researcher'])}&order=created_at.desc") or [],
        preview=True,project1=p1[0] if p1 else {},project2=p2[0] if p2 else {},
        common=sorted(p1_codes&p2_codes),only_p1=sorted(p1_codes-p2_codes),only_p2=sorted(p2_codes-p1_codes),
        p1_id=p1_id,p2_id=p2_id)

@app.route("/portal/merge/execute", methods=["POST"])
@login_required
def portal_merge_execute():
    p1_id=request.form.get("p1_id",""); p2_id=request.form.get("p2_id","")
    new_name=request.form.get("new_name","").strip() or "병합 프로젝트"
    new_proj=sb("POST","projects",data={"name":new_name,"description":f"병합: {p1_id[:8]}+{p2_id[:8]}",
        "researcher_email":session["researcher"]})
    if not new_proj or not isinstance(new_proj,list): flash("병합 실패", "error"); return redirect(url_for("portal_merge"))
    new_pid=new_proj[0]["id"]
    all_vars={}
    for src in [p1_id,p2_id]:
        for v in (sb("GET","project_variables",params=f"?project_id=eq.{_q(src)}") or []):
            all_vars[v["name"]]=v
    for vn,v in all_vars.items():
        sb("POST","project_variables",data={"project_id":new_pid,"name":vn,
            "label":v.get("label"),"var_type":v.get("var_type","number"),"unit":v.get("unit")})
    p_code_map={}
    for src in [p1_id,p2_id]:
        for p in (sb("GET","project_participants",params=f"?project_id=eq.{_q(src)}") or []):
            if p["code"] not in p_code_map:
                new_p=sb("POST","project_participants",data={"project_id":new_pid,"code":p["code"],
                    "gender":p.get("gender"),"age":p.get("age"),"group_name":p.get("group_name")})
                if new_p and isinstance(new_p,list): p_code_map[p["code"]]=new_p[0]["id"]
    for src_pid in [p1_id,p2_id]:
        src_parts=sb("GET","project_participants",params=f"?project_id=eq.{_q(src_pid)}") or []
        src_map={p["id"]:p["code"] for p in (src_parts if isinstance(src_parts,list) else [])}
        tag="P1" if src_pid==p1_id else "P2"
        for m in (sb("GET","measurements",params=f"?project_id=eq.{_q(src_pid)}") or []):
            code=src_map.get(m.get("participant_id",""),"")
            new_pid2=p_code_map.get(code)
            if not new_pid2: continue
            sb("POST","measurements",data={"project_id":new_pid,"participant_id":new_pid2,
                "phase":f"[{tag}] {m.get('phase','')}" if m.get("phase") else tag,
                "notes":m.get("notes"),"data":m.get("data",{})})
    flash(f"병합 완료! '{new_name}' 프로젝트가 생성됐어요.")
    return redirect(url_for("portal_project", project_id=new_pid))

@app.route("/portal/all/participants")
@login_required
def portal_all_participants():
    participants=_project_rows("project_participants", params="?order=enrolled_at.desc") or []
    return render_template("portal_participants.html",researcher=session["researcher"],
        participants=participants if isinstance(participants,list) else [])

@app.route("/portal/all/sessions")
@login_required
def portal_all_sessions():
    session_list = _project_rows("sessions", params="?order=received_at.desc") or []
    if session_list:
        all_parts = _project_rows("project_participants", params="?select=id,code") or []
        p_map = {p["id"]: p["code"] for p in (all_parts if isinstance(all_parts, list) else [])}
        for s in session_list:
            s["participant_code"] = p_map.get(s.get("participant_id", ""), "")
    return render_template("portal_sessions.html", researcher=session["researcher"],
        sessions=session_list)

def _batch_response(total, saved, invalid, failed):
    """Shared response shape for the /api/* batch-ingest endpoints below.
    ok is true only if nothing failed to *save* (invalid input doesn't count
    against it, since retrying invalid input wouldn't help)."""
    code = 201 if failed == 0 else (500 if failed == total else 207)
    return jsonify({"ok": failed == 0, "total": total, "saved": saved,
                     "invalid": invalid, "failed": failed}), code

@app.route("/api/sessions", methods=["POST"])
@csrf.exempt
def api_receive_session():
    if request.headers.get("X-API-Key","")!=APP_API_KEY:
        return jsonify({"error":"Unauthorized"}),401
    payload = request.get_json(silent=True) or {}
    sid = str(_uuid.uuid4())
    result = sb("POST", "sessions", data={
        "id":             sid,
        "project_id":     payload.get("project_id"),
        "participant_id": payload.get("participant_id"),
        "phase":          payload.get("phase", ""),
        "notes":          payload.get("notes", ""),
        "data":           payload.get("data", {}),
    })
    if not result:
        return jsonify({"ok": False, "error": "save failed"}), 500
    return jsonify({"ok": True, "id": sid}), 201

# ── Wardy 앱 이벤트 수신 API ──────────────────────────
@app.route("/api/wardy/events", methods=["POST"])
@csrf.exempt
def api_wardy_events():
    if request.headers.get("X-API-Key", "") != APP_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "No JSON body"}), 400

    # 단건 또는 배치(리스트) 모두 허용
    events = body if isinstance(body, list) else [body]
    saved = invalid = failed = 0
    for ev in events:
        if not ev.get("state") or not ev.get("user_name"):
            invalid += 1
            continue
        result = sb("POST", "wardy_events", data={
            "project_id":  ev.get("project_id") or None,
            "user_name":   str(ev.get("user_name", "")).strip(),
            "seq":         ev.get("seq") or ev.get("order"),
            "event_time":  ev.get("time") or ev.get("event_time"),
            "state":       str(ev.get("state", "")).lower().strip(),
            "game_level":  ev.get("gameLevel") or ev.get("game_level"),
            "data":        ev.get("data") or {},
        })
        saved += 1 if result else 0
        failed += 0 if result else 1
    return _batch_response(len(events), saved, invalid, failed)

# ── NeuroBreeze API ────────────────────────────────────
@app.route("/api/neurobreeze/eeg", methods=["POST"])
@csrf.exempt
def api_neurobreeze_eeg():
    if request.headers.get("X-API-Key","") != APP_API_KEY:
        return jsonify({"error":"Unauthorized"}), 401
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error":"No JSON body"}), 400
    records = body if isinstance(body, list) else [body]
    saved = invalid = failed = 0
    for r in records:
        if not r.get("user_name"):
            invalid += 1
            continue
        sid = str(_uuid.uuid4())
        result = sb("POST", "neurobreeze_eeg", data={
            "id":                    sid,
            "project_id":            r.get("project_id") or None,
            "user_name":             str(r.get("user_name","")).strip(),
            "session_num":           r.get("session_num") or r.get("회차"),
            "recorded_at":           r.get("recorded_at") or r.get("기록시간"),
            "left_alpha_power":      r.get("leftAlphaPowerSpectrum"),
            "right_alpha_power":     r.get("rightAlphaPowerSpectrum"),
            "alpha_power":           r.get("AlphaPowerSpectrum"),
            "left_alpha_indicator":  r.get("leftAlphaIndicatorValue"),
            "right_alpha_indicator": r.get("rightAlphaIndicatorValue"),
            "alpha_indicator":       r.get("AlphaIndicatorValue"),
        })
        saved += 1 if result else 0
        failed += 0 if result else 1
    return _batch_response(len(records), saved, invalid, failed)

@app.route("/api/neurobreeze/meditation", methods=["POST"])
@csrf.exempt
def api_neurobreeze_meditation():
    if request.headers.get("X-API-Key","") != APP_API_KEY:
        return jsonify({"error":"Unauthorized"}), 401
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error":"No JSON body"}), 400
    records = body if isinstance(body, list) else [body]
    saved = invalid = failed = 0
    for r in records:
        if not r.get("user_name"):
            invalid += 1
            continue
        sid = str(_uuid.uuid4())
        result = sb("POST", "neurobreeze_meditation", data={
            "id":                        sid,
            "project_id":                r.get("project_id") or None,
            "user_name":                 str(r.get("user_name","")).strip(),
            "session_num":               r.get("session_num") or r.get("회차"),
            "start_time":                r.get("start_time") or r.get("시작시간"),
            "end_time":                  r.get("end_time") or r.get("종료시간"),
            "feedback_count":            r.get("feedback_count") or r.get("피드백"),
            "breath_mode":               r.get("breath_mode") or r.get("호흡모드"),
            "left_alpha_power":          r.get("leftAlphaPowerSpectrum"),
            "right_alpha_power":         r.get("rightAlphaPowerSpectrum"),
            "alpha_power":               r.get("AlphaPowerSpectrum"),
            "left_alpha_indicator":      r.get("leftAlphaIndicatorValue"),
            "right_alpha_indicator":     r.get("rightAlphaIndicatorValue"),
            "alpha_indicator":           r.get("AlphaIndicatorValue"),
            "start_alpha_power_avg":     r.get("Start_Alpha_Spectrum_Avg"),
            "end_alpha_power_avg":       r.get("End_Alpha_Spectrum_Avg"),
            "start_alpha_indicator_avg": r.get("Start_Alpha_Indicator_Avg"),
            "end_alpha_indicator_avg":   r.get("End_Alpha_Indicator_Avg"),
            "alpha_power_increase":      r.get("Alpha_Spec_Increase"),
            "alpha_indicator_increase":  r.get("Alpha_indi_Increase"),
            "alpha_progress_rate":       r.get("Alpha_Progress_Rate"),
        })
        saved += 1 if result else 0
        failed += 0 if result else 1
    return _batch_response(len(records), saved, invalid, failed)

# ── Mind Mate 수신 API ────────────────────────────────
# Unity 앱이 Google Apps Script 웹앱을 거쳐 이 엔드포인트로 릴레이한다.
# 공통 필드(UserName/Department/PhoneNumber)는 개인정보라 그대로 저장하지
# 않고, PhoneNumber를 HMAC(pepper)으로 해시한 participant_key로만 남긴다.
# (전화번호를 신원 기준으로 쓰는 이유: 이름/부서는 동명이인·부서이동으로
# 흔들리지만 번호는 한 사람에게 안정적으로 붙어있다는 전제.)
_MINDMATE_ID_NS = _uuid.UUID("f2b6f0a0-6c9e-4f7c-9a2e-2f6b6c1f9a10")

def _participant_key(phone_number):
    if not isinstance(phone_number, str) or not re.fullmatch(r"[0-9+() .\-]{7,40}", phone_number):
        raise ValueError("invalid PhoneNumber")
    digits = re.sub(r"\D", "", phone_number)
    if not 7 <= len(digits) <= 15:
        raise ValueError("invalid PhoneNumber")
    return hmac.new(MINDMATE_PEPPER.encode(), digits.encode(), hashlib.sha256).hexdigest()

def _deterministic_id(*parts):
    return str(_uuid.uuid5(_MINDMATE_ID_NS, "|".join(str(p) for p in parts)))

_MINDMATE_FIELDS = {
    "writeData_constant": {"Date", "StartTime", "EndTime", "MusicName", "MusicType"},
    "updateData_daily": {
        "Date", "WorkType", "IsServeyDaily", "IsMindHealing", "Servey_MindHealing_1",
        "IsFiveDays", "Servey_Five_Result",
        *(f"Servey_Daily_{i}" for i in range(1, 4)),
        *(f"Servey_Five_{i}" for i in range(1, 13)),
    },
    "updateData_daily_json": {"jsonData"},
    "appOpen": set(),
}
_MINDMATE_BATCH_FIELDS = {"dataCount", "date", "workType", "isServeyDaily", "isMindHealing", "isFiveDays"}

def _mindmate_allowed_fields(payload, allowed):
    selected = {k: v for k, v in payload.items() if k in allowed}
    if any(v is not None and not isinstance(v, (str, int, float, bool)) for v in selected.values()):
        raise ValueError("fields must be scalar values")
    return selected

def _strip_pii(payload):
    # Keep only the documented fields, including inside the JSON-encoded batch.
    selected = _mindmate_allowed_fields(payload, _MINDMATE_FIELDS[payload["order"]])
    if payload["order"] == "updateData_daily_json":
        batch = json.loads(selected.get("jsonData", "{}"))
        if not isinstance(batch, dict):
            raise ValueError("invalid jsonData")
        selected["jsonData"] = json.dumps(_mindmate_allowed_fields(batch, _MINDMATE_BATCH_FIELDS), ensure_ascii=False)
    return selected

def _mindmate_payload():
    """form-encoded와 JSON body 둘 다 허용."""
    if request.form:
        return request.form.to_dict()
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}

def _merge_upsert(table, row_id, fields, overwrite_always=("raw", "updated_at", "source")):
    """부분 전송을 반영: 새 값이 비어있으면 기존 값을 유지한 채 upsert.
    raw/updated_at은 항상 최신 값으로 덮어씀."""
    existing = sb("GET", table, params=f"?id=eq.{_q(row_id)}&select=*")
    merged = dict(existing[0]) if existing else {}
    for k, v in fields.items():
        if k in overwrite_always or v not in (None, ""):
            merged[k] = v
    merged["id"] = row_id
    merged["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    return sb("POST", table, data=merged, upsert=True)

@app.route("/api/mindmate/followup", methods=["POST"])
@csrf.exempt
def api_mindmate_followup():
    if request.headers.get("X-API-Key","") != APP_API_KEY:
        return jsonify({"error":"Unauthorized"}), 401

    payload = _mindmate_payload()
    order = payload.get("order", "")
    if not isinstance(order, str) or order not in _MINDMATE_FIELDS:
        return jsonify(error="unknown order"), 400
    phone = payload.get("PhoneNumber", "")
    if not phone:
        return jsonify({"error": "PhoneNumber required"}), 400
    try:
        pkey = _participant_key(phone)
        payload = _strip_pii(payload)
    except (ValueError, TypeError):
        return jsonify(error="invalid PhoneNumber or payload fields"), 400
    raw = json.dumps(payload, ensure_ascii=False)

    if order == "writeData_constant":
        date, start_time = payload.get("Date", ""), payload.get("StartTime", "")
        if not date or not start_time:
            return jsonify({"error": "Date/StartTime required"}), 400
        row_id = _deterministic_id("music", pkey, date, start_time)
        result = _merge_upsert("mindmate_music_log", row_id, {
            "participant_key": pkey, "date": date, "start_time": start_time,
            "end_time":  payload.get("EndTime", ""),
            "music_name": payload.get("MusicName", ""),
            "music_type": payload.get("MusicType", ""),
            "raw": raw,
        })
        return jsonify({"ok": bool(result), "id": row_id}), (201 if result else 500)

    elif order == "updateData_daily":
        date = payload.get("Date", "")
        if not date:
            return jsonify({"error": "Date required"}), 400
        row_id = _deterministic_id("daily", pkey, date)
        fields = {
            "participant_key": pkey, "date": date,
            "work_type":            payload.get("WorkType", ""),
            "is_survey_daily":      payload.get("IsServeyDaily", ""),
            "servey_daily_1":       payload.get("Servey_Daily_1", ""),
            "servey_daily_2":       payload.get("Servey_Daily_2", ""),
            "servey_daily_3":       payload.get("Servey_Daily_3", ""),
            "is_mind_healing":      payload.get("IsMindHealing", ""),
            "servey_mindhealing_1": payload.get("Servey_MindHealing_1", ""),
            "is_five_days":         payload.get("IsFiveDays", ""),
            "servey_five_result":   payload.get("Servey_Five_Result", ""),
            "source": "updateData_daily",
            "raw": raw,
        }
        for i in range(1, 13):
            fields[f"servey_five_{i}"] = payload.get(f"Servey_Five_{i}", "")
        result = _merge_upsert("mindmate_daily_survey", row_id, fields)
        return jsonify({"ok": bool(result), "id": row_id}), (201 if result else 500)

    elif order == "updateData_daily_json":
        try:
            j = json.loads(payload.get("jsonData", "{}"))
        except (ValueError, TypeError):
            return jsonify({"error": "invalid jsonData"}), 400
        if not isinstance(j, dict):
            return jsonify(error='invalid jsonData'), 400
        dates      = str(j.get("date", "")).split("|")
        work_types = str(j.get("workType", "")).split("|")
        is_daily   = str(j.get("isServeyDaily", "")).split("|")
        is_healing = str(j.get("isMindHealing", "")).split("|")
        is_five    = str(j.get("isFiveDays", "")).split("|")
        try:
            n = int(j.get("dataCount") or len(dates))
        except (TypeError, ValueError):
            return jsonify(error='invalid dataCount'), 400
        if not 1 <= n <= 500:
            return jsonify(error='dataCount must be 1..500'), 400
        saved = invalid = failed = 0
        for i in range(n):
            date = dates[i] if i < len(dates) else ""
            if not date:
                invalid += 1
                continue
            row_id = _deterministic_id("daily", pkey, date)
            # 이 배치 갱신에는 실제 설문 답변이 없고 상태 플래그만 들어있음 —
            # 답변 컬럼은 건드리지 않고(비워둠) _merge_upsert가 기존 값을 지킨다.
            result = _merge_upsert("mindmate_daily_survey", row_id, {
                "participant_key": pkey, "date": date,
                "work_type":       work_types[i] if i < len(work_types) else "",
                "is_survey_daily": is_daily[i]   if i < len(is_daily)   else "",
                "is_mind_healing": is_healing[i] if i < len(is_healing) else "",
                "is_five_days":    is_five[i]    if i < len(is_five)    else "",
                "source": "updateData_daily_json",
                "raw": raw,
            })
            saved += 1 if result else 0
            failed += 0 if result else 1
        return _batch_response(n, saved, invalid, failed)

    elif order == "appOpen":
        # 앱을 열기만 해도 보내는 신호(아직 Unity 쪽엔 없음, 추가 예정).
        # 하루 1건으로 합치지 않고 매번 새 행 — "지금 누가 쓰고 있는지"엔
        # 열어본 횟수/시점 자체가 의미 있어서 병합-upsert 대상이 아님.
        result = sb("POST", "mindmate_app_open", data={
            "participant_key": pkey,
            "raw": raw,
        })
        return jsonify({"ok": bool(result)}), (201 if result else 500)

    else:
        return jsonify({"error": f"unknown order: {order}"}), 400

# ── Wardy 이벤트 뷰어 ─────────────────────────────────
@app.route("/portal/wardy")
@login_required
def portal_wardy():
    project_id = request.args.get("project_id", "").strip()
    user_name  = request.args.get("user_name", "").strip()
    state      = request.args.get("state", "").strip()

    params = "?select=*&order=event_time.asc"
    if project_id: params += f"&project_id=eq.{_q(project_id)}"
    if user_name:  params += f"&user_name=ilike.{_q(user_name)}%25"
    if state:      params += f"&state=ilike.{_q(state)}"
    params += "&limit=3000"
    all_events = _project_rows("wardy_events", params=params) or []
    total = len(all_events)

    # 참여자별로 그룹핑 (가장 최근 이벤트 순 정렬)
    from collections import OrderedDict
    groups = OrderedDict()
    for ev in all_events:
        key = ev.get("user_name") or "Unknown"
        if key not in groups:
            groups[key] = []
        groups[key].append(ev)

    grouped_users = []
    for uname, evs in groups.items():
        times = [e.get("event_time") or e.get("received_at") or "" for e in evs]
        times = [t for t in times if t]
        grouped_users.append({
            "user_name": uname,
            "events":    evs,
            "count":     len(evs),
            "first_time": min(times)[:16].replace("T"," ") if times else "",
            "last_time":  max(times)[:16].replace("T"," ") if times else "",
            "states":    sorted(set(e.get("state","") for e in evs if e.get("state"))),
        })
    # 최근 이벤트가 있는 참여자 먼저
    grouped_users.sort(key=lambda g: g["last_time"], reverse=True)

    distinct_states = sorted(set(e.get("state", "") for e in all_events if e.get("state")))
    projects = _accessible_projects(session["researcher"]) or []

    return render_template("portal_wardy.html",
        researcher=session["researcher"],
        grouped_users=grouped_users, projects=projects,
        project_id=project_id, user_name=user_name, state=state,
        total=total,
        distinct_states=distinct_states,
        api_key="(서버 환경변수에서 별도 관리)")

@app.route("/portal/wardy/delete", methods=["POST"])
@login_required
def portal_wardy_delete():
    ids = request.form.getlist("ids")
    if not ids:
        flash("삭제할 항목을 선택해주세요.", "error")
        return redirect(url_for("portal_wardy"))
    return _delete_owned_rows("wardy_events", ids, "portal_wardy")

@app.route("/portal/wardy/export")
@login_required
def portal_wardy_export():
    project_id = request.args.get("project_id", "").strip()
    user_name  = request.args.get("user_name", "").strip()
    state      = request.args.get("state", "").strip()

    params = "?select=*&order=received_at.asc"
    if project_id: params += f"&project_id=eq.{_q(project_id)}"
    if user_name:  params += f"&user_name=ilike.{_q(user_name)}%25"
    if state:      params += f"&state=ilike.{_q(state)}"
    events = _project_rows("wardy_events", params=params) or []

    buf = io.StringIO()
    w = SafeCSVWriter(buf)
    w.writerow(["id","project_id","user_name","seq","event_time","state","game_level","data","received_at"])
    for ev in events:
        w.writerow([
            ev.get("id",""), ev.get("project_id",""), ev.get("user_name",""),
            ev.get("seq",""), ev.get("event_time",""), ev.get("state",""),
            ev.get("game_level",""), json.dumps(ev.get("data") or {}, ensure_ascii=False),
            ev.get("received_at",""),
        ])
    buf.seek(0)
    return Response(buf.read(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=wardy_events.csv"})

# ── NeuroBreeze 뷰어 ──────────────────────────────────
def _nb_group_by_user(rows):
    """neurobreeze 레코드를 user_name 기준으로 그룹핑, 최근 순 정렬."""
    from collections import OrderedDict
    groups = OrderedDict()
    for r in rows:
        key = r.get("user_name") or "Unknown"
        if key not in groups:
            groups[key] = []
        groups[key].append(r)
    result = []
    for uname, recs in groups.items():
        times = [x.get("received_at") or "" for x in recs]
        times = [t for t in times if t]
        result.append({
            "user_name":  uname,
            "records":    recs,
            "count":      len(recs),
            "last_time":  max(times)[:16].replace("T"," ") if times else "",
        })
    result.sort(key=lambda g: g["last_time"], reverse=True)
    return result

@app.route("/portal/neurobreeze")
@login_required
def portal_neurobreeze():
    project_id = request.args.get("project_id","").strip()
    user_name  = request.args.get("user_name","").strip()
    tab        = request.args.get("tab","eeg")  # eeg | meditation

    base_params = "?select=*&order=received_at.asc&limit=3000"
    if project_id: base_params += f"&project_id=eq.{_q(project_id)}"
    if user_name:  base_params += f"&user_name=ilike.{_q(user_name)}%25"
    eeg_rows        = _project_rows("neurobreeze_eeg", params=base_params) or []
    meditation_rows = _project_rows("neurobreeze_meditation", params=base_params) or []

    projects = _accessible_projects(session["researcher"]) or []

    return render_template("portal_neurobreeze.html",
        researcher=session["researcher"],
        eeg_groups=_nb_group_by_user(eeg_rows),
        med_groups=_nb_group_by_user(meditation_rows),
        eeg_total=len(eeg_rows), med_total=len(meditation_rows),
        projects=projects,
        project_id=project_id, user_name=user_name, tab=tab,
        api_key="(서버 환경변수에서 별도 관리)")

@app.route("/portal/neurobreeze/eeg/delete", methods=["POST"])
@login_required
def portal_neurobreeze_eeg_delete():
    ids = request.form.getlist("ids")
    if not ids:
        flash("삭제할 항목을 선택해주세요.", "error")
        return redirect(url_for("portal_neurobreeze", tab="eeg"))
    return _delete_owned_rows("neurobreeze_eeg", ids, "portal_neurobreeze", tab="eeg")

@app.route("/portal/neurobreeze/meditation/delete", methods=["POST"])
@login_required
def portal_neurobreeze_meditation_delete():
    ids = request.form.getlist("ids")
    if not ids:
        flash("삭제할 항목을 선택해주세요.", "error")
        return redirect(url_for("portal_neurobreeze", tab="meditation"))
    return _delete_owned_rows("neurobreeze_meditation", ids, "portal_neurobreeze", tab="meditation")

@app.route("/portal/neurobreeze/export")
@login_required
def portal_neurobreeze_export():
    project_id = request.args.get("project_id","").strip()
    user_name  = request.args.get("user_name","").strip()
    tab        = request.args.get("tab","eeg")

    table = "neurobreeze_eeg" if tab == "eeg" else "neurobreeze_meditation"
    params = "?select=*&order=received_at.asc"
    if project_id: params += f"&project_id=eq.{_q(project_id)}"
    if user_name:  params += f"&user_name=ilike.{_q(user_name)}%25"
    rows = _project_rows(table, params=params) or []

    buf = io.StringIO()
    w = SafeCSVWriter(buf)

    if tab == "eeg":
        w.writerow(["id","project_id","user_name","session_num","recorded_at",
                    "left_alpha_power","right_alpha_power","alpha_power",
                    "left_alpha_indicator","right_alpha_indicator","alpha_indicator","received_at"])
        for r in rows:
            w.writerow([r.get(c,"") for c in [
                "id","project_id","user_name","session_num","recorded_at",
                "left_alpha_power","right_alpha_power","alpha_power",
                "left_alpha_indicator","right_alpha_indicator","alpha_indicator","received_at"]])
        fname = "neurobreeze_eeg.csv"
    else:
        w.writerow(["id","project_id","user_name","session_num","start_time","end_time",
                    "feedback_count","breath_mode","alpha_power","alpha_indicator",
                    "start_alpha_power_avg","end_alpha_power_avg",
                    "start_alpha_indicator_avg","end_alpha_indicator_avg",
                    "alpha_power_increase","alpha_indicator_increase","alpha_progress_rate","received_at"])
        for r in rows:
            w.writerow([r.get(c,"") for c in [
                "id","project_id","user_name","session_num","start_time","end_time",
                "feedback_count","breath_mode","alpha_power","alpha_indicator",
                "start_alpha_power_avg","end_alpha_power_avg",
                "start_alpha_indicator_avg","end_alpha_indicator_avg",
                "alpha_power_increase","alpha_indicator_increase","alpha_progress_rate","received_at"]])
        fname = "neurobreeze_meditation.csv"

    buf.seek(0)
    return Response(buf.read(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={fname}"})

# ── Mind Mate 뷰어 (음원 재생 로그 / 일일 설문) ────────
_MM_SURVEY_COLS = (
    ["id","participant_key","date","work_type","is_survey_daily",
     "servey_daily_1","servey_daily_2","servey_daily_3",
     "is_mind_healing","servey_mindhealing_1","is_five_days"]
    + [f"servey_five_{i}" for i in range(1,13)]
    + ["servey_five_result","raw","created_at","updated_at"]
)
_MM_MUSIC_COLS = ["id","participant_key","date","start_time","end_time",
                   "music_name","music_type","raw","created_at","updated_at"]

def _mm_label(participant_key):
    return "참여자-" + (participant_key or "")[:8].upper()

@app.route("/portal/mindmate")
@login_required
def portal_mindmate():
    tab = request.args.get("tab", "survey")  # survey | music | live
    q   = request.args.get("q", "").strip()  # participant_key 앞부분 검색

    if tab == "live":
        return render_template("portal_mindmate.html",
            researcher=session["researcher"], tab=tab, q=q, rows=[], total=0)

    table = "mindmate_music_log" if tab == "music" else "mindmate_daily_survey"
    params = "?select=*&order=date.desc&limit=3000"
    if q: params += f"&participant_key=ilike.{_q(q)}%25"
    rows = sb("GET", table, params=params) or []
    for r in rows:
        r["label"] = _mm_label(r.get("participant_key"))

    return render_template("portal_mindmate.html",
        researcher=session["researcher"], tab=tab, q=q, rows=rows, total=len(rows))

@app.route("/portal/mindmate/music/delete", methods=["POST"])
@login_required
def portal_mindmate_music_delete():
    ids = request.form.getlist("ids")
    if not ids:
        flash("삭제할 항목을 선택해주세요.", "error")
        return redirect(url_for("portal_mindmate", tab="music"))
    for rid in ids:
        sb("DELETE", "mindmate_music_log", params=f"?id=eq.{_q(rid)}")
    flash(f"{len(ids)}건 삭제됐습니다.")
    return redirect(url_for("portal_mindmate", tab="music"))

@app.route("/portal/mindmate/survey/delete", methods=["POST"])
@login_required
def portal_mindmate_survey_delete():
    ids = request.form.getlist("ids")
    if not ids:
        flash("삭제할 항목을 선택해주세요.", "error")
        return redirect(url_for("portal_mindmate", tab="survey"))
    for rid in ids:
        sb("DELETE", "mindmate_daily_survey", params=f"?id=eq.{_q(rid)}")
    flash(f"{len(ids)}건 삭제됐습니다.")
    return redirect(url_for("portal_mindmate", tab="survey"))

@app.route("/portal/mindmate/export")
@login_required
def portal_mindmate_export():
    tab = request.args.get("tab", "survey")
    if tab == "music":
        table, cols, fname = "mindmate_music_log", _MM_MUSIC_COLS, "mindmate_music_log.csv"
    else:
        table, cols, fname = "mindmate_daily_survey", _MM_SURVEY_COLS, "mindmate_daily_survey.csv"
    rows = sb("GET", table, params="?select=*&order=date.desc") or []

    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM: 엑셀에서 바로 열 수 있게
    w = SafeCSVWriter(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c,"") for c in cols])
    buf.seek(0)
    return Response(buf.read(), mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment;filename={fname}"})

# ── Mind Mate 실시간 뷰어 ─────────────────────────────
# 두 테이블(음원 재생 / 일일 설문·동기화)의 최근 활동을 시간순으로 합쳐서
# "지금 누가 쓰고 있는지" 보여준다. 프로젝트 연결 전까지 지정된 관리자만 접근.
_MM_ACTIVITY_TYPES = {
    "music":  "🎵 음원 재생",
    "survey": "📋 일일 설문",
    "sync":   "🔄 동기화",
    "open":   "📱 앱 접속",
}

@app.route("/portal/mindmate/live")
@login_required
def portal_mindmate_live():
    return redirect(url_for("portal_mindmate", tab="live"))

@app.route("/portal/mindmate/live/data")
@csrf.exempt
@login_required
def portal_mindmate_live_data():
    music  = sb("GET","mindmate_music_log",
        params="?select=id,participant_key,date,music_name,updated_at&order=updated_at.desc&limit=50") or []
    survey = sb("GET","mindmate_daily_survey",
        params="?select=id,participant_key,date,work_type,source,updated_at&order=updated_at.desc&limit=50") or []
    opens  = sb("GET","mindmate_app_open",
        params="?select=id,participant_key,received_at&order=received_at.desc&limit=50") or []

    events = []
    for r in music:
        events.append({
            "id": r.get("id"), "type": "music", "label": _MM_ACTIVITY_TYPES["music"],
            "participant": _mm_label(r.get("participant_key")),
            "date": r.get("date"), "detail": r.get("music_name") or "",
            "at": r.get("updated_at"),
        })
    for r in survey:
        kind = "sync" if r.get("source") == "updateData_daily_json" else "survey"
        events.append({
            "id": r.get("id"), "type": kind, "label": _MM_ACTIVITY_TYPES[kind],
            "participant": _mm_label(r.get("participant_key")),
            "date": r.get("date"), "detail": r.get("work_type") or "",
            "at": r.get("updated_at"),
        })
    for r in opens:
        events.append({
            "id": r.get("id"), "type": "open", "label": _MM_ACTIVITY_TYPES["open"],
            "participant": _mm_label(r.get("participant_key")),
            "date": (r.get("received_at") or "")[:10], "detail": "",
            "at": r.get("received_at"),
        })
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return jsonify({"ok": True, "events": events[:50]})

# ── Project meta edit ────────────────────────────────
@app.route("/portal/projects/<project_id>/edit", methods=["POST"])
@login_required
def portal_project_edit(project_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    new_name = request.form.get("name","").strip()
    sb("PATCH", "projects",
       data={"name": new_name,
             "description": request.form.get("description","").strip(),
             "app_type": request.form.get("app_type","").strip()},
       params=f"?id=eq.{_q(project_id)}")
    audit("edit_project", "projects", project_id, f"name={new_name}")
    flash("프로젝트 정보가 수정됐어요.")
    return redirect(url_for("portal_project", project_id=project_id))

# ── Project clone ─────────────────────────────────────
@app.route("/portal/projects/<project_id>/clone", methods=["POST"])
@login_required
def portal_project_clone(project_id):
    proj, err = _require_project_owner(project_id)
    if err: return err
    variables = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    new_id = str(_uuid.uuid4())
    sb("POST","projects",data={
        "id": new_id,
        "name": proj["name"] + " (복사)",
        "description": proj.get("description",""),
        "app_type": proj.get("app_type",""),
        "researcher_email": session["researcher"],
    })
    for v in (variables if isinstance(variables,list) else []):
        sb("POST","project_variables",data={
            "project_id": new_id,
            "name": v["name"], "label": v.get("label"), "var_type": v.get("var_type","number"), "unit": v.get("unit")
        })
    flash(f"'{proj['name']}' 프로젝트가 복사됐어요. (변수 설정만 복사, 데이터 제외)")
    return redirect(url_for("portal_project", project_id=new_id))

# ── Excel export ──────────────────────────────────────
@app.route("/portal/projects/<project_id>/export/excel")
@login_required
def portal_project_export_excel(project_id):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    proj, err = _require_project_owner(project_id)
    if err: return err
    participants = sb("GET","project_participants",params=f"?project_id=eq.{_q(project_id)}") or []
    measurements = sb("GET","measurements",params=f"?project_id=eq.{_q(project_id)}") or []
    variables    = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    p_map = {p["id"]: p for p in (participants if isinstance(participants,list) else [])}
    var_names = [v["name"] for v in (variables if isinstance(variables,list) else [])]
    var_labels = {v["name"]: (v.get("label") or v["name"]) for v in (variables if isinstance(variables,list) else [])}

    wb = openpyxl.Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="0F6B6B")

    def style_header(ws, headers):
        for i, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=i, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
            ws.column_dimensions[cell.column_letter].width = max(12, len(h)+2)

    # Sheet 1: All data
    ws1 = wb.active; ws1.title = "전체 데이터"
    hdrs = ["참여자코드","그룹","성별","나이","회차","측정일","메모"] + [var_labels.get(n,n) for n in var_names]
    style_header(ws1, hdrs)
    for m in (measurements if isinstance(measurements,list) else []):
        if m.get("excluded"): continue
        p = p_map.get(m.get("participant_id",""), {})
        row = [p.get("code",""), p.get("group_name",""), p.get("gender",""), p.get("age",""),
               m.get("phase",""), m.get("measured_at","")[:10], m.get("notes","")]
        for n in var_names:
            val = m.get("data",{}).get(n) if m.get("data") else None
            row.append(val)
        ws1.append(row)

    # Sheet 2: Group x Phase means
    ws2 = wb.create_sheet("그룹×회차 평균")
    phases = sorted({m.get("phase","") for m in (measurements if isinstance(measurements,list) else []) if m.get("phase") and not m.get("excluded")})
    groups = sorted({p.get("group_name","미지정") for p in (participants if isinstance(participants,list) else [])})
    s2_hdrs = ["변수","그룹"] + phases
    style_header(ws2, s2_hdrs)
    for vn in var_names:
        for grp in groups:
            row = [var_labels.get(vn,vn), grp]
            for ph in phases:
                vals = []
                for m in (measurements if isinstance(measurements,list) else []):
                    if m.get("excluded"): continue
                    p = p_map.get(m.get("participant_id",""),{})
                    if (p.get("group_name") or "미지정") == grp and m.get("phase") == ph:
                        v = m.get("data",{}).get(vn) if m.get("data") else None
                        if v is not None:
                            try: vals.append(float(v))
                            except: pass
                row.append(round(sum(vals)/len(vals),3) if vals else "")
            ws2.append(row)

    buf = io.BytesIO()
    neutralize_excel_formulas(wb)
    wb.save(buf); buf.seek(0)
    proj_name = proj["name"].replace("/","_").replace("\\","_")
    return Response(buf.read(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={proj_name}.xlsx"})

# ── Measurement edit ──────────────────────────────────
@app.route("/portal/projects/<project_id>/measurements/<m_id>/edit", methods=["POST"])
@login_required
def portal_measurement_edit(project_id, m_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    rows = sb("GET","measurements",params=f"?id=eq.{_q(m_id)}")
    if not rows: return redirect(url_for("portal_project", project_id=project_id))
    variables = sb("GET","project_variables",params=f"?project_id=eq.{_q(project_id)}") or []
    data_dict = {}
    if isinstance(variables, list):
        for v in variables:
            val = request.form.get(f"var_{v['name']}", "").strip()
            if val:
                try: data_dict[v['name']] = float(val)
                except: data_dict[v['name']] = val
    sb("PATCH","measurements",
       data={"phase": request.form.get("phase","").strip() or None,
             "notes": request.form.get("notes","").strip() or None,
             "data": data_dict},
       params=f"?id=eq.{_q(m_id)}")
    return redirect(url_for("portal_project", project_id=project_id, _anchor="tab-measurements"))

# ── Participant bulk group change ─────────────────────
@app.route("/portal/projects/<project_id>/participants/bulk-group", methods=["POST"])
@login_required
def portal_participants_bulk_group(project_id):
    _, err = _require_project_owner(project_id)
    if err: return err
    ids = request.form.getlist("participant_ids")
    group_name = request.form.get("group_name","").strip() or None
    for pid in ids:
        sb("PATCH","project_participants",data={"group_name": group_name},
           params=f"?id=eq.{_q(pid)}&project_id=eq.{_q(project_id)}")
    flash(f"{len(ids)}명의 그룹이 변경됐어요.")
    return redirect(url_for("portal_project", project_id=project_id, _anchor="tab-participants"))

# ── News management ───────────────────────────────────
@app.route("/portal/news")
@login_required
def portal_news():
    email = session["researcher"]
    news_list = sb("GET","news",params="?order=created_at.desc") or []
    return render_template("portal_news.html", news=news_list, researcher=email)

@app.route("/portal/news/new", methods=["POST"])
@login_required
def portal_news_new():
    sb("POST","news",data={
        "title": request.form.get("title","").strip(),
        "content": request.form.get("content","").strip(),
        "researcher_email": session["researcher"],
        "published": 1 if request.form.get("published") else 0,
    })
    flash("공지가 등록됐어요.")
    return redirect(url_for("portal_news"))

@app.route("/portal/news/<news_id>/edit", methods=["POST"])
@login_required
def portal_news_edit(news_id):
    sb("PATCH","news",
       data={"title": request.form.get("title","").strip(),
             "content": request.form.get("content","").strip(),
             "published": 1 if request.form.get("published") else 0},
       params=f"?id=eq.{_q(news_id)}")
    flash("공지가 수정됐어요.")
    return redirect(url_for("portal_news"))

@app.route("/portal/news/<news_id>/delete", methods=["POST"])
@login_required
def portal_news_delete(news_id):
    sb("DELETE","news",params=f"?id=eq.{_q(news_id)}")
    flash("공지가 삭제됐어요.")
    return redirect(url_for("portal_news"))

# ── Analytics Dashboard ───────────────────────────────
@app.route("/portal/analytics")
@login_required
def portal_analytics():
    email = session["researcher"]
    projects = sb("GET", "projects", params=f"?researcher_email=eq.{_q(email)}&order=created_at.desc") or []
    return render_template("portal_analytics.html", researcher=email,
        projects=projects if isinstance(projects, list) else [])

@app.route("/portal/analytics/data")
@csrf.exempt
@login_required
def portal_analytics_data():
    email = session["researcher"]
    project_id = request.args.get("project_id", "").strip()
    if not project_id:
        return jsonify({"error": "project_id required"}), 400
    if not _project_access_ok(project_id, email):
        return jsonify({"error": "unauthorized"}), 403
    participants = sb("GET", "project_participants", params=f"?project_id=eq.{_q(project_id)}") or []
    p_map = {p["id"]: p for p in (participants if isinstance(participants, list) else [])}
    mlist = sb("GET", "measurements", params=f"?project_id=eq.{_q(project_id)}&excluded=eq.0&order=measured_at.asc") or []
    rows = []
    for m in (mlist if isinstance(mlist, list) else []):
        info = p_map.get(m.get("participant_id", ""), {})
        data = m.get("data") or {}
        if not isinstance(data, dict):
            try: data = json.loads(data)
            except: data = {}
        row = {
            "참여자코드": info.get("code", ""),
            "그룹": info.get("group_name", ""),
            "성별": info.get("gender", ""),
            "나이": info.get("age", ""),
            "phase": m.get("phase", ""),
            "측정일": (m.get("measured_at") or "")[:10],
        }
        row.update(data)
        rows.append(row)
    return jsonify({"rows": rows, "count": len(rows)})

# ── API documentation ─────────────────────────────────
@app.route("/api/docs")
@login_required
def api_docs():
    api_key = '(서버 환경변수에서 별도 관리)'
    return render_template("api_docs.html", researcher=session["researcher"], api_key=api_key)

# ── Research topic inline edit ────────────────────────
@app.route("/portal/research/<key>/edit", methods=["POST"])
@login_required
def portal_research_edit(key):
    summary = request.form.get("summary","").strip()
    detail  = request.form.get("detail","").strip()
    sb("POST", "research_topics_extra",
       data={"key": key, "summary": summary, "detail": detail},
       upsert=True)
    flash("연구 소개가 수정됐어요.")
    return redirect(url_for("research") + f"#{key}")

# ── Contact messages inbox ────────────────────────────
@app.route("/portal/contacts")
@login_required
def portal_contacts():
    msgs = sb("GET", "contact_messages", params="?order=created_at.desc") or []
    return render_template("portal_contacts.html", messages=msgs, researcher=session["researcher"])

# ── 태스크(일정) 관리 ─────────────────────────────────────
@app.route("/portal/tasks")
@login_required
def portal_tasks():
    me = session["researcher"]
    # 내 프로젝트 + 협력자 프로젝트
    my_projects = sb("GET", "projects", params=f"?researcher_email=eq.{_q(me)}&select=id,name") or []
    collab_rows = sb("GET", "project_collaborators", params=f"?researcher_email=eq.{_q(me)}&select=project_id") or []
    collab_pids = [r["project_id"] for r in collab_rows]
    if collab_pids:
        for pid in collab_pids:
            row = sb("GET", "projects", params=f"?id=eq.{_q(pid)}&select=id,name")
            if row: my_projects += row
    proj_map = {p["id"]: p["name"] for p in my_projects}
    pids = list(proj_map.keys())

    all_tasks = []
    for pid in pids:
        rows = sb("GET", "tasks", params=f"?project_id=eq.{_q(pid)}&order=due_date.asc") or []
        for r in rows:
            r["project_name"] = proj_map.get(pid, "")
        all_tasks += rows

    # 고정 멤버 목록 (연구실 구성원)
    members = ["최윤정", "남궁혜인", "연지영", "이채영"]
    now_date = datetime.utcnow().strftime("%Y-%m-%d")
    return render_template("portal_tasks.html",
        researcher=me, tasks=all_tasks,
        projects=my_projects, members=members,
        now_date=now_date)

@app.route("/portal/tasks/new", methods=["POST"])
@login_required
def portal_task_new():
    me = session["researcher"]
    project_id = request.form.get("project_id") or None
    if project_id and not _project_access_ok(project_id, me):
        flash("이 프로젝트에 접근 권한이 없어요.", "error")
        return redirect(url_for("portal_tasks"))
    data = {
        "title":       request.form.get("title", "").strip(),
        "project_id":  project_id,
        "description": request.form.get("description", "").strip(),
        "assigned_to": request.form.get("assigned_to", "").strip() or None,
        "due_date":    request.form.get("due_date", "").strip() or None,
        "status":      request.form.get("status", "todo"),
        "priority":    request.form.get("priority", "normal"),
        "created_by":  me,
    }
    if not data["title"]:
        flash("태스크 제목을 입력하세요.", "error")
        return redirect(url_for("portal_tasks"))
    sb("POST", "tasks", data=data)
    flash("태스크를 추가했습니다.")
    return redirect(url_for("portal_tasks"))

@app.route("/portal/tasks/<task_id>/status", methods=["POST"])
@login_required
def portal_task_status(task_id):
    new_status = request.get_json(silent=True) or {}
    status = new_status.get("status", "todo")
    if status not in ('todo', 'doing', 'done'):
        abort(400)
    sb("PATCH", "tasks", data={"status": status, "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")},
       params=f"?id=eq.{_q(task_id)}")
    return jsonify({"ok": True})

@app.route("/portal/tasks/<task_id>/edit", methods=["POST"])
@login_required
def portal_task_edit(task_id):
    sb("PATCH", "tasks", data={
        "title":       request.form.get("title", "").strip(),
        "description": request.form.get("description", "").strip(),
        "assigned_to": request.form.get("assigned_to", "").strip() or None,
        "due_date":    request.form.get("due_date", "").strip() or None,
        "priority":    request.form.get("priority", "normal"),
        "status":      request.form.get("status", "todo"),
        "updated_at":  datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
    }, params=f"?id=eq.{_q(task_id)}")
    flash("태스크가 수정됐습니다.")
    return redirect(url_for("portal_tasks"))

@app.route("/portal/tasks/<task_id>/delete", methods=["POST"])
@login_required
def portal_task_delete(task_id):
    sb("DELETE", "tasks", params=f"?id=eq.{_q(task_id)}")
    flash("태스크를 삭제했습니다.")
    return redirect(url_for("portal_tasks"))

@app.route("/portal/audit")
@login_required
def portal_audit():
    PAGE_SIZE = 50
    page = max(1, request.args.get("page", 1, type=int))
    total = _sb_count("audit_log")
    offset = (page - 1) * PAGE_SIZE
    logs = sb("GET", "audit_log",
              params=f"?order=created_at.desc&limit={_q(PAGE_SIZE)}&offset={_q(offset)}") or []
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return render_template("portal_audit.html", logs=logs, researcher=session["researcher"],
                           page=page, total_pages=total_pages, total=total)

# ── Collaboration ──────────────────────────────────────
@app.route("/portal/projects/<project_id>/collaborators/add", methods=["POST"])
@login_required
def portal_project_add_collaborator(project_id):
    proj, err = _require_project_owner(project_id)
    if err: return err
    # Only the owner can invite collaborators
    if proj.get("researcher_email") != session.get("researcher"):
        flash("프로젝트 소유자만 협력자를 초대할 수 있어요.", "error")
        return redirect(url_for("portal_project", project_id=project_id))
    email = (request.form.get("collaborator_email") or "").strip().lower()
    role  = request.form.get("role", "viewer")
    if role not in ('viewer', 'editor'):
        abort(400)
    if not email:
        flash("초대할 연구자 이메일을 입력해 주세요.", "error")
        return redirect(url_for("portal_project", project_id=project_id))
    if email == session.get("researcher"):
        flash("본인을 협력자로 초대할 수 없어요.", "error")
        return redirect(url_for("portal_project", project_id=project_id))
    existing = sb("GET", "project_collaborators",
                  params=f"?project_id=eq.{_q(project_id)}&researcher_email=eq.{_q(email)}&select=id")
    if existing:
        flash(f"{email}는 이미 협력자로 등록되어 있어요.", "error")
        return redirect(url_for("portal_project", project_id=project_id))
    sb("POST", "project_collaborators", data={
        "project_id": project_id, "researcher_email": email, "role": role
    })
    audit("collaborator_add", "project_collaborators", project_id,
          after={"email": email, "role": role})
    flash(f"{email}를 {role} 역할로 초대했어요.")
    return redirect(url_for("portal_project", project_id=project_id))

@app.route("/portal/projects/<project_id>/collaborators/remove", methods=["POST"])
@login_required
def portal_project_remove_collaborator(project_id):
    proj, err = _require_project_owner(project_id)
    if err: return err
    if proj.get("researcher_email") != session.get("researcher"):
        flash("프로젝트 소유자만 협력자를 제거할 수 있어요.", "error")
        return redirect(url_for("portal_project", project_id=project_id))
    email = (request.form.get("collaborator_email") or "").strip().lower()
    sb("DELETE", "project_collaborators",
       params=f"?project_id=eq.{_q(project_id)}&researcher_email=eq.{_q(email)}")
    audit("collaborator_remove", "project_collaborators", project_id, before={"email": email})
    flash(f"{email}를 협력자 목록에서 제거했어요.")
    return redirect(url_for("portal_project", project_id=project_id))

# ── Protocol phases ────────────────────────────────────
@app.route("/portal/projects/<project_id>/protocols/save", methods=["POST"])
@login_required
def portal_project_protocols_save(project_id):
    proj, err = _require_project_owner(project_id)
    if err: return err
    phases_raw = request.form.get("phases", "")
    phases = [p.strip() for p in phases_raw.split(",") if p.strip()]
    sb("DELETE", "project_protocols", params=f"?project_id=eq.{_q(project_id)}")
    for i, phase in enumerate(phases):
        sb("POST", "project_protocols", data={
            "project_id": project_id, "phase_name": phase, "sort_order": i
        })
    audit("protocol_save", "project_protocols", project_id, after={"phases": phases})
    flash("프로토콜 단계가 저장되었어요.")
    return redirect(url_for("portal_project", project_id=project_id) + "#tab-settings")

# ── Password reset ─────────────────────────────────────
@app.route("/reset-password", methods=["GET","POST"])
@limiter.limit('5 per minute', methods=['POST'])
def reset_password_request():
    if request.method == 'POST':
        flash('보안을 위해 온라인 비밀번호 재설정을 중단했습니다. 연구실 관리자에게 문의해주세요.')
        return redirect(url_for('login'))
    return render_template('reset_password_request.html')

@app.route("/reset-password/<token>", methods=["GET","POST"])
def reset_password_confirm(token):
    # Previously disclosed tokens must not remain usable.
    abort(404)

# ── Data viewer / editor (Feature C) ──────────────────
@app.route("/portal/projects/<project_id>/data")
@login_required
def portal_project_data(project_id):
    proj, err = _require_project_owner(project_id)
    if err: return err
    file_id = request.args.get("file_id")
    participants = sb("GET", "project_participants", params=f"?project_id=eq.{_q(project_id)}") or []
    p_map = {p["id"]: p for p in (participants if isinstance(participants, list) else [])}
    if file_id:
        mlist = sb("GET", "measurements", params=f"?project_id=eq.{_q(project_id)}&source_file_id=eq.{_q(file_id)}&order=measured_at.asc") or []
        file_info = sb("GET", "portal_files", params=f"?id=eq.{_q(file_id)}")
        file_info = file_info[0] if file_info else {}
        try: col_schema = json.loads(file_info.get("column_schema") or "[]")
        except: col_schema = []
    else:
        mlist = sb("GET", "measurements", params=f"?project_id=eq.{_q(project_id)}&order=measured_at.asc") or []
        file_info = {}
        col_schema = []

    measurements = mlist if isinstance(mlist, list) else []
    for m in measurements:
        info = p_map.get(m.get("participant_id",""), {})
        m["participant_code"] = info.get("code","")
        m["group_name"] = info.get("group_name","")
        if not isinstance(m.get("data"), dict):
            try: m["data"] = json.loads(m["data"] or "{}")
            except: m["data"] = {}

    # Derive columns from data if no schema stored
    if not col_schema and measurements:
        all_keys = []
        for m in measurements:
            for k in (m.get("data") or {}).keys():
                if k not in all_keys: all_keys.append(k)
        col_schema = [{"name": k, "type": "text"} for k in all_keys]

    files = sb("GET", "portal_files", params=f"?project_id=eq.{_q(project_id)}&order=uploaded_at.desc") or []
    return render_template("portal_data.html",
        project=proj, measurements=measurements, col_schema=col_schema,
        file_info=file_info, file_id=file_id, files=files,
        researcher=session["researcher"])

@app.route("/portal/projects/<project_id>/data/cell", methods=["POST"])
@login_required
def portal_project_data_cell(project_id):
    _, err = _require_project_owner(project_id)
    if err: return jsonify({"ok": False, "error": "unauthorized"}), 403
    payload = request.get_json(silent=True) or {}
    measurement_id = payload.get("measurement_id","")
    col = payload.get("column","")
    val = payload.get("value","")
    if not measurement_id or not col:
        return jsonify({"ok": False, "error": "missing fields"}), 400
    rows = sb("GET", "measurements", params=f"?id=eq.{_q(measurement_id)}&project_id=eq.{_q(project_id)}&select=data")
    if not rows:
        return jsonify({"ok": False, "error": "not found"}), 404
    data = rows[0].get("data") or {}
    if not isinstance(data, dict):
        try: data = json.loads(data)
        except: data = {}
    # Handle special top-level columns
    if col in ("phase", "notes"):
        sb("PATCH", "measurements", data={col: val or None}, params=f"?id=eq.{_q(measurement_id)}")
    else:
        data[col] = val
        sb("PATCH", "measurements", data={"data": data}, params=f"?id=eq.{_q(measurement_id)}")
    return jsonify({"ok": True})

@app.route("/portal/projects/<project_id>/data/export")
@login_required
def portal_project_data_export(project_id):
    proj, err = _require_project_owner(project_id)
    if err: return err
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    file_id = request.args.get("file_id")
    participants = sb("GET", "project_participants", params=f"?project_id=eq.{_q(project_id)}") or []
    p_map = {p["id"]: p for p in (participants if isinstance(participants, list) else [])}
    if file_id:
        mlist = sb("GET", "measurements", params=f"?project_id=eq.{_q(project_id)}&source_file_id=eq.{_q(file_id)}&order=measured_at.asc") or []
        file_info = sb("GET", "portal_files", params=f"?id=eq.{_q(file_id)}")
        file_info = file_info[0] if file_info else {}
        try: col_schema = json.loads(file_info.get("column_schema") or "[]")
        except: col_schema = []
        data_cols = [c["name"] for c in col_schema]
    else:
        mlist = sb("GET", "measurements", params=f"?project_id=eq.{_q(project_id)}&order=measured_at.asc") or []
        data_cols = []

    measurements = mlist if isinstance(mlist, list) else []
    for m in measurements:
        info = p_map.get(m.get("participant_id",""), {})
        m["participant_code"] = info.get("code","")
        m["group_name"] = info.get("group_name","")
        if not isinstance(m.get("data"), dict):
            try: m["data"] = json.loads(m["data"] or "{}")
            except: m["data"] = {}
        if not data_cols:
            for k in (m.get("data") or {}).keys():
                if k not in data_cols: data_cols.append(k)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    header_fill = PatternFill("solid", fgColor="0F6B6B")
    header_font = Font(color="FFFFFF", bold=True)
    meta_cols = ["code", "group", "phase", "measured_at", "notes"]
    all_cols = meta_cols + data_cols
    for ci, col in enumerate(all_cols, 1):
        cell = ws.cell(row=1, column=ci, value=col)
        cell.fill = header_fill; cell.font = header_font
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[cell.column_letter].width = max(12, len(col)+4)
    for m in measurements:
        data = m.get("data") or {}
        row = [
            m.get("participant_code",""), m.get("group_name",""),
            m.get("phase",""), (m.get("measured_at") or "")[:10], m.get("notes","")
        ] + [data.get(c,"") for c in data_cols]
        ws.append(row)
    buf = io.BytesIO()
    neutralize_excel_formulas(wb)
    wb.save(buf); buf.seek(0)
    fname = f'{proj["name"].replace(" ","_")}_{datetime.now().strftime("%Y%m%d")}.xlsx'
    return Response(buf.read(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment;filename={fname}"})

if __name__ == "__main__":
    app.run(debug=False)
