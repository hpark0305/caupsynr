-- Apply after all application schemas. No rows are deleted or changed.
-- Prerequisite: Flask SUPABASE_KEY must be a server-only service_role/secret key.
-- This application authenticates researchers in Flask, NOT Supabase Auth.
-- Browser/Unity/GAS must use Flask endpoints, not the database Data API directly.
BEGIN;
DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY[
        'accounts', 'projects', 'project_participants', 'measurements',
        'project_variables', 'portal_files', 'news', 'research_topics_extra',
        'contact_messages', 'sessions', 'audit_log', 'project_collaborators',
        'reset_tokens', 'project_protocols', 'wardy_events', 'tasks',
        'neurobreeze_eeg', 'neurobreeze_meditation', 'mindmate_followup',
        'mindmate_music_log', 'mindmate_daily_survey', 'mindmate_app_open',
        'portal_auth_sessions', 'researchers', 'participants', 'forms', 'form_responses'
    ] LOOP
        IF to_regclass(format('public.%I', target)) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', target);
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC, anon, authenticated', target);
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO service_role', target);
        END IF;
    END LOOP;
END $$;

-- Internal event-trigger helper is never a client RPC. Keep its owner and
-- service_role privileges; removing client EXECUTE does not disable the trigger.
DO $$
BEGIN
    IF to_regprocedure('public.rls_auto_enable()') IS NOT NULL THEN
        REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC, anon, authenticated;
    END IF;
END $$;
COMMIT;

-- Read-only verification (all app tables should show RLS=true, anon/auth grants=false).
SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled,
       has_table_privilege('anon', c.oid, 'SELECT') AS anon_select,
       has_table_privilege('anon', c.oid, 'INSERT') AS anon_insert,
       has_table_privilege('authenticated', c.oid, 'SELECT') AS authenticated_select
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname;
