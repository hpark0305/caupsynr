-- NeuroBreeze 접속 기록 (2026-11-14)
-- 앱을 열어 쓰다가 닫거나 백그라운드로 보낼 때 1건. id = 앱이 만든 session_id (upsert).
-- 데이터 공유에 동의한 사용자만 앱이 보낸다. 이전에는 Google Apps Script → Google Sheets로만 갔다.
CREATE TABLE IF NOT EXISTS neurobreeze_app_usage (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID,
    user_name       TEXT NOT NULL,
    install_id      TEXT,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    foreground_sec  INTEGER,
    event_name      TEXT,
    app_version     TEXT,
    platform        TEXT,
    received_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nb_usage_project  ON neurobreeze_app_usage(project_id);
CREATE INDEX IF NOT EXISTS idx_nb_usage_user     ON neurobreeze_app_usage(user_name);
CREATE INDEX IF NOT EXISTS idx_nb_usage_received ON neurobreeze_app_usage(received_at DESC);

-- Flask alone accesses this table with its server key.
ALTER TABLE neurobreeze_app_usage ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON neurobreeze_app_usage FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON neurobreeze_app_usage TO service_role;
