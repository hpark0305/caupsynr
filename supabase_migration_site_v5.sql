-- Additive only; apply BEFORE deploying the public-site renewal (News / Participate / Projects).
-- site_studies: participant-recruitment notices shown on /participate (IRB number required).
-- site_projects: funded/ongoing projects listed on /research.
-- Same access model as supabase_security_access.sql: Flask uses the service_role key,
-- browsers never touch these tables directly.
BEGIN;
CREATE TABLE IF NOT EXISTS public.site_studies (
  id               text primary key default gen_random_uuid()::text,
  title            text not null,
  summary          text,
  eligibility      text,
  duration         text,
  compensation     text,
  irb_no           text not null,
  contact          text not null,
  link             text,
  status           text not null default 'recruiting' check (status in ('recruiting', 'closed')),
  published        integer default 1,
  researcher_email text,
  created_at       timestamptz default now()
);
CREATE TABLE IF NOT EXISTS public.site_projects (
  id               text primary key default gen_random_uuid()::text,
  title            text not null,
  summary          text,
  theme            text,
  period           text,
  funder           text,
  role             text,
  status           text not null default 'ongoing' check (status in ('ongoing', 'completed')),
  published        integer default 1,
  researcher_email text,
  created_at       timestamptz default now()
);
DO $$
DECLARE target text;
BEGIN
  FOREACH target IN ARRAY ARRAY['site_studies', 'site_projects'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', target);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC, anon, authenticated', target);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO service_role', target);
  END LOOP;
END $$;
COMMIT;
