-- =============================================================================
-- Migración inicial de NeuroMelody (Tabla 14 del documento de definición):
-- profiles, plans, sessions, session_metrics y model_versions.
--
-- Principios:
--   * RLS activa en todas las tablas: la clave anónima llega al cliente, así que
--     la base de datos debe impedir por sí sola el acceso a datos ajenos.
--   * Borrado en cascada desde auth.users: al eliminar la cuenta desaparecen
--     todos los datos fisiológicos asociados (requisito ético, Tabla 18).
--   * Solo se guardan indicadores agregados; la señal cruda nunca sale del
--     dispositivo (RNF-07).
-- =============================================================================

-- 1. profiles: un perfil por usuario de Supabase Auth
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  alias text,
  preferences jsonb default '{}'::jsonb not null,
  -- Nulo mientras el usuario no acepte las advertencias de uso (RF-17).
  warnings_accepted_at timestamptz,
  created_at timestamptz default now() not null,
  updated_at timestamptz default now() not null
);

-- 2. plans: plan de sesión (objetivos y parámetros musicales iniciales)
create table if not exists public.plans (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.profiles(id) on delete cascade,
  goals text[] default '{}'::text[] not null,
  initial_params jsonb default '{}'::jsonb not null,
  duration_minutes integer not null check (duration_minutes > 0),
  created_at timestamptz default now() not null
);

-- 3. sessions: una sesión de escucha finalizada
create table if not exists public.sessions (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.profiles(id) on delete cascade,
  -- Borrar un plan no debe borrar el historial de sesiones que lo usaron.
  plan_id uuid references public.plans(id) on delete set null,
  started_at timestamptz not null,
  ended_at timestamptz,
  final_state text,
  -- Texto del modelo de lenguaje; la interfaz lo marca como generado automáticamente.
  summary text,
  created_at timestamptz default now() not null,
  constraint sessions_ended_after_started
    check (ended_at is null or ended_at >= started_at)
);

-- 4. session_metrics: serie agregada a una muestra cada 5 s por sesión
create table if not exists public.session_metrics (
  session_id uuid not null references public.sessions(id) on delete cascade,
  second integer not null check (second >= 0),
  -- Los índices admiten nulo: los tramos de baja calidad (RF-04) no los producen.
  mean_hr numeric check (mean_hr > 0),
  rmssd numeric check (rmssd >= 0),
  sdnn numeric check (sdnn >= 0),
  lf_hf_ratio numeric check (lf_hf_ratio >= 0),
  estimated_state text,
  confidence numeric check (confidence >= 0 and confidence <= 1),
  primary key (session_id, second)
);

-- 5. model_versions: versiones publicadas del clasificador ONNX (sin datos de usuario)
create table if not exists public.model_versions (
  version text primary key,
  storage_path text not null,
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  metrics jsonb default '{}'::jsonb not null,
  published_at timestamptz default now() not null
);

-- 6. Índices en claves foráneas (relaciones y comprobaciones de RLS)
create index if not exists idx_plans_owner_id on public.plans(owner_id);
create index if not exists idx_sessions_owner_started
  on public.sessions(owner_id, started_at desc);
create index if not exists idx_sessions_plan_id on public.sessions(plan_id);

-- 7. Activación de RLS en todas las tablas
alter table public.profiles enable row level security;
alter table public.plans enable row level security;
alter table public.sessions enable row level security;
alter table public.session_metrics enable row level security;
alter table public.model_versions enable row level security;

-- 8. Políticas de profiles (sin delete: la cuenta se elimina desde Auth y cae en cascada)
create policy "Users can view own profile"
  on public.profiles for select
  to authenticated
  using ((select auth.uid()) = id);

create policy "Users can insert own profile"
  on public.profiles for insert
  to authenticated
  with check ((select auth.uid()) = id);

create policy "Users can update own profile"
  on public.profiles for update
  to authenticated
  using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);

-- 9. Políticas de plans
create policy "Users can view own plans"
  on public.plans for select
  to authenticated
  using ((select auth.uid()) = owner_id);

create policy "Users can insert own plans"
  on public.plans for insert
  to authenticated
  with check ((select auth.uid()) = owner_id);

create policy "Users can update own plans"
  on public.plans for update
  to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

create policy "Users can delete own plans"
  on public.plans for delete
  to authenticated
  using ((select auth.uid()) = owner_id);

-- 10. Políticas de sessions (el plan referenciado también debe ser propio)
create policy "Users can view own sessions"
  on public.sessions for select
  to authenticated
  using ((select auth.uid()) = owner_id);

create policy "Users can insert own sessions"
  on public.sessions for insert
  to authenticated
  with check (
    (select auth.uid()) = owner_id
    and (
      plan_id is null
      or exists (
        select 1 from public.plans
        where plans.id = sessions.plan_id
          and plans.owner_id = (select auth.uid())
      )
    )
  );

create policy "Users can update own sessions"
  on public.sessions for update
  to authenticated
  using ((select auth.uid()) = owner_id)
  with check (
    (select auth.uid()) = owner_id
    and (
      plan_id is null
      or exists (
        select 1 from public.plans
        where plans.id = sessions.plan_id
          and plans.owner_id = (select auth.uid())
      )
    )
  );

create policy "Users can delete own sessions"
  on public.sessions for delete
  to authenticated
  using ((select auth.uid()) = owner_id);

-- 11. Políticas de session_metrics (propiedad verificada a través de sessions).
-- Sin update: la serie de una sesión finalizada es inmutable.
create policy "Users can view metrics of own sessions"
  on public.session_metrics for select
  to authenticated
  using (
    exists (
      select 1 from public.sessions
      where sessions.id = session_metrics.session_id
        and sessions.owner_id = (select auth.uid())
    )
  );

create policy "Users can insert metrics to own sessions"
  on public.session_metrics for insert
  to authenticated
  with check (
    exists (
      select 1 from public.sessions
      where sessions.id = session_metrics.session_id
        and sessions.owner_id = (select auth.uid())
    )
  );

create policy "Users can delete metrics of own sessions"
  on public.session_metrics for delete
  to authenticated
  using (
    exists (
      select 1 from public.sessions
      where sessions.id = session_metrics.session_id
        and sessions.owner_id = (select auth.uid())
    )
  );

-- 12. model_versions: lectura para usuarios autenticados; solo el rol de
-- servicio publica versiones (no hay políticas de escritura y se revocan permisos).
create policy "Authenticated users can view model versions"
  on public.model_versions for select
  to authenticated
  using (true);

revoke all on table public.model_versions from anon;
revoke insert, update, delete, truncate on table public.model_versions from authenticated;

-- 13. Trigger para crear el perfil al registrarse un usuario en auth.users.
-- search_path vacío para que la función security definer no resuelva
-- objetos de esquemas controlables por terceros.
create or replace function public.handle_new_user()
returns trigger
security definer
set search_path = ''
language plpgsql
as $$
begin
  insert into public.profiles (id, alias)
  values (new.id, new.raw_user_meta_data->>'alias');
  return new;
end;
$$;

create or replace trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
