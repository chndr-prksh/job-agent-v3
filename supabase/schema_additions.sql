-- =====================================================================
-- job-agent v3 — ADDITIONAL tables (your main schema stays untouched)
-- These three tables are additive: they layer on top of yours.
-- =====================================================================

-- 1. daemon_heartbeat (we need this for liveness)
create table if not exists public.daemon_heartbeat (
    id int primary key default 1,
    last_seen timestamp without time zone default now(),
    pid int,
    constraint single_row check (id = 1)
);

-- 2. apply_log (audit trail per job per event)
create table if not exists public.apply_log (
    id bigserial primary key,
    job_id uuid references public.jobs(id) on delete cascade,
    event text not null,
    detail jsonb default '{}'::jsonb,
    created_at timestamp without time zone default now()
);
create index if not exists idx_apply_log_job on public.apply_log(job_id, created_at desc);
create index if not exists idx_apply_log_event on public.apply_log(event, created_at desc);

-- 3. job_status (per-job internal state, separate from your jobs table which
--    is the scraped listing truth; this is "where the daemon is in the pipeline")
create table if not exists public.job_status (
    job_id uuid primary key references public.jobs(id) on delete cascade,
    pipeline_status text default 'queued',  -- 'queued' | 'fetched' | 'ranked' | 'tailored' | 'planned' | 'ready_to_apply' | 'applying' | 'submitted' | 'skipped' | 'blocked' | 'failed'
    status_message text,
    updated_at timestamp without time zone default now()
);

-- Trigger for updated_at on job_status
create or replace function set_updated_at() returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

drop trigger if exists trg_job_status_set_updated_at on public.job_status;
create trigger trg_job_status_set_updated_at
    before update on public.job_status
    for each row execute function set_updated_at();

-- Convenience view: "jobs ready to be planned" (have matched skill data but no plan yet)
create or replace view public.jobs_ready_to_plan as
select j.id as job_id
from public.jobs j
join public.job_matches m on m.job_id = j.id
where m.relevance_score >= 0.4
  and not exists (select 1 from public.application_plans p where p.job_id = j.id)
  and exists (
      select 1 from public.resume_versions rv where rv.job_id = j.id
  );