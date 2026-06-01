-- NeuroBreeze EEG 앱 — Supabase PostgreSQL 스키마
-- Supabase SQL Editor에서 실행하세요

-- ────────────────────────────────────────────────────────────
-- 테이블 1: neurobreeze_eeg — 뇌파검사 결과
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS neurobreeze_eeg (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id            UUID,
    user_name             TEXT        NOT NULL,
    session_num           INTEGER,
    recorded_at           TEXT,
    left_alpha_power      REAL,
    right_alpha_power     REAL,
    alpha_power           REAL,
    left_alpha_indicator  REAL,
    right_alpha_indicator REAL,
    alpha_indicator       REAL,
    received_at           TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nb_eeg_project   ON neurobreeze_eeg(project_id);
CREATE INDEX IF NOT EXISTS idx_nb_eeg_user      ON neurobreeze_eeg(user_name);
CREATE INDEX IF NOT EXISTS idx_nb_eeg_received  ON neurobreeze_eeg(received_at DESC);

-- ────────────────────────────────────────────────────────────
-- 테이블 2: neurobreeze_meditation — 명상 세션
-- ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS neurobreeze_meditation (
    id                       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id               UUID,
    user_name                TEXT        NOT NULL,
    session_num              INTEGER,
    start_time               TEXT,
    end_time                 TEXT,
    feedback_count           INTEGER,
    breath_mode              TEXT,
    -- 세션 전체 알파 수치
    left_alpha_power         REAL,
    right_alpha_power        REAL,
    alpha_power              REAL,
    left_alpha_indicator     REAL,
    right_alpha_indicator    REAL,
    alpha_indicator          REAL,
    -- 시작/종료 1분 평균
    start_alpha_power_avg    REAL,
    end_alpha_power_avg      REAL,
    start_alpha_indicator_avg REAL,
    end_alpha_indicator_avg  REAL,
    -- 증가율
    alpha_power_increase     REAL,
    alpha_indicator_increase REAL,
    alpha_progress_rate      REAL,
    received_at              TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nb_med_project  ON neurobreeze_meditation(project_id);
CREATE INDEX IF NOT EXISTS idx_nb_med_user     ON neurobreeze_meditation(user_name);
CREATE INDEX IF NOT EXISTS idx_nb_med_received ON neurobreeze_meditation(received_at DESC);
