-- =============================================================================
-- Database limits (security audit, SEC-P5): the database repeats the limits
-- the API checks (app/constants.py and app/schemas.py).
--
-- Why: the anon key is public and row level security lets a signed-in person
-- insert their own rows straight through PostgREST (sessions, session_metrics,
-- training_recordings, profiles, plans) or call save_session and
-- save_training_recording without going through the API. Ownership and the
-- second factor were already enforced here; the size and range of the values
-- were not, so a person could store absurd figures or bloated rows in their
-- own account. Direct inserts stay granted on purpose: the functions are
-- security invoker and need them.
--
--   * Constraints are added NOT VALID: they hold for every row inserted or
--     updated from now on, without failing on rows already stored.
--   * Every bound is the API's own, or wider where the API has none, so the
--     API never sends something the database refuses.
--   * save_session is that of 20261020000000_pulse_only_sessions.sql plus the
--     most samples a session may have: a count no row constraint can see.
--     Ownership, consent, the second factor and its permissions are unchanged.
-- =============================================================================

-- 1. profiles: the API never writes these columns; the bounds only stop bloat.
-- handle_new_user copies the alias of the sign-up metadata, so a longer one now
-- stops that sign-up; the app sends none.
alter table public.profiles
  add constraint profiles_alias_length check (char_length(alias) <= 60) not valid,
  add constraint profiles_preferences_size
    check (jsonb_typeof(preferences) = 'object' and octet_length(preferences::text) <= 8192) not valid;

-- 2. plans: MAX_PLANNED_MINUTES and the goal catalog (schemas.Goal, at most one of each).
alter table public.plans
  add constraint plans_duration_within_limit check (duration_minutes <= 240) not valid,
  add constraint plans_goals_known check (
    cardinality(goals) <= 5
    and goals <@ array['relax', 'rest_before_sleep', 'calm_after_activity', 'slow_breathing', 'pause']::text[]
  ) not valid,
  add constraint plans_initial_params_size
    check (jsonb_typeof(initial_params) = 'object' and octet_length(initial_params::text) <= 8192) not valid;

-- 3. sessions: MAX_LISTENED_SECONDS and SUMMARY_MAX_CHARS. Comparing also
-- refuses NaN and Infinity, which a numeric column would otherwise accept.
alter table public.sessions
  add constraint sessions_listened_seconds_within_limit check (listened_seconds <= 86400) not valid,
  add constraint sessions_summary_length check (char_length(summary) <= 700) not valid;

-- 4. session_metrics: the bounds of a sample (schemas.Sample). The second
-- cannot pass the longest session, which also caps the rows of one session.
alter table public.session_metrics
  add constraint session_metrics_second_within_limit check (second <= 86400) not valid,
  add constraint session_metrics_mean_hr_within_limit check (mean_hr <= 250) not valid,
  add constraint session_metrics_rmssd_within_limit check (rmssd <= 1000) not valid,
  add constraint session_metrics_sdnn_within_limit check (sdnn <= 1000) not valid,
  -- The ratio has no natural ceiling (HF may be tiny), so it only has to be a number.
  add constraint session_metrics_lf_hf_ratio_finite check (lf_hf_ratio < 'Infinity'::numeric) not valid;

-- 5. training_recordings: the stages of the protocol in order, each with the
-- fields of schemas.RecordingStage and no other, within MAX_STAGE_SECONDS,
-- MAX_STAGE_BEATS and MAX_RR_MS. A beat cannot end after the longest stage.
alter table public.training_recordings
  add constraint training_recordings_stages_in_order check (
    segments -> 0 ->> 'label' = 'rest'
    and segments -> 1 ->> 'label' = 'slow_breathing'
    and segments -> 2 ->> 'label' = 'demanding_task'
  ) not valid,
  add constraint training_recordings_stages_within_limits check (
    not jsonb_path_exists(segments, 'lax $[*] ? (
      !(exists(@.planned_seconds)) || !(exists(@.beat_times_ms)) || !(exists(@.rr_ms))
      || @.planned_seconds.type() != "number" || @.planned_seconds < 1 || @.planned_seconds > 3600
      || @.beat_times_ms.type() != "array" || @.rr_ms.type() != "array"
      || @.rr_ms.size() > 12000 || @.rr_ms.size() != @.beat_times_ms.size()
    )')
    and not jsonb_path_exists(segments, 'lax $[*].keyvalue() ? (
      @.key != "label" && @.key != "planned_seconds" && @.key != "beat_times_ms" && @.key != "rr_ms"
    )')
    and not jsonb_path_exists(segments, 'lax $[*].rr_ms[*] ? (@.type() != "number" || @ <= 0 || @ > 10000)')
    and not jsonb_path_exists(segments, 'lax $[*].beat_times_ms[*] ? (@.type() != "number" || @ < 0 || @ > 3600000)')
  ) not valid;

-- 6. save_session: as before, plus MAX_SESSION_SAMPLES. A session sent again
-- still only updates its rating, so the count is checked before storing a new one.
create or replace function public.save_session(p_session jsonb)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_owner uuid := (select auth.uid());
  v_id uuid := (p_session ->> 'id')::uuid;
  v_plan uuid;
begin
  if not exists (
    select 1 from public.profiles
    where profiles.id = v_owner and profiles.sync_consent_at is not null
  ) then
    raise exception 'consent_required' using errcode = 'P0001';
  end if;

  if exists (select 1 from public.sessions where sessions.id = v_id) then
    update public.sessions
    set rating_after = coalesce((p_session ->> 'rating_after')::smallint, sessions.rating_after)
    where sessions.id = v_id;
    return false;
  end if;

  if jsonb_typeof(p_session -> 'samples') = 'array' and jsonb_array_length(p_session -> 'samples') > 2880 then
    raise exception 'too_many_samples' using errcode = '23514';
  end if;

  insert into public.plans (owner_id, goals, duration_minutes)
  values (
    v_owner,
    array(select jsonb_array_elements_text(p_session -> 'goals')),
    (p_session ->> 'planned_minutes')::integer
  )
  returning plans.id into v_plan;

  insert into public.sessions (
    id, owner_id, plan_id, started_at, ended_at, final_state,
    listened_seconds, source_kind, signal_mode, rating_before, rating_after
  )
  values (
    v_id,
    v_owner,
    v_plan,
    (p_session ->> 'started_at')::timestamptz,
    (p_session ->> 'ended_at')::timestamptz,
    p_session ->> 'final_state',
    (p_session ->> 'listened_seconds')::numeric,
    p_session ->> 'source_kind',
    coalesce(p_session ->> 'signal_mode', 'hrv'),
    (p_session ->> 'rating_before')::smallint,
    (p_session ->> 'rating_after')::smallint
  );

  insert into public.session_metrics (
    session_id, second, mean_hr, rmssd, sdnn, lf_hf_ratio, estimated_state, good_quality
  )
  select v_id, m.second, m.mean_hr, m.rmssd, m.sdnn, m.lf_hf_ratio, m.estimated_state, m.good_quality
  from jsonb_to_recordset(p_session -> 'samples') as m(
    second integer,
    mean_hr numeric,
    rmssd numeric,
    sdnn numeric,
    lf_hf_ratio numeric,
    estimated_state text,
    good_quality boolean
  );

  return true;
end;
$$;

-- 7. Permissions as before: create or replace keeps them, restated so this file stands alone.
revoke execute on function public.save_session(jsonb) from public, anon;
grant execute on function public.save_session(jsonb) to authenticated;
