-- =============================================================================
-- Pulse-only sessions (ADR-51): many watches send the pulse without the time
-- between beats. A session records which kind of signal it had, so the app
-- never shows an empty variability as if it were a measurement.
--
-- Sessions stored before this migration had beat intervals: the default says
-- so. The functions below are those of 20261011000000_cloud_sessions.sql with
-- the new column added; ownership, consent and the second factor are unchanged.
-- =============================================================================

-- 1. sessions: the kind of signal the source sent.
alter table public.sessions
  add column signal_mode text not null default 'hrv'
    constraint sessions_signal_mode_known check (signal_mode in ('hrv', 'hr_only'));

-- 2. save_session: as before, plus the signal mode (intervals when the body does not say).
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

-- 3. list_sessions: as before, plus the signal mode.
create or replace function public.list_sessions(p_limit integer)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  select coalesce(jsonb_agg(to_jsonb(listed) order by listed.started_at desc), '[]'::jsonb)
  from (
    select
      s.id,
      s.started_at,
      coalesce(s.ended_at, s.started_at) as ended_at,
      -- A session outlives its plan; without one, the time listened stands in for the plan.
      coalesce(p.duration_minutes, greatest(1, ceil(s.listened_seconds / 60.0)::integer)) as planned_minutes,
      coalesce(p.goals, '{}'::text[]) as goals,
      s.listened_seconds,
      s.source_kind,
      s.signal_mode,
      s.rating_before,
      s.rating_after
    from public.sessions s
    left join public.plans p on p.id = s.plan_id
    where s.owner_id = (select auth.uid())
    order by s.started_at desc
    limit p_limit
  ) as listed;
$$;

-- 4. get_session: as before, plus the signal mode.
create or replace function public.get_session(p_id uuid)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  select to_jsonb(found)
  from (
    select
      s.id,
      s.started_at,
      coalesce(s.ended_at, s.started_at) as ended_at,
      coalesce(p.duration_minutes, greatest(1, ceil(s.listened_seconds / 60.0)::integer)) as planned_minutes,
      coalesce(p.goals, '{}'::text[]) as goals,
      s.listened_seconds,
      s.source_kind,
      s.signal_mode,
      s.rating_before,
      s.rating_after,
      (
        select coalesce(
          jsonb_agg(
            jsonb_build_object(
              'second', m.second,
              'mean_hr', m.mean_hr,
              'rmssd', m.rmssd,
              'sdnn', m.sdnn,
              'lf_hf_ratio', m.lf_hf_ratio,
              'estimated_state', m.estimated_state,
              'good_quality', m.good_quality
            )
            order by m.second
          ),
          '[]'::jsonb
        )
        from public.session_metrics m
        where m.session_id = s.id
      ) as samples
    from public.sessions s
    left join public.plans p on p.id = s.plan_id
    where s.id = p_id and s.owner_id = (select auth.uid())
  ) as found;
$$;
