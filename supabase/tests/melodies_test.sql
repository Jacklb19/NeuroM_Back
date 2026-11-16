-- Checks the saved melodies against a local database (see README): each
-- account sees only its own, saving again keeps the newest version, they need
-- no second factor, the limit of 50 holds with 54000, and deleting the account
-- deletes them.
--
-- It runs inside one transaction that is rolled back, so it leaves nothing
-- behind. Requests are simulated the way PostgREST does it: the
-- `authenticated` role plus the claims of the person's token.
\set ON_ERROR_STOP on

begin;

insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-0000000000a1', 'ana@example.test'),
  ('00000000-0000-4000-8000-0000000000b1', 'ben@example.test');

-- Plain accounts, without the second factor: melodies are not health data.
select set_config('test.ana', '{"sub":"00000000-0000-4000-8000-0000000000a1","role":"authenticated","aal":"aal1"}', true);
select set_config('test.ben', '{"sub":"00000000-0000-4000-8000-0000000000b1","role":"authenticated","aal":"aal1"}', true);
select set_config('test.melody', '{
  "id": "019a8c8e-2b10-7000-8000-000000000001",
  "name": "Bosque de niebla",
  "seed": 123456,
  "instrument": "bowls",
  "ambience": "wind",
  "profile": "immersive",
  "level": "target",
  "position_seconds": 720,
  "duration_minutes": 30,
  "music_mix": 1,
  "ambience_mix": 0.6,
  "synthesis_version": 1,
  "created_at": "2026-11-16T21:00:00Z",
  "updated_at": "2026-11-16T21:00:00Z"
}', true);

-- 1. Ana saves a melody; saving it again does not duplicate it.
select set_config('request.jwt.claims', current_setting('test.ana'), true);
set local role authenticated;
do $$
begin
  assert public.save_melody(current_setting('test.melody')::jsonb), 'a new melody is created';
  assert not public.save_melody(current_setting('test.melody')::jsonb), 'the same melody is not created twice';
  assert jsonb_array_length(public.list_melodies()) = 1, 'Ana has one melody';
end $$;

-- 2. A newer version renames it; an older one arriving later does not undo it.
do $$
declare
  v_melody jsonb := current_setting('test.melody')::jsonb;
begin
  perform public.save_melody(v_melody || '{"name": "Renombrada", "updated_at": "2026-11-16T22:00:00Z"}');
  perform public.save_melody(v_melody || '{"name": "Vieja", "updated_at": "2026-11-16T21:30:00Z"}');
  assert (public.list_melodies() -> 0 ->> 'name') = 'Renombrada', 'the newest name is kept';
  assert (public.list_melodies() -> 0 ->> 'seed')::integer = 123456, 'what plays the melody never changes';
end $$;

-- 3. Ben sees nothing of Ana, cannot delete her melody nor take its id.
reset role;
select set_config('request.jwt.claims', current_setting('test.ben'), true);
set local role authenticated;
do $$
declare
  v_state text := 'no error';
begin
  assert jsonb_array_length(public.list_melodies()) = 0, 'Ben sees no melody of Ana';
  assert not public.delete_melody('019a8c8e-2b10-7000-8000-000000000001'), 'Ben cannot delete it';
  begin
    perform public.save_melody(current_setting('test.melody')::jsonb);
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '23505', 'an id of another account is a conflict, got ' || v_state;
end $$;

-- 4. A value outside the catalogs is refused by the table itself.
do $$
declare
  v_state text := 'no error';
begin
  begin
    perform public.save_melody(jsonb_set(current_setting('test.melody')::jsonb, '{id}', '"019a8c8e-2b10-7000-8000-0000000000b2"') || '{"instrument": "trumpet"}');
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '23514', 'an unknown instrument breaks a check, got ' || v_state;
end $$;

-- 4b. The length is one of the four, and the saved point lies within it (ADR-67).
do $$
declare
  v_state text := 'no error';
begin
  begin
    perform public.save_melody(jsonb_set(current_setting('test.melody')::jsonb, '{id}', '"019a8c8e-2b10-7000-8000-0000000000b3"') || '{"duration_minutes": 15, "position_seconds": 1000}');
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '23514', 'a point past the end breaks a check, got ' || v_state;
end $$;

-- 5. Ana's account is filled up to 50; the next one is refused with 54000.
reset role;
insert into public.melodies (id, owner_id, name, seed, instrument, ambience, profile, level, position_seconds,
  music_mix, ambience_mix, synthesis_version, created_at, updated_at)
select gen_random_uuid(), '00000000-0000-4000-8000-0000000000a1', 'Relleno', 1, 'warm', 'none', 'original', 'target', 0,
  1, 1, 1, now(), now()
from generate_series(1, 49);
select set_config('request.jwt.claims', current_setting('test.ana'), true);
set local role authenticated;
do $$
declare
  v_state text := 'no error';
begin
  begin
    perform public.save_melody(jsonb_set(current_setting('test.melody')::jsonb, '{id}', '"019a8c8e-2b10-7000-8000-000000000099"'));
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '54000', 'the 51st melody is refused, got ' || v_state;
  -- Rows written before the length existed take the default of 30 minutes.
  assert (select bool_and((item ->> 'duration_minutes')::integer = 30) from jsonb_array_elements(public.list_melodies()) item),
    'every melody is listed with its length';
  assert public.delete_melody('019a8c8e-2b10-7000-8000-000000000001'), 'Ana deletes her own melody';
end $$;

-- 6. Deleting the account deletes its melodies.
reset role;
delete from auth.users where id = '00000000-0000-4000-8000-0000000000a1';
do $$
begin
  assert not exists (select 1 from public.melodies where owner_id = '00000000-0000-4000-8000-0000000000a1'), 'nothing of Ana is left';
end $$;

select 'melodies: all checks passed' as result;

rollback;
