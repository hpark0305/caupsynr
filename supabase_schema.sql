-- ══════════════════════════════════════════════════════════════
-- TSL Portal — Supabase PostgreSQL Schema
-- Run this in: Supabase Dashboard → SQL Editor → New query
-- ══════════════════════════════════════════════════════════════

-- Enable UUID generation
create extension if not exists "pgcrypto";

-- ── accounts ──────────────────────────────────────────────────
create table if not exists accounts (
  email    text primary key,
  password text not null
);

-- ── projects ──────────────────────────────────────────────────
create table if not exists projects (
  id               text primary key default gen_random_uuid()::text,
  name             text not null,
  description      text,
  app_type         text,
  researcher_email text not null,
  created_at       timestamptz default now()
);

-- ── project_participants ──────────────────────────────────────
create table if not exists project_participants (
  id           text primary key default gen_random_uuid()::text,
  project_id   text not null references projects(id) on delete cascade,
  code         text not null,
  gender       text,
  age          integer,
  group_name   text,
  enrolled_at  timestamptz default now(),
  unique (project_id, code)
);

-- ── measurements ─────────────────────────────────────────────
create table if not exists measurements (
  id             text primary key default gen_random_uuid()::text,
  project_id     text not null references projects(id) on delete cascade,
  participant_id text not null references project_participants(id) on delete cascade,
  phase          text,
  notes          text,
  data           jsonb default '{}'::jsonb,
  excluded       integer default 0,
  measured_at    timestamptz default now()
);

-- ── project_variables ────────────────────────────────────────
create table if not exists project_variables (
  id         text primary key default gen_random_uuid()::text,
  project_id text not null references projects(id) on delete cascade,
  name       text not null,
  label      text,
  var_type   text default 'number',
  unit       text
);

-- ── portal_files ─────────────────────────────────────────────
create table if not exists portal_files (
  id               text primary key default gen_random_uuid()::text,
  project_id       text,
  filename         text not null,
  original_name    text not null,
  size             integer default 0,
  researcher_email text not null,
  uploaded_at      timestamptz default now()
);

-- ── news ─────────────────────────────────────────────────────
create table if not exists news (
  id               text primary key default gen_random_uuid()::text,
  title            text not null,
  content          text,
  researcher_email text not null,
  published        integer default 1,
  created_at       timestamptz default now()
);

-- ── research_topics_extra ────────────────────────────────────
create table if not exists research_topics_extra (
  key     text primary key,
  title   text,
  summary text,
  detail  text
);

-- ── contact_messages ─────────────────────────────────────────
create table if not exists contact_messages (
  id         text primary key default gen_random_uuid()::text,
  name       text,
  email      text,
  subject    text,
  message    text not null,
  created_at timestamptz default now()
);

-- ── sessions (external app data) ─────────────────────────────
create table if not exists sessions (
  id             text primary key default gen_random_uuid()::text,
  project_id     text,
  participant_id text,
  phase          text,
  notes          text,
  data           jsonb default '{}'::jsonb,
  received_at    timestamptz default now()
);

-- ── audit_log ────────────────────────────────────────────────
create table if not exists audit_log (
  id           text primary key default gen_random_uuid()::text,
  researcher   text,
  action       text not null,
  target_table text,
  target_id    text,
  detail       text,
  before_val   text,
  after_val    text,
  created_at   timestamptz default now()
);

-- ── project_collaborators ────────────────────────────────────
create table if not exists project_collaborators (
  id               text primary key default gen_random_uuid()::text,
  project_id       text not null references projects(id) on delete cascade,
  researcher_email text not null,
  role             text default 'editor',
  invited_at       timestamptz default now(),
  unique (project_id, researcher_email)
);

-- ── reset_tokens ─────────────────────────────────────────────
create table if not exists reset_tokens (
  token      text primary key,
  email      text not null,
  expires_at text not null,
  used       integer default 0
);

-- ── project_protocols ────────────────────────────────────────
create table if not exists project_protocols (
  id          text primary key default gen_random_uuid()::text,
  project_id  text not null references projects(id) on delete cascade,
  phase_name  text not null,
  description text,
  due_offset_days integer default 0,
  sort_order  integer default 0
);

-- ══════════════════════════════════════════════════════════════
-- Indexes
-- ══════════════════════════════════════════════════════════════
create index if not exists idx_m_project       on measurements(project_id);
create index if not exists idx_m_participant   on measurements(participant_id);
create index if not exists idx_pp_project      on project_participants(project_id);
create index if not exists idx_pv_project      on project_variables(project_id);
create index if not exists idx_m_measured_at   on measurements(measured_at desc);
create index if not exists idx_audit_created   on audit_log(created_at desc);
create index if not exists idx_proj_researcher on projects(researcher_email);
create index if not exists idx_collab_project  on project_collaborators(project_id);

-- ══════════════════════════════════════════════════════════════
-- Row Level Security — enabled; service role bypasses RLS
-- (The app uses the service role key, not anon)
-- ══════════════════════════════════════════════════════════════
alter table accounts              enable row level security;
alter table projects              enable row level security;
alter table project_participants  enable row level security;
alter table measurements          enable row level security;
alter table project_variables     enable row level security;
alter table portal_files          enable row level security;
alter table news                  enable row level security;
alter table research_topics_extra enable row level security;
alter table contact_messages      enable row level security;
alter table sessions              enable row level security;
alter table audit_log             enable row level security;
alter table project_collaborators enable row level security;
alter table reset_tokens          enable row level security;
alter table project_protocols     enable row level security;

-- After all schema files, apply supabase_security_access.sql to revoke client grants.
