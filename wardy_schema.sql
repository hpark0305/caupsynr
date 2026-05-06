-- Wardy 앱 이벤트 로그 테이블
CREATE TABLE IF NOT EXISTS wardy_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID,
    user_name   TEXT NOT NULL,
    seq         INTEGER,
    event_time  TEXT,
    state       TEXT NOT NULL,
    game_level  TEXT,
    data        JSONB DEFAULT '{}',
    received_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wardy_project  ON wardy_events(project_id);
CREATE INDEX IF NOT EXISTS idx_wardy_user     ON wardy_events(user_name);
CREATE INDEX IF NOT EXISTS idx_wardy_state    ON wardy_events(state);
CREATE INDEX IF NOT EXISTS idx_wardy_received ON wardy_events(received_at DESC);
