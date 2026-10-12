-- Checks the cloud sessions migration against a local database (see README):
-- consent, the second factor, ownership, sending a session twice, deleting
-- a session and the cascade when the account is deleted.
--
-- It runs inside one transaction that is rolled back, so it leaves nothing
-- behind. Each request is simulated the way PostgREST does it: the
-- `authenticated` role plus the claims of the person's token.
\set ON_ERROR_STOP on

begin;

insert into auth.users (id, email) values
  ('00000000-0000-4000-8000-00000000000a', 'ana@example.test'),
  ('00000000-0000-4000-8000-00000000000b', 'ben@example.test');

select set_config('test.ana_aal2', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated","aal":"aal2"}', true);
select set_config('test.ana_aal1', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated","aal":"aal1"}', true);
select set_config('test.ben_aal2', '{"sub":"00000000-0000-4000-8000-00000000000b","role":"authenticated","aal":"aal2"}', true);
select set_config('test.session', '{
  "id": "017f22e2-79b0-7000-8000-000000000001",
  "started_at": "2026-10-07T10:00:00Z",
  "ended_at": "2026-10-07T10:20:00Z",
  "planned_minutes": 20,
  "goals": ["relax", "slow_breathing"],
  "listened_seconds": 1200,
  "source_kind": "simulator",
  "final_state": "low",
  "rating_before": 4,
  "rating_after": null,
  "samples": [
    {"second": 10, "mean_hr": 70.5, "rmssd": 31, "sdnn": 36, "lf_hf_ratio": null, "estimated_state": "low", "good_quality": true},
    {"second": 5, "mean_hr": 78, "rmssd": 22, "sdnn": 30, "lf_hf_ratio": 1.4, "estimated_state": null, "good_quality": true},
    {"second": 15, "mean_hr": null, "rmssd": null, "sdnn": null, "lf_hf_ratio": null, "estimated_state": null, "good_quality": false}
  ]
}', true);

-- 1. Without consent nothing is stored, even at aal2.
select set_config('request.jwt.claims', current_setting('test.ana_aal2'), true);
set local role authenticated;
do $$
declare
  v_error text := 'no error';
begin
  begin
    perform public.save_session(current_setting('test.session')::jsonb);
  exception when others then
    v_error := sqlerrm;
  end;
  assert v_error = 'consent_required', 'without consent: expected consent_required, got ' || v_error;
end $$;

-- 2. The person gives consent on their own profile; that is not health data, so aal1 is enough.
select set_config('request.jwt.claims', current_setting('test.ana_aal1'), true);
update public.profiles set sync_consent_at = now() where id = (select auth.uid());
do $$
begin
  assert (select count(*) from public.profiles where sync_consent_at is not null) = 1,
    'the person could not record their own consent';
end $$;

-- 3. With consent but without the second factor, the database refuses the session.
do $$
declare
  v_state text := 'no error';
begin
  begin
    perform public.save_session(current_setting('test.session')::jsonb);
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '42501', 'aal1: expected a row level security violation (42501), got ' || v_state;
end $$;

-- 4. At aal2 the session, its plan and its metrics are stored together.
select set_config('request.jwt.claims', current_setting('test.ana_aal2'), true);
do $$
begin
  assert public.save_session(current_setting('test.session')::jsonb), 'first send should create the session';
  assert (select count(*) from public.sessions) = 1, 'one session expected';
  assert (select count(*) from public.session_metrics) = 3, 'three metrics expected';
  assert (select goals from public.plans) = array['relax', 'slow_breathing'], 'goals belong to the plan';
  assert (select final_state from public.sessions) = 'low', 'final state stored';
end $$;

-- 5. Sending it again creates nothing; only the rating given afterwards is updated.
do $$
begin
  assert not public.save_session(
    jsonb_set(current_setting('test.session')::jsonb, '{rating_after}', '8')
  ), 'second send should report that the session already existed';
  assert (select count(*) from public.sessions) = 1, 'no duplicated session';
  assert (select count(*) from public.plans) = 1, 'no duplicated plan';
  assert (select count(*) from public.session_metrics) = 3, 'no duplicated metrics';
  assert (select rating_after from public.sessions) = 8, 'the later rating is kept';
  assert not public.save_session(current_setting('test.session')::jsonb), 'third send';
  assert (select rating_after from public.sessions) = 8, 'a send without rating does not erase it';
end $$;

-- 6. The list has no metrics; the detail has them in order.
do $$
declare
  v_list jsonb := public.list_sessions(10);
  v_detail jsonb := public.get_session('017f22e2-79b0-7000-8000-000000000001');
begin
  assert jsonb_array_length(v_list) = 1, 'one session listed';
  assert (v_list -> 0 ->> 'planned_minutes')::integer = 20, 'planned minutes come from the plan';
  assert v_list -> 0 -> 'goals' = '["relax", "slow_breathing"]'::jsonb, 'goals listed';
  assert not (v_list -> 0 ? 'samples'), 'the list carries no metrics';
  assert jsonb_array_length(v_detail -> 'samples') = 3, 'detail has the three samples';
  assert (v_detail -> 'samples' -> 0 ->> 'second')::integer = 5, 'samples ordered by second';
  assert (v_detail -> 'samples' -> 2 ->> 'good_quality')::boolean = false, 'quality flag kept';
  assert (v_detail ->> 'rating_after')::integer = 8, 'detail carries the ratings';
end $$;

-- 7. The same person without the second factor sees no health data.
select set_config('request.jwt.claims', current_setting('test.ana_aal1'), true);
do $$
begin
  assert (select count(*) from public.sessions) = 0, 'aal1 must not read sessions';
  assert (select count(*) from public.session_metrics) = 0, 'aal1 must not read metrics';
  assert public.list_sessions(10) = '[]'::jsonb, 'aal1 lists nothing';
  assert public.get_session('017f22e2-79b0-7000-8000-000000000001') is null, 'aal1 gets nothing';
  assert not public.delete_session('017f22e2-79b0-7000-8000-000000000001'), 'aal1 deletes nothing';
end $$;

-- 8. Another person, even at aal2 and with consent, cannot reach it.
reset role;
update public.profiles set sync_consent_at = now() where id = '00000000-0000-4000-8000-00000000000b';
select set_config('request.jwt.claims', current_setting('test.ben_aal2'), true);
set local role authenticated;
do $$
declare
  v_state text := 'no error';
begin
  assert public.list_sessions(10) = '[]'::jsonb, 'another person lists nothing';
  assert public.get_session('017f22e2-79b0-7000-8000-000000000001') is null, 'another person gets nothing';
  assert not public.delete_session('017f22e2-79b0-7000-8000-000000000001'), 'another person deletes nothing';
  begin
    perform public.save_session(current_setting('test.session')::jsonb);
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '23505', 'reusing someone else''s id: expected a conflict (23505), got ' || v_state;
end $$;

-- 9. Nobody without a token can call the functions.
reset role;
set local role anon;
do $$
declare
  v_state text := 'no error';
begin
  begin
    perform public.list_sessions(10);
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '42501', 'anon: expected permission denied (42501), got ' || v_state;
end $$;

-- 10. Deleting a session takes its metrics and its plan with it.
reset role;
select set_config('request.jwt.claims', current_setting('test.ana_aal2'), true);
set local role authenticated;
do $$
begin
  assert public.delete_session('017f22e2-79b0-7000-8000-000000000001'), 'the owner deletes the session';
  assert not public.delete_session('017f22e2-79b0-7000-8000-000000000001'), 'nothing left to delete';
end $$;
reset role;
do $$
begin
  assert (select count(*) from public.sessions) = 0, 'session deleted';
  assert (select count(*) from public.session_metrics) = 0, 'metrics deleted in cascade';
  assert (select count(*) from public.plans) = 0, 'plan deleted with its only session';
end $$;

-- 11. Deleting the account removes everything the person had.
select set_config('request.jwt.claims', current_setting('test.ana_aal2'), true);
set local role authenticated;
select public.save_session(current_setting('test.session')::jsonb);
reset role;
delete from auth.users where id = '00000000-0000-4000-8000-00000000000a';
do $$
begin
  assert (select count(*) from public.profiles where id = '00000000-0000-4000-8000-00000000000a') = 0, 'profile gone';
  assert (select count(*) from public.sessions) = 0, 'sessions gone with the account';
  assert (select count(*) from public.session_metrics) = 0, 'metrics gone with the account';
  assert (select count(*) from public.plans) = 0, 'plans gone with the account';
end $$;

rollback;

\echo 'cloud_sessions_test: all checks passed'
