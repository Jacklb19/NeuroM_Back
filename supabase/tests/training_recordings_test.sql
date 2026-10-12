-- Checks the training recordings migration against a local database (see
-- README): consent, the second factor, ownership, sending a recording twice,
-- withdrawing it, the cascade when the account is deleted, the files only
-- the training job may read and the published version of the classifier.
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
select set_config('test.recording', '{
  "id": "017f22e2-79b0-7000-8000-0000000000c1",
  "participant": "5f0c1d4e-1111-4222-8333-444455556666",
  "recorded_at": "2026-10-12T10:00:00Z",
  "source_kind": "ble",
  "consent": true,
  "segments": [
    {"label": "rest", "planned_seconds": 300, "beat_times_ms": [800, 1600, 2400], "rr_ms": [800, 800, 800]},
    {"label": "slow_breathing", "planned_seconds": 180, "beat_times_ms": [900, 1800], "rr_ms": [900, 900]},
    {"label": "demanding_task", "planned_seconds": 180, "beat_times_ms": [650], "rr_ms": [650]}
  ]
}', true);

-- 1. Without the consent nothing is stored, even at aal2.
select set_config('request.jwt.claims', current_setting('test.ana_aal2'), true);
set local role authenticated;
do $$
declare
  v_error text := 'no error';
begin
  begin
    perform public.save_training_recording(current_setting('test.recording')::jsonb - 'consent');
  exception when others then
    v_error := sqlerrm;
  end;
  assert v_error = 'consent_required', 'without consent: expected consent_required, got ' || v_error;
end $$;

-- 2. Without the second factor row level security refuses the heartbeats.
select set_config('request.jwt.claims', current_setting('test.ana_aal1'), true);
do $$
declare
  v_state text := 'no error';
begin
  begin
    perform public.save_training_recording(current_setting('test.recording')::jsonb);
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '42501', 'aal1: expected insufficient_privilege (42501), got ' || v_state;
end $$;

-- 3. With both, the recording is stored once: sending it again changes nothing.
select set_config('request.jwt.claims', current_setting('test.ana_aal2'), true);
do $$
declare
  v_listed jsonb;
begin
  assert public.save_training_recording(current_setting('test.recording')::jsonb), 'first send: expected true';
  assert not public.save_training_recording(current_setting('test.recording')::jsonb), 'second send: expected false';
  v_listed := public.list_training_recordings();
  assert jsonb_array_length(v_listed) = 1, 'expected one recording, got ' || v_listed::text;
  assert (v_listed -> 0 ->> 'beats')::integer = 6, 'expected 6 beats, got ' || (v_listed -> 0 ->> 'beats');
  assert v_listed -> 0 ? 'segments' is false, 'the list must not carry the heartbeats';
end $$;

-- 4. A source that measures nobody is refused by the table itself.
do $$
declare
  v_state text := 'no error';
begin
  begin
    perform public.save_training_recording(
      current_setting('test.recording')::jsonb
        || '{"id": "017f22e2-79b0-7000-8000-0000000000c2", "source_kind": "simulator"}'::jsonb
    );
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '23514', 'simulator: expected check_violation (23514), got ' || v_state;
end $$;

-- 5. Another person, even past the second factor, sees and deletes nothing of it.
select set_config('request.jwt.claims', current_setting('test.ben_aal2'), true);
do $$
begin
  assert public.list_training_recordings() = '[]'::jsonb, 'another person must list nothing';
  assert not public.delete_training_recording('017f22e2-79b0-7000-8000-0000000000c1'), 'another person must delete nothing';
  assert (select count(*) from public.training_recordings) = 0, 'another person must read no row';
end $$;

-- 6. A signed-in person cannot reach the files of the training job, nor publish a model.
do $$
declare
  v_state text := 'no error';
begin
  begin
    perform 1 from public.training_assets;
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '42501', 'training_assets: expected insufficient_privilege (42501), got ' || v_state;

  v_state := 'no error';
  begin
    insert into public.model_versions (version, sha256, model)
    values ('forged', repeat('0', 64), '{"kind": "forged"}'::jsonb);
  exception when others then
    v_state := sqlstate;
  end;
  assert v_state = '42501', 'model_versions: expected insufficient_privilege (42501), got ' || v_state;
  assert public.current_classifier() is null, 'no version published yet: expected null';
end $$;

-- 7. The owner withdraws the recording.
select set_config('request.jwt.claims', current_setting('test.ana_aal2'), true);
do $$
begin
  assert public.delete_training_recording('017f22e2-79b0-7000-8000-0000000000c1'), 'the owner must be able to delete';
  assert not public.delete_training_recording('017f22e2-79b0-7000-8000-0000000000c1'), 'nothing left to delete';
  assert public.save_training_recording(current_setting('test.recording')::jsonb), 'it can be sent again after withdrawing it';
end $$;

-- 8. Deleting the account takes its recordings with it.
reset role;
delete from auth.users where id = '00000000-0000-4000-8000-00000000000a';
do $$
begin
  assert (select count(*) from public.training_recordings) = 0, 'the recordings must fall with the account';
end $$;

-- 9. The service role publishes versions; a signed-in person gets the latest one with its file.
insert into public.model_versions (version, sha256, model, published_at) values
  ('2026-10-11', repeat('a', 64), '{"schemaVersion": 1, "note": "older"}'::jsonb, '2026-10-11T00:00:00Z'),
  ('2026-10-20', repeat('b', 64), '{"schemaVersion": 1, "note": "newer"}'::jsonb, '2026-10-20T00:00:00Z');
insert into public.training_assets (name, content) values ('S2.npz', '\x00ff'::bytea);

select set_config('request.jwt.claims', current_setting('test.ben_aal2'), true);
set local role authenticated;
do $$
declare
  v_current jsonb := public.current_classifier();
begin
  assert v_current ->> 'version' = '2026-10-20', 'expected the latest version, got ' || coalesce(v_current::text, 'null');
  assert v_current -> 'model' ->> 'note' = 'newer', 'expected the file of the latest version';
  assert v_current ->> 'sha256' = repeat('b', 64), 'expected its checksum';
end $$;

reset role;
rollback;

\echo 'training recordings migration: all checks passed'
