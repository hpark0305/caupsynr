-- Additive only; apply BEFORE deploying the 2FA / access-log release.
-- accounts: TOTP second factor (개인정보의 안전성 확보조치 기준 제6조②)
-- audit_log: source IP and user agent for access records (제8조 접속기록)
BEGIN;
ALTER TABLE public.accounts  ADD COLUMN IF NOT EXISTS totp_secret text;
ALTER TABLE public.accounts  ADD COLUMN IF NOT EXISTS totp_last   bigint;
ALTER TABLE public.audit_log ADD COLUMN IF NOT EXISTS ip          text;
ALTER TABLE public.audit_log ADD COLUMN IF NOT EXISTS user_agent  text;
-- Also created (applied 2026-11-03; redundant with idx_audit_created, harmless):
CREATE INDEX IF NOT EXISTS audit_log_created_idx ON public.audit_log (created_at);
COMMIT;
