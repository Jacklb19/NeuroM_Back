-- =============================================================================
-- Length of a saved melody (ADR-67): the listening screen becomes a player
-- with a time bar, so a melody needs a length. It is the length the piece
-- was being played for when it was saved: 15, 30, 45 or 60 minutes.
--
-- A new migration rather than a change to 20261117000000_melodies.sql, which
-- may already be applied. Melodies saved before take 30 minutes, the default
-- length of the listening screen. save_melody and list_melodies are created
-- again with the new column; ownership, the limit and the rest do not change.
-- =============================================================================

alter table public.melodies
  add column duration_minutes smallint not null default 30
    constraint melodies_duration_known check (duration_minutes in (15, 30, 45, 60));

-- The saved point lies within the piece.
alter table public.melodies
  add constraint melodies_position_within_duration check (position_seconds <= duration_minutes * 60) not valid;

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
    id, owner_id, name, seed, instrument, ambience, profile, level, position_seconds, duration_minutes,
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
    (p_melody ->> 'duration_minutes')::smallint,
    (p_melody ->> 'music_mix')::real,
    (p_melody ->> 'ambience_mix')::real,
    (p_melody ->> 'synthesis_version')::smallint,
    (p_melody ->> 'created_at')::timestamptz,
    v_updated
  );
  return true;
end;
$$;

create or replace function public.list_melodies()
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
  select coalesce(jsonb_agg(to_jsonb(listed) order by listed.updated_at desc), '[]'::jsonb)
  from (
    select id, name, seed, instrument, ambience, profile, level, position_seconds, duration_minutes,
           music_mix, ambience_mix, synthesis_version, created_at, updated_at
    from public.melodies
    where owner_id = (select auth.uid())
  ) as listed;
$$;

-- Created again: the grants of 20261117000000_melodies.sql are kept by `create or replace`,
-- and repeated here so this file reads complete.
revoke execute on function public.save_melody(jsonb) from public, anon;
revoke execute on function public.list_melodies() from public, anon;
grant execute on function public.save_melody(jsonb) to authenticated;
grant execute on function public.list_melodies() to authenticated;
