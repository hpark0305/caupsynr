-- Align public.sessions with the columns /api/sessions writes and
-- /portal/all/sessions reads (project_id, phase, received_at). Before this,
-- every /api/sessions insert failed (PGRST204) and the page listed nothing.
-- Also points participant_id at project_participants (the portal's
-- participant table) instead of the unused legacy public.participants.
-- Aborts unless sessions is empty, so no stored row is ever affected.
BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.sessions) THEN
        RAISE EXCEPTION 'sessions is not empty; review this migration before applying';
    END IF;
END $$;
ALTER TABLE public.sessions
    ADD COLUMN IF NOT EXISTS project_id uuid REFERENCES public.projects(id),
    ADD COLUMN IF NOT EXISTS phase text,
    ADD COLUMN IF NOT EXISTS received_at timestamptz DEFAULT now();
ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS sessions_participant_id_fkey;
ALTER TABLE public.sessions
    ADD CONSTRAINT sessions_participant_id_fkey
    FOREIGN KEY (participant_id) REFERENCES public.project_participants(id);
CREATE INDEX IF NOT EXISTS sessions_project_received_idx ON public.sessions (project_id, received_at);
COMMIT;
