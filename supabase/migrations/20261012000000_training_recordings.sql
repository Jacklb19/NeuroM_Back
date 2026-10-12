-- =============================================================================
-- Guided recordings for training, and the published versions of the classifier
-- (ADR-32, ADR-33).
--
--   * A person may send the guided recordings they made in the app, with an
--     explicit consent for each one. They are heartbeats, the most sensitive
--     data the cloud holds, so they sit behind the second factor (ADR-10)
--     like sessions do, and their owner can delete them at any time.
--   * Training runs outside the app. It reads the recordings and its own
--     files with the service role, which bypasses row level security; no
--     policy lets a signed-in person read anybody else's recording.
--   * A version of the classifier is published by inserting it into
--     model_versions with the service role; the app asks for the latest one.
--
-- The API calls the functions below with the person's own token, so every
-- statement in them runs under row level security (security invoker).
-- =============================================================================

-- 1. training_recordings: one guided recording a person chose to send.
create table public.training_recordings (
  -- The UUID v7 the device gave the recording, so sending it twice never duplicates it.
  id uuid primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  -- Random code of the device: groups the recordings of one person without naming them.
  participant text not null
    constraint training_recordings_participant_is_a_code check (participant ~ '^[0-9a-f-]{36}$'),
  recorded_at timestamptz not null,
  -- The instant the person agreed to send this recording.
  consent_at timestamptz not null default now(),
  -- Only sources that measure a person; the simulator and the samples teach nothing.
  source_kind text not null
    constraint training_recordings_source_measures_a_person check (source_kind in ('ble', 'camera')),
  -- The three stages of the protocol, each with its label and its heartbeats.
  segments jsonb not null
    constraint training_recordings_has_the_three_stages
      check (jsonb_typeof(segments) = 'array' and jsonb_array_length(segments) = 3),
  created_at timestamptz not null default now()
);

create index idx_training_recordings_owner on public.training_recordings(owner_id, recorded_at desc);

alter table public.training_recordings enable row level security;

create policy "People can view their own recordings"
  on public.training_recordings for select
  to authenticated
  using ((select auth.uid()) = owner_id);

create policy "People can send their own recordings"
  on public.training_recordings for insert
  to authenticated
  with check ((select auth.uid()) = owner_id);

create policy "People can delete their own recordings"
  on public.training_recordings for delete
  to authenticated
  using ((select auth.uid()) = owner_id);

create policy "Heartbeats require a second factor"
  on public.training_recordings as restrictive
  for all
  to authenticated
  using ((select auth.jwt() ->> 'aal') = 'aal2')
  with check ((select auth.jwt() ->> 'aal') = 'aal2');

revoke all on table public.training_recordings from anon;
revoke update, truncate on table public.training_recordings from authenticated;

-- 2. save_training_recording: stores a recording the person agreed to send.
-- Returns true when it stored it and false when it was already there.
create or replace function public.save_training_recording(p_recording jsonb)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_id uuid := (p_recording ->> 'id')::uuid;
begin
  if coalesce((p_recording ->> 'consent')::boolean, false) is not true then
    raise exception 'consent_required' using errcode = 'P0001';
  end if;

  if exists (select 1 from public.training_recordings where training_recordings.id = v_id) then
    return false;
  end if;

  insert into public.training_recordings (id, owner_id, participant, recorded_at, source_kind, segments)
  values (
    v_id,
    (select auth.uid()),
    p_recording ->> 'participant',
    (p_recording ->> 'recorded_at')::timestamptz,
    p_recording ->> 'source_kind',
    p_recording -> 'segments'
  );
  return true;
end;
$$;

-- 3. list_training_recordings: what the person has sent, newest first, without the heartbeats.
create or replace function public.list_training_recordings()
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  select coalesce(jsonb_agg(to_jsonb(listed) order by listed.recorded_at desc), '[]'::jsonb)
  from (
    select
      r.id,
      r.recorded_at,
      r.source_kind,
      (
        select coalesce(sum(jsonb_array_length(segment -> 'rr_ms')), 0)::integer
        from jsonb_array_elements(r.segments) as segment
      ) as beats
    from public.training_recordings r
    where r.owner_id = (select auth.uid())
  ) as listed;
$$;

-- 4. delete_training_recording: withdraws a recording. Returns whether there was one to delete.
create or replace function public.delete_training_recording(p_id uuid)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
begin
  delete from public.training_recordings
  where training_recordings.id = p_id and training_recordings.owner_id = (select auth.uid());
  return found;
end;
$$;

-- 5. model_versions: the classifier is a few numbers, so a version carries its
-- own file instead of pointing at one in storage.
alter table public.model_versions
  alter column storage_path drop not null,
  add column model jsonb;

-- 6. current_classifier: the latest published version with its file, or null
-- while none has been published (the app then keeps the model it ships with).
create or replace function public.current_classifier()
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  select jsonb_build_object(
    'version', v.version,
    'sha256', v.sha256,
    'published_at', v.published_at,
    'model', v.model
  )
  from public.model_versions v
  where v.model is not null
  order by v.published_at desc
  limit 1;
$$;

-- 7. training_assets: files the training job needs and that must not be public
-- (the heartbeats derived from the WESAD dataset). No policy: only the service
-- role, which bypasses row level security, can read or write it.
create table public.training_assets (
  name text primary key,
  content bytea not null,
  updated_at timestamptz not null default now()
);

alter table public.training_assets enable row level security;
revoke all on table public.training_assets from anon, authenticated;

-- 8. Only signed-in people may call the functions; without a token there is nobody to act for.
revoke execute on function public.save_training_recording(jsonb) from public, anon;
revoke execute on function public.list_training_recordings() from public, anon;
revoke execute on function public.delete_training_recording(uuid) from public, anon;
revoke execute on function public.current_classifier() from public, anon;
grant execute on function public.save_training_recording(jsonb) to authenticated;
grant execute on function public.list_training_recordings() to authenticated;
grant execute on function public.delete_training_recording(uuid) to authenticated;
grant execute on function public.current_classifier() to authenticated;
