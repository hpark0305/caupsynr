-- NeuroBreeze 세션 기록 v2 (2026-11-10)
-- 앱이 기기 없이 진행한 세션까지 1세션 1행으로 남긴다. id = 앱이 만든 session_uuid (upsert).
-- 기존 열·RLS·REVOKE는 그대로 둔다. 운영 DB(서울)에 적용하기 전에 확인을 받는다.
ALTER TABLE neurobreeze_meditation
  ADD COLUMN IF NOT EXISTS mode                    TEXT,
  ADD COLUMN IF NOT EXISTS program                 TEXT,
  ADD COLUMN IF NOT EXISTS planned_sec             INTEGER,
  ADD COLUMN IF NOT EXISTS duration_sec            INTEGER,
  ADD COLUMN IF NOT EXISTS completed               BOOLEAN,
  ADD COLUMN IF NOT EXISTS end_reason              TEXT,
  ADD COLUMN IF NOT EXISTS device_lost_mid_session BOOLEAN,
  ADD COLUMN IF NOT EXISTS eeg_sample_count        INTEGER,
  ADD COLUMN IF NOT EXISTS stability_score         INTEGER,
  ADD COLUMN IF NOT EXISTS app_version             TEXT,
  ADD COLUMN IF NOT EXISTS platform                TEXT;

CREATE INDEX IF NOT EXISTS idx_nb_med_program ON neurobreeze_meditation(program);
