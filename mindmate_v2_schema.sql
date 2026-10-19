-- Mind Mate 재설계: Unity 앱 → Apps Script 릴레이 → /api/mindmate/followup 이 채우는 테이블 2개.
-- Supabase SQL Editor에서 실행하세요.
--
-- UserName/Department/PhoneNumber는 저장하지 않습니다. PhoneNumber를
-- HMAC(pepper)으로 해시한 participant_key만 남겨 같은 사람은 항상 같은
-- 값으로 매핑되면서도 원문 전화번호는 복원할 수 없게 합니다.
-- raw 컬럼에는 원본 payload에서 PII 3필드만 뺀 나머지를 그대로 백업합니다.

-- order=writeData_constant (음원 재생 기록)
CREATE TABLE IF NOT EXISTS mindmate_music_log (
    id              UUID PRIMARY KEY,
    participant_key TEXT NOT NULL,
    date            TEXT,
    start_time      TEXT,
    end_time        TEXT,
    music_name      TEXT,
    music_type      TEXT,
    raw             TEXT DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mm_music_pkey ON mindmate_music_log(participant_key);
CREATE INDEX IF NOT EXISTS idx_mm_music_date ON mindmate_music_log(date);

-- order=updateData_daily / updateData_daily_json (일일 설문 + 5일 정기설문)
-- id는 (participant_key, date)로 결정론적으로 생성되어 참여자당 하루 1행만 존재.
-- 앱이 같은 날짜를 여러 번(부분 필드만) 보내면 upsert로 병합됩니다.
CREATE TABLE IF NOT EXISTS mindmate_daily_survey (
    id                   UUID PRIMARY KEY,
    participant_key      TEXT NOT NULL,
    date                 TEXT NOT NULL,
    work_type            TEXT,
    is_survey_daily      TEXT,
    servey_daily_1       TEXT,
    servey_daily_2       TEXT,
    servey_daily_3       TEXT,
    is_mind_healing      TEXT,
    servey_mindhealing_1 TEXT,
    is_five_days         TEXT,
    servey_five_1        TEXT,
    servey_five_2        TEXT,
    servey_five_3        TEXT,
    servey_five_4        TEXT,
    servey_five_5        TEXT,
    servey_five_6        TEXT,
    servey_five_7        TEXT,
    servey_five_8        TEXT,
    servey_five_9        TEXT,
    servey_five_10       TEXT,
    servey_five_11       TEXT,
    servey_five_12       TEXT,
    servey_five_result   TEXT,
    source               TEXT,  -- 'updateData_daily' | 'updateData_daily_json' — 실시간 뷰의 활동 종류 구분용
    raw                  TEXT DEFAULT '{}',
    created_at           TIMESTAMPTZ DEFAULT now(),
    updated_at           TIMESTAMPTZ DEFAULT now()
);

-- 이 스키마를 이미 실행해서 테이블이 있는 경우를 위한 보강 (없으면 no-op)
ALTER TABLE mindmate_daily_survey ADD COLUMN IF NOT EXISTS source TEXT;

CREATE INDEX IF NOT EXISTS idx_mm_survey_pkey ON mindmate_daily_survey(participant_key);
CREATE INDEX IF NOT EXISTS idx_mm_survey_date ON mindmate_daily_survey(date);

-- order=appOpen (앱 실행 자체를 기록 — Unity 쪽엔 아직 없음, 추가 예정)
-- 하루 1건으로 합치지 않고 매번 새 행. 참여자당 하루에 여러 번 열 수 있고,
-- 실시간 화면에서는 "언제 열었는지" 자체가 신호라 upsert 대상이 아님.
CREATE TABLE IF NOT EXISTS mindmate_app_open (
    id              UUID PRIMARY KEY,
    participant_key TEXT NOT NULL,
    raw             TEXT DEFAULT '{}',
    received_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mm_open_pkey     ON mindmate_app_open(participant_key);
CREATE INDEX IF NOT EXISTS idx_mm_open_received ON mindmate_app_open(received_at DESC);

-- RLS 활성화: 서비스 롤은 RLS를 우회하므로 비활성화할 필요가 없습니다.
ALTER TABLE mindmate_music_log    ENABLE ROW LEVEL SECURITY;
ALTER TABLE mindmate_daily_survey ENABLE ROW LEVEL SECURITY;
ALTER TABLE mindmate_app_open     ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON mindmate_music_log, mindmate_daily_survey, mindmate_app_open FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON mindmate_music_log, mindmate_daily_survey, mindmate_app_open TO service_role;

-- After all schema files, apply supabase_security_access.sql to revoke client grants.
