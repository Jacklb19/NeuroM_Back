-- =============================================================================
-- Account storage limits (quality audit of 13 nov 2026, ADR-63): each account
-- may keep at most 2000 sessions and 20 guided recordings in the cloud.
--
-- Why: the plan is free (Supabase Free, 500 MB) and the anon key is public, so
-- one account could fill the shared database by inserting rows straight
-- through PostgREST or by calling save_session and save_training_recording in
-- a loop. 2000 sessions are more than five a day for a year; 20 recordings are
-- far more than a person sends to train the classifier.
--
--   * A BEFORE INSERT trigger counts the rows of the owner and refuses the new
--     one with SQLSTATE 54000 (program_limit_exceeded), which the API answers
--     as 409 limit_reached. Updates are not counted: sending a stored session
--     again (it only updates the later rating) always works.
--   * A transaction-level advisory lock per owner makes two inserts at the
--     same time count one after the other, so the limit cannot be passed.
--   * The functions run as the caller (security invoker): row level security
--     still applies, and the count only sees the caller's own rows.
-- Existing rows are kept even above the limit; only new ones are refused.
-- =============================================================================

-- 1. Sessions.
create or replace function public.enforce_session_limit()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('sessions:' || new.owner_id::text, 0));
  if (select count(*) from public.sessions where owner_id = new.owner_id) >= 2000 then
    raise exception 'session_limit_reached' using errcode = '54000';
  end if;
  return new;
end;
$$;

create trigger sessions_within_account_limit
  before insert on public.sessions
  for each row execute function public.enforce_session_limit();

-- 2. Guided recordings.
create or replace function public.enforce_recording_limit()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('recordings:' || new.owner_id::text, 0));
  if (select count(*) from public.training_recordings where owner_id = new.owner_id) >= 20 then
    raise exception 'recording_limit_reached' using errcode = '54000';
  end if;
  return new;
end;
$$;

create trigger training_recordings_within_account_limit
  before insert on public.training_recordings
  for each row execute function public.enforce_recording_limit();

-- Only the triggers call them.
revoke execute on function public.enforce_session_limit() from public, anon, authenticated;
revoke execute on function public.enforce_recording_limit() from public, anon, authenticated;
