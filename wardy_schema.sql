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

-- ────────────────────────────────────────────────────────────
-- 태스크(일정) 관리 테이블
-- Supabase SQL Editor에서 실행하세요
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tasks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID,
    title       TEXT NOT NULL,
    description TEXT,
    assigned_to TEXT,
    due_date    DATE,
    status      TEXT DEFAULT 'todo',     -- todo / doing / done
    priority    TEXT DEFAULT 'normal',   -- low / normal / high
    created_by  TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tasks_project  ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assigned_to);
CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due      ON tasks(due_date);
