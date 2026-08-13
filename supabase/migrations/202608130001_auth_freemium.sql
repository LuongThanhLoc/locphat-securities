-- Lộc Phát Securities: Supabase Auth profiles, watchlist and login protection.
-- Run once through the Supabase SQL editor or CLI after creating the project.

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  username text not null,
  username_normalized text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint profiles_username_format check (
    username_normalized ~ '^[a-z0-9][a-z0-9._-]{1,22}[a-z0-9]$'
  ),
  constraint profiles_username_reserved check (
    username_normalized not in ('admin','administrator','api','auth','help','locphat','root','security','support','system','webmaster')
  )
);

create table if not exists public.watchlist_items (
  user_id uuid not null references auth.users(id) on delete cascade,
  symbol text not null,
  company_name text not null default '',
  exchange text,
  note text not null default '',
  ai_analysis jsonb not null default '{}'::jsonb,
  added_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, symbol),
  constraint watchlist_symbol_format check (symbol ~ '^[A-Z0-9]{2,10}$'),
  constraint watchlist_note_length check (char_length(note) <= 300)
);

create table if not exists public.auth_login_attempts (
  id bigint generated always as identity primary key,
  action text not null default 'login' check (action in ('login', 'register')),
  ip_hash text not null,
  email_hash text not null,
  succeeded boolean not null default false,
  created_at timestamptz not null default now()
);
alter table public.auth_login_attempts add column if not exists action text not null default 'login';
create index if not exists auth_login_attempts_lookup_idx
  on public.auth_login_attempts(action, ip_hash, email_hash, created_at desc);

create or replace view public.recent_login_failures
with (security_invoker = true)
as
select ip_hash, email_hash, count(*)::integer as failure_count
from public.auth_login_attempts
where action = 'login' and succeeded = false and created_at >= now() - interval '15 minutes'
group by ip_hash, email_hash;

create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
declare
  requested_username text;
begin
  requested_username := lower(trim(new.raw_user_meta_data ->> 'username'));
  if requested_username is null
     or requested_username !~ '^[a-z0-9][a-z0-9._-]{1,22}[a-z0-9]$'
     or requested_username in ('admin','administrator','api','auth','help','locphat','root','security','support','system','webmaster') then
    raise exception 'invalid_username';
  end if;
  insert into public.profiles(id, username, username_normalized)
  values (new.id, requested_username, requested_username);
  return new;
exception
  when unique_violation then
    raise exception 'username_already_exists';
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_auth_user();

create or replace function public.sync_watchlist(items jsonb)
returns setof public.watchlist_items
language plpgsql
security invoker
set search_path = public
as $$
begin
  if auth.uid() is null then raise exception 'authentication_required'; end if;
  if jsonb_typeof(items) <> 'array' or jsonb_array_length(items) > 200 then
    raise exception 'invalid_watchlist';
  end if;

  delete from public.watchlist_items w
  where w.user_id = auth.uid()
    and not exists (
      select 1 from jsonb_array_elements(items) item
      where upper(trim(item ->> 'symbol')) = w.symbol
    );

  insert into public.watchlist_items(user_id, symbol, company_name, exchange, note, ai_analysis, added_at, updated_at)
  select auth.uid(), upper(trim(item ->> 'symbol')),
         left(coalesce(item ->> 'company_name', ''), 240),
         nullif(left(coalesce(item ->> 'exchange', ''), 20), ''),
         left(coalesce(item ->> 'note', ''), 300),
         case when jsonb_typeof(item -> 'ai_analysis') = 'object' then item -> 'ai_analysis' else '{}'::jsonb end,
         coalesce((item ->> 'added_at')::timestamptz, now()), now()
  from jsonb_array_elements(items) item
  where upper(trim(item ->> 'symbol')) ~ '^[A-Z0-9]{2,10}$'
  on conflict(user_id, symbol) do update set
    company_name = excluded.company_name,
    exchange = excluded.exchange,
    note = excluded.note,
    ai_analysis = excluded.ai_analysis,
    updated_at = now();

  return query select * from public.watchlist_items where user_id = auth.uid() order by added_at desc;
end;
$$;

alter table public.profiles enable row level security;
alter table public.watchlist_items enable row level security;
alter table public.auth_login_attempts enable row level security;

drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own" on public.profiles for select to authenticated using ((select auth.uid()) = id);
drop policy if exists "profiles_update_own" on public.profiles;
create policy "profiles_update_own" on public.profiles for update to authenticated using ((select auth.uid()) = id) with check ((select auth.uid()) = id);
drop policy if exists "watchlist_all_own" on public.watchlist_items;
create policy "watchlist_all_own" on public.watchlist_items for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

revoke all on public.auth_login_attempts from anon, authenticated;
revoke all on public.recent_login_failures from anon, authenticated;
grant select on public.profiles to authenticated;
grant select, insert, update, delete on public.watchlist_items to authenticated;
grant execute on function public.sync_watchlist(jsonb) to authenticated;
