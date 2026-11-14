-- Checks the account storage limits against a local database (see README):
-- at 2000 sessions or 20 guided recordings a new one is refused with 54000,
-- a session already stored can still be sent again, and another account is
-- not affected.
--
-- It runs inside one transaction that is rolled back, so it leaves nothing
-- behind. Requests are simulated the way PostgREST does it: the
-- `authenticated` role plus the claims of the person's token. The rows that
-- fill the account up to the limit are written as the database owner.
\set ON_ERROR_STOP on

begin;

insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-00000000000a', 'ana@example.test'),
  ('00000000-0000-4000-8000-00000000000b', 'ben@example.test');
update public.profiles set sync_consent_at = now();

select set_config('test.ana_aal2', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated","aal":"aal2"}', true);
select set_config('test.ben_aal2', '{"sub":"00000000-0000-4000-8000-00000000000b","role":"authenticated","aal":"aal2"}', true);
select set_config('test.session', '{
  "id": "017f22e2-79b0-7000-8000-000000000001",
  "started_at": "2026-10-07T10:00:00Z",
  "ended_at": "2026-10-07T10:20:00Z",
  "planned_minutes": 20,
  "goals": ["relax"],
  "listened_seconds": 1200,
  "source_kind": "ble",
  "final_state": null,
  "rating_before": 4,
  "rating_after": null,
  "samples": []
}', true);
select set_config('test.segments', '[
  {"label": "rest", "planned_seconds": 300, "beat_times_ms": [800, 1600], "rr_ms": [800, 800]},
  {"label": "slow_breathing", "planned_seconds": 180, "beat_times_ms": [900], "rr_ms": [900]},
  {"label": "demanding_task", "planned_seconds": 180, "beat_times_ms": [650], "rr_ms": [650]}
]', true);

-- 1. Ana stores one session herself, then her account is filled up to 2000.
select set_config('request.jwt.claims', current_setting('test.ana_aal2'), true);
set local role authenticated;
do $$
begin
  assert public.save_session(current_setting('test.session')::jsonb), 'the first session is stored';
end $$;
reset role;
insert into public.sessions (id, owner_id, started_at, ended_at, listened_seconds, source_kind)
select gen_random_uuid(), '00000000-0000-4000-8000-00000000000a', now(), now(), 60, 'ble'
from generate_series(1, 1999);

-- 2. At the limit a new session is refused with 54000, and nothing of it is kept.
set local role authenticated;
do $$
declare
  v_state text := 'no error';
begin
  begin
    perform public.save_session(jsonb_set(current_setting('test.session')::jsonb, '{id}', '"017f22e2-79b0-7000-8000-000000000002"'));
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '54000', 'at the limit: expected 54000, got ' || v_state;
  assert (select count(*) from public.sessions) = 2000, 'still 2000 sessions';
  assert (select count(*) from public.plans where owner_id = (select auth.uid())) = 1, 'the refused session left no plan';
end $$;

-- 3. Sending a stored session again only updates it, so it still works at the limit.
do $$
begin
  assert not public.save_session(jsonb_set(current_setting('test.session')::jsonb, '{rating_after}', '7')),
    'a stored session is sent again';
  assert (select rating_after from public.sessions where id = '017f22e2-79b0-7000-8000-000000000001') = 7, 'its later rating is kept';
end $$;

-- 4. Another account is not affected by Ana's limit.
select set_config('request.jwt.claims', current_setting('test.ben_aal2'), true);
do $$
begin
  assert public.save_session(jsonb_set(current_setting('test.session')::jsonb, '{id}', '"017f22e2-79b0-7000-8000-000000000003"')),
    'another account stores its session';
end $$;

-- 5. Guided recordings: 20 per account, then 54000.
reset role;
insert into public.training_recordings (id, owner_id, participant, recorded_at, source_kind, segments)
select gen_random_uuid(), '00000000-0000-4000-8000-00000000000a', '5f0c1d4e-1111-4222-8333-444455556666', now(), 'ble',
  current_setting('test.segments')::jsonb
from generate_series(1, 20);
select set_config('request.jwt.claims', current_setting('test.ana_aal2'), true);
set local role authenticated;
do $$
declare
  v_state text := 'no error';
begin
  begin
    perform public.save_training_recording(jsonb_build_object(
      'id', '017f22e2-79b0-7000-8000-0000000000c9',
      'participant', '5f0c1d4e-1111-4222-8333-444455556666',
      'recorded_at', '2026-10-12T10:00:00Z',
      'source_kind', 'ble',
      'consent', true,
      'segments', current_setting('test.segments')::jsonb
    ));
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '54000', 'recordings at the limit: expected 54000, got ' || v_state;
end $$;

-- 6. Nobody can call the trigger functions directly.
do $$
begin
  assert not has_function_privilege('authenticated', 'public.enforce_session_limit()', 'execute'), 'authenticated cannot execute it';
  assert not has_function_privilege('anon', 'public.enforce_recording_limit()', 'execute'), 'anon cannot execute it';
end $$;

reset role;
select 'account limits: all checks passed' as result;
rollback;
