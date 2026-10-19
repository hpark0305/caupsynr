-- Apply BEFORE deploying the security update. Additive; no research data changes.
-- Requires the server-only service_role / secret key, not an anon browser key.
BEGIN;
CREATE TABLE IF NOT EXISTS public.portal_auth_sessions (
    id text PRIMARY KEY,
    researcher_email text NOT NULL,
    created_at bigint NOT NULL,
    last_seen bigint NOT NULL
);
CREATE INDEX IF NOT EXISTS portal_auth_sessions_created_idx
    ON public.portal_auth_sessions (created_at);
ALTER TABLE public.portal_auth_sessions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.portal_auth_sessions FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.portal_auth_sessions TO service_role;
-- No client-facing RLS policies: authentication records are server-only.
COMMIT;

-- Optional maintenance by a trusted operator (not needed for expiry enforcement):
-- DELETE FROM public.portal_auth_sessions
-- WHERE created_at < extract(epoch FROM now() - interval '8 hours')::bigint;
