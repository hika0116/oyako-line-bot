-- Minimum Working Family OS: structured recipe catalog (review before applying).
-- This migration is intentionally not applied by the application at runtime.

create table if not exists public.recipes (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    summary text not null default '',
    base_servings integer not null check (base_servings > 0),
    dish_roles text[] not null default '{}',
    meal_occasions text[] not null default '{}',
    cuisine text not null default '',
    cooking_method text[] not null default '{}',
    total_minutes integer not null check (total_minutes > 0),
    active_minutes integer not null default 0 check (active_minutes >= 0),
    difficulty text not null default 'standard',
    low_energy boolean not null default false,
    bento_suitable boolean not null default false,
    leak_risk text not null default 'low' check (leak_risk in ('low', 'medium', 'high')),
    make_ahead_possible boolean not null default false,
    season_months integer[] not null default '{}',
    tags text[] not null default '{}',
    content_status text not null default 'draft'
        check (content_status in ('draft', 'reviewed', 'published', 'disabled')),
    source_type text not null default 'internal',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint recipes_title_not_blank check (length(btrim(title)) > 0),
    constraint recipes_roles_valid check (
        dish_roles <@ array['staple', 'main', 'side', 'soup', 'one_dish', 'staple_and_main']::text[]
    ),
    constraint recipes_occasions_valid check (
        meal_occasions <@ array['breakfast', 'lunch', 'bento', 'dinner', 'otsumami']::text[]
    ),
    constraint recipes_active_minutes_not_over_total check (
        active_minutes <= total_minutes
    ),
    constraint recipes_season_months_valid check (
        season_months <@ array[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]::integer[]
    )
);

create table if not exists public.recipe_ingredients (
    id uuid primary key default gen_random_uuid(),
    recipe_id uuid not null references public.recipes(id) on delete cascade,
    ingredient_name text not null,
    normalized_name text not null,
    quantity numeric,
    unit text not null default '',
    scaling_mode text not null default 'linear'
        check (scaling_mode in ('linear', 'count', 'seasoning', 'mostly_fixed', 'optional')),
    rounding_increment numeric,
    minimum_quantity numeric,
    optional boolean not null default false,
    basic_seasoning boolean not null default false,
    substitutes text[] not null default '{}',
    sort_order integer not null default 0,
    constraint recipe_ingredients_name_not_blank check (length(btrim(ingredient_name)) > 0),
    constraint recipe_ingredients_quantity_positive check (
        quantity is null or quantity > 0
    ),
    constraint recipe_ingredients_rounding_increment_positive check (
        rounding_increment is null or rounding_increment > 0
    ),
    constraint recipe_ingredients_minimum_quantity_nonnegative check (
        minimum_quantity is null or minimum_quantity >= 0
    )
);

create table if not exists public.recipe_steps (
    id uuid primary key default gen_random_uuid(),
    recipe_id uuid not null references public.recipes(id) on delete cascade,
    step_number integer not null check (step_number > 0),
    instruction text not null,
    duration_minutes integer not null default 0 check (duration_minutes >= 0),
    equipment text[] not null default '{}',
    parallel_group text not null default '',
    depends_on_steps integer[] not null default '{}',
    can_parallelize boolean not null default false,
    unique (recipe_id, step_number),
    constraint recipe_steps_instruction_not_blank check (length(btrim(instruction)) > 0)
);

create table if not exists public.recipe_sources (
    id uuid primary key default gen_random_uuid(),
    recipe_id uuid not null references public.recipes(id) on delete cascade,
    source_name text not null,
    source_url text not null default '',
    source_type text not null default 'internal',
    license_or_usage_note text not null default '',
    checked_at timestamptz,
    source_role text not null default 'reference'
);

create table if not exists public.recipe_proposal_history (
    id uuid primary key default gen_random_uuid(),
    user_id text not null,
    recipe_id uuid not null references public.recipes(id) on delete cascade,
    meal_occasion text not null
        check (meal_occasion in ('breakfast', 'lunch', 'bento', 'dinner', 'otsumami')),
    proposed_at timestamptz not null default now(),
    selected_at timestamptz,
    selected boolean not null default false,
    servings integer check (servings is null or servings > 0)
);

create table if not exists public.recipe_collection_topics (
    id uuid primary key default gen_random_uuid(),
    target_ingredients text[] not null default '{}',
    target_meal_occasions text[] not null default '{}',
    target_cooking_methods text[] not null default '{}',
    target_season_month integer check (target_season_month between 1 and 12),
    target_count integer not null default 5 check (target_count > 0),
    priority_score numeric not null default 0,
    priority_reasons jsonb not null default '[]'::jsonb,
    status text not null default 'pending'
        check (status in ('pending', 'searching', 'review_required', 'completed', 'failed')),
    created_at timestamptz not null default now(),
    processed_at timestamptz
);

create index if not exists recipes_content_status_idx on public.recipes(content_status);
create index if not exists recipes_meal_occasions_gin_idx on public.recipes using gin(meal_occasions);
create index if not exists recipes_dish_roles_gin_idx on public.recipes using gin(dish_roles);
create index if not exists recipe_ingredients_recipe_idx on public.recipe_ingredients(recipe_id, sort_order);
create index if not exists recipe_ingredients_normalized_idx on public.recipe_ingredients(normalized_name);
create index if not exists recipe_steps_recipe_idx on public.recipe_steps(recipe_id, step_number);
create index if not exists recipe_sources_recipe_idx on public.recipe_sources(recipe_id);
create index if not exists recipe_history_user_recent_idx
    on public.recipe_proposal_history(user_id, proposed_at desc);
create index if not exists recipe_history_user_selected_idx
    on public.recipe_proposal_history(user_id, selected_at desc) where selected;
create index if not exists recipe_collection_topics_status_idx
    on public.recipe_collection_topics(status, priority_score desc);

-- This repository has no shared updated_at trigger function to reuse, so the
-- catalog owns a narrowly scoped one. It is safe to call for every row update.
create or replace function public.set_recipe_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists recipes_set_updated_at on public.recipes;
create trigger recipes_set_updated_at
    before update on public.recipes
    for each row
    execute function public.set_recipe_updated_at();

alter table public.recipes enable row level security;
alter table public.recipe_ingredients enable row level security;
alter table public.recipe_steps enable row level security;
alter table public.recipe_sources enable row level security;
alter table public.recipe_proposal_history enable row level security;
alter table public.recipe_collection_topics enable row level security;

-- Published master data is readable.  There are deliberately no client write
-- policies; catalog maintenance must use a trusted service/review process.
create policy "published recipes are readable"
    on public.recipes for select to anon, authenticated
    using (content_status = 'published');

create policy "published recipe ingredients are readable"
    on public.recipe_ingredients for select to anon, authenticated
    using (exists (
        select 1 from public.recipes
        where recipes.id = recipe_ingredients.recipe_id
          and recipes.content_status = 'published'
    ));

create policy "published recipe steps are readable"
    on public.recipe_steps for select to anon, authenticated
    using (exists (
        select 1 from public.recipes
        where recipes.id = recipe_steps.recipe_id
          and recipes.content_status = 'published'
    ));

create policy "published recipe sources are readable"
    on public.recipe_sources for select to anon, authenticated
    using (exists (
        select 1 from public.recipes
        where recipes.id = recipe_sources.recipe_id
          and recipes.content_status = 'published'
    ));

-- recipe_proposal_history.user_id stores a LINE user id, not a Supabase Auth
-- subject. RLS stays enabled and no anon/authenticated policy is created, so
-- the table is service-role-only. If a future client uses Supabase Auth, add a
-- separate auth_user_id and write policies against that column instead.

-- Collection topics are aggregate operational data. No anon/authenticated
-- policy is created, so only the service role can access them.
