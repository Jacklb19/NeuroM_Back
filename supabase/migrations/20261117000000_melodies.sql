-- =============================================================================
-- Saved melodies (P-17, ADR-66): the pieces a person keeps from the listening
-- screen, so they can hear them again on any device.
--
-- A melody is a seed and the choices that make the synthesizer play it again,
-- plus the point where it was saved. It holds no pulse and nothing about the
-- person but the name they give it, so it is not health data: it needs an
-- account, but no second factor and no sync consent (unlike ADR-10 and ADR-18).
--
--   * The id is the UUID v7 the device generated: saving twice never duplicates.
--   * Saving a melody that exists keeps the newest version by updated_at, so two
--     devices that rename it end with the same name whatever the order.
--   * At most 50 per account, with the same trigger as ADR-63 (SQLSTATE 54000,
--     which the API answers as 409 limit_reached).
--   * Deleting the account deletes its melodies (on delete cascade).
--
-- The API calls the functions below with the person's own token, so every
-- statement in them runs under row level security (security invoker).
-- =============================================================================

-- 1. The table. Every column repeats the catalogs and ranges of the app (SEC-P5).
create table public.melodies (
  id uuid primary key,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  name text not null
    constraint melodies_name_length check (char_length(name) between 1 and 60 and name = btrim(name)),
  seed integer not null
    constraint melodies_seed_not_negative check (seed >= 0),
  instrument text not null
    constraint melodies_instrument_known check (instrument in ('warm', 'piano', 'bowls')),
  ambience text not null
    constraint melodies_ambience_known check (ambience in ('none', 'rain', 'sea', 'wind')),
  profile text not null
    constraint melodies_profile_known check (profile in ('original', 'minimal', 'immersive')),
  level text not null
    constraint melodies_level_known check (level in ('high', 'intermediate', 'target', 'sleep')),
  position_seconds integer not null
    constraint melodies_position_in_range check (position_seconds between 0 and 7200),
  music_mix real not null
    constraint melodies_music_mix_in_range check (music_mix between 0 and 1),
  ambience_mix real not null
    constraint melodies_ambience_mix_in_range check (ambience_mix between 0 and 1),
  synthesis_version smallint not null
    constraint melodies_synthesis_version_positive check (synthesis_version >= 1),
  created_at timestamptz not null,
  updated_at timestamptz not null,
  constraint melodies_updated_after_created check (updated_at >= created_at)
);

create index idx_melodies_owner_updated on public.melodies(owner_id, updated_at desc);

-- 2. Each account reads and writes only its own melodies.
alter table public.melodies enable row level security;

create policy "Users can view own melodies"
  on public.melodies for select
  to authenticated
  using ((select auth.uid()) = owner_id);

create policy "Users can insert own melodies"
  on public.melodies for insert
  to authenticated
  with check ((select auth.uid()) = owner_id);

create policy "Users can update own melodies"
  on public.melodies for update
  to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

create policy "Users can delete own melodies"
  on public.melodies for delete
  to authenticated
  using ((select auth.uid()) = owner_id);

-- 3. At most 50 per account (ADR-63): the same lock and count as sessions and recordings.
create or replace function public.enforce_melody_limit()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('melodies:' || new.owner_id::text, 0));
  if (select count(*) from public.melodies where owner_id = new.owner_id) >= 50 then
    raise exception 'melody_limit_reached' using errcode = '54000';
  end if;
  return new;
end;
$$;

create trigger melodies_within_account_limit
  before insert on public.melodies
  for each row execute function public.enforce_melody_limit();

revoke execute on function public.enforce_melody_limit() from public, anon, authenticated;

-- 4. save_melody: creates the melody, or keeps the newest version of one that
-- exists. Returns true when it created it. An id that belongs to another
-- account is invisible here, so the insert fails with 23505 (session_conflict
-- in the API, as for sessions).
create or replace function public.save_melody(p_melody jsonb)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_id uuid := (p_melody ->> 'id')::uuid;
  v_updated timestamptz := (p_melody ->> 'updated_at')::timestamptz;
begin
  if exists (select 1 from public.melodies where melodies.id = v_id) then
    update public.melodies
    set name = p_melody ->> 'name',
        music_mix = (p_melody ->> 'music_mix')::real,
        ambience_mix = (p_melody ->> 'ambience_mix')::real,
        updated_at = v_updated
    where melodies.id = v_id and melodies.updated_at < v_updated;
    return false;
  end if;

  insert into public.melodies (
    id, owner_id, name, seed, instrument, ambience, profile, level, position_seconds,
    music_mix, ambience_mix, synthesis_version, created_at, updated_at
  )
  values (
    v_id,
    (select auth.uid()),
    p_melody ->> 'name',
    (p_melody ->> 'seed')::integer,
    p_melody ->> 'instrument',
    p_melody ->> 'ambience',
    p_melody ->> 'profile',
    p_melody ->> 'level',
    (p_melody ->> 'position_seconds')::integer,
    (p_melody ->> 'music_mix')::real,
    (p_melody ->> 'ambience_mix')::real,
    (p_melody ->> 'synthesis_version')::smallint,
    (p_melody ->> 'created_at')::timestamptz,
    v_updated
  );
  return true;
end;
$$;

-- 5. list_melodies: every melody of the account, most recently changed first.
-- The limit of the account is small, so the whole list fits in one answer.
create or replace function public.list_melodies()
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  select coalesce(jsonb_agg(to_jsonb(listed) order by listed.updated_at desc), '[]'::jsonb)
  from (
    select id, name, seed, instrument, ambience, profile, level, position_seconds,
           music_mix, ambience_mix, synthesis_version, created_at, updated_at
    from public.melodies
    where owner_id = (select auth.uid())
  ) as listed;
$$;

-- 6. delete_melody: true when the account had a melody with that id.
create or replace function public.delete_melody(p_id uuid)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
begin
  delete from public.melodies
  where melodies.id = p_id and melodies.owner_id = (select auth.uid());
  return found;
end;
$$;

-- 7. Only signed-in people may call them; without a token there is nobody to act for.
revoke execute on function public.save_melody(jsonb) from public, anon;
revoke execute on function public.list_melodies() from public, anon;
revoke execute on function public.delete_melody(uuid) from public, anon;
grant execute on function public.save_melody(jsonb) to authenticated;
grant execute on function public.list_melodies() to authenticated;
grant execute on function public.delete_melody(uuid) to authenticated;
