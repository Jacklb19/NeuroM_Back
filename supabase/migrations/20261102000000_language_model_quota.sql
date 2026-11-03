-- =============================================================================
-- Language model quota (ADR-54): each account may ask the language model a
-- limited number of times per day (UTC), shared by the plan proposal, the
-- session summary and the progress explanation. Without it, one account, or
-- many accounts created without confirming an email, could use up the
-- provider's free quota for everybody.
--
-- The counter is changed only through consume_language_model_quota. The table
-- has row level security and no policy, so nobody reads or rewrites it with
-- their own token; the function runs as its owner for that one update.
-- =============================================================================

-- 1. language_model_usage: calls of one account on one day.
create table public.language_model_usage (
  owner_id uuid not null references auth.users (id) on delete cascade,
  used_on date not null,
  calls integer not null default 0
    constraint language_model_usage_calls_non_negative check (calls >= 0),
  primary key (owner_id, used_on)
);

alter table public.language_model_usage enable row level security;

-- 2. consume_language_model_quota: counts one call of the caller, if today's
-- limit allows it. Returns false, without counting, once the limit is reached.
create or replace function public.consume_language_model_quota(p_daily_limit integer)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_owner uuid := (select auth.uid());
  v_today date := (now() at time zone 'utc')::date;
  v_calls integer;
begin
  if v_owner is null or p_daily_limit is null or p_daily_limit < 1 then
    return false;
  end if;

  -- Only today's row matters: earlier days of this account are dropped.
  delete from public.language_model_usage
  where language_model_usage.owner_id = v_owner and language_model_usage.used_on < v_today;

  insert into public.language_model_usage as usage (owner_id, used_on, calls)
  values (v_owner, v_today, 1)
  on conflict (owner_id, used_on) do update
    set calls = usage.calls + 1
    where usage.calls < p_daily_limit
  returning usage.calls into v_calls;

  return v_calls is not null;
end;
$$;

revoke execute on function public.consume_language_model_quota(integer) from public, anon;
grant execute on function public.consume_language_model_quota(integer) to authenticated;
