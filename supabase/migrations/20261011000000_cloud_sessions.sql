-- =============================================================================
-- Cloud sessions (S6.2): what the device already stores for a session can now
-- be kept in the cloud, behind a second factor.
--
--   * ADR-10: reading or writing health data needs a session at aal2 (TOTP),
--     enforced here by row level security and again by the API.
--   * ADR-18: sync consent lives in profiles; the session id is the UUID v7
--     the device generated, so sending a session twice never duplicates it.
--   * ADR-23 and ADR-27: self-ratings from 0 to 10 and the goals of the plan.
--
-- The API calls the functions below with the person's own token, so every
-- statement in them runs under row level security (security invoker).
-- =============================================================================

-- 1. Sync consent: the instant it was given; null while it has not been, or once withdrawn.
alter table public.profiles
  add column sync_consent_at timestamptz;

-- 2. sessions: the fields of a session stored on the device.
alter table public.sessions
  add column listened_seconds numeric not null default 0
    constraint sessions_listened_seconds_not_negative check (listened_seconds >= 0),
  add column source_kind text
    constraint sessions_source_kind_known
      check (source_kind in ('simulator', 'recording', 'ble', 'camera')),
  add column rating_before smallint
    constraint sessions_rating_before_in_scale check (rating_before between 0 and 10),
  add column rating_after smallint
    constraint sessions_rating_after_in_scale check (rating_after between 0 and 10),
  add constraint sessions_final_state_known
    check (final_state in ('high', 'low', 'uncertain'));

-- The device names the session; the database never invents an id.
alter table public.sessions
  alter column id drop default;

-- 3. session_metrics: the states the app estimates, and whether the sample was of good quality.
alter table public.session_metrics
  add column good_quality boolean not null default true,
  add constraint session_metrics_estimated_state_known
    check (estimated_state in ('high', 'low', 'uncertain'));

-- 4. Second factor on health data. Restrictive policies are combined with AND
-- with the ownership policies of the initial migration: both must hold.
create policy "Health data requires a second factor"
  on public.sessions as restrictive
  for all
  to authenticated
  using ((select auth.jwt() ->> 'aal') = 'aal2')
  with check ((select auth.jwt() ->> 'aal') = 'aal2');

create policy "Health data requires a second factor"
  on public.session_metrics as restrictive
  for all
  to authenticated
  using ((select auth.jwt() ->> 'aal') = 'aal2')
  with check ((select auth.jwt() ->> 'aal') = 'aal2');

-- 5. save_session: stores a finished session, its plan and its metrics in one
-- transaction. Returns true when it created the session and false when it
-- already existed; then only the rating given after listening is updated,
-- because it is the one field the person fills in later.
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
    listened_seconds, source_kind, rating_before, rating_after
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

-- 6. list_sessions: the person's sessions without their metrics, newest first.
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
      s.rating_before,
      s.rating_after
    from public.sessions s
    left join public.plans p on p.id = s.plan_id
    where s.owner_id = (select auth.uid())
    order by s.started_at desc
    limit p_limit
  ) as listed;
$$;

-- 7. get_session: one session with its metrics in order, or null when the
-- person has no session with that id.
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

-- 8. delete_session: removes a session (its metrics fall in cascade) and the
-- plan it was created with, unless another session still uses that plan.
-- Returns whether there was a session to delete.
create or replace function public.delete_session(p_id uuid)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_plan uuid;
begin
  delete from public.sessions
  where sessions.id = p_id and sessions.owner_id = (select auth.uid())
  returning sessions.plan_id into v_plan;

  if not found then
    return false;
  end if;

  delete from public.plans
  where plans.id = v_plan
    and not exists (select 1 from public.sessions where sessions.plan_id = plans.id);

  return true;
end;
$$;

-- 9. Only signed-in people may call the functions; without a token there is nobody to act for.
revoke execute on function public.save_session(jsonb) from public, anon;
revoke execute on function public.list_sessions(integer) from public, anon;
revoke execute on function public.get_session(uuid) from public, anon;
revoke execute on function public.delete_session(uuid) from public, anon;
grant execute on function public.save_session(jsonb) to authenticated;
grant execute on function public.list_sessions(integer) to authenticated;
grant execute on function public.get_session(uuid) to authenticated;
grant execute on function public.delete_session(uuid) to authenticated;
