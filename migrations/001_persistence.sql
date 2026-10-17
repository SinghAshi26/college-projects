-- Single trusted workspace. This schema must NOT be added to Data API exposed schemas.
CREATE SCHEMA IF NOT EXISTS appnix;
REVOKE ALL ON SCHEMA appnix FROM PUBLIC;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'appnix_backend') THEN
        CREATE ROLE appnix_backend NOLOGIN;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS appnix.schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS appnix.leads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_key text NOT NULL UNIQUE,
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS leads_place_id ON appnix.leads ((data->>'place_id'))
    WHERE coalesce(data->>'place_id', '') <> '';
CREATE INDEX IF NOT EXISTS leads_status ON appnix.leads ((data->>'status'));
CREATE INDEX IF NOT EXISTS leads_name ON appnix.leads ((data->>'name'));
CREATE TABLE IF NOT EXISTS appnix.app_settings (
    id integer PRIMARY KEY CHECK (id = 1),
    preferences jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(preferences) = 'object'),
    daily_limit bigint NOT NULL CHECK (daily_limit >= 0),
    monthly_limit bigint NOT NULL CHECK (monthly_limit >= 0),
    tracking_started_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS appnix.search_history (
    id uuid PRIMARY KEY,
    category text NOT NULL,
    city text NOT NULL,
    requested_pages integer NOT NULL CHECK (requested_pages BETWEEN 1 AND 3),
    leads_returned bigint NOT NULL DEFAULT 0,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','partial','failed','blocked')),
    error_category text,
    day_start timestamptz NOT NULL,
    month_start timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS search_history_started ON appnix.search_history (started_at DESC);
CREATE TABLE IF NOT EXISTS appnix.usage_counters (
    provider text NOT NULL,
    period text NOT NULL CHECK (period IN ('day','month')),
    period_start timestamptz NOT NULL,
    reset_at timestamptz NOT NULL,
    request_count bigint NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    pending_count bigint NOT NULL DEFAULT 0 CHECK (pending_count >= 0),
    successful_requests bigint NOT NULL DEFAULT 0,
    failed_requests bigint NOT NULL DEFAULT 0,
    search_jobs bigint NOT NULL DEFAULT 0,
    leads_returned bigint NOT NULL DEFAULT 0,
    configured_limit bigint,
    PRIMARY KEY (provider, period, period_start)
);
CREATE TABLE IF NOT EXISTS appnix.api_usage_logs (
    id uuid PRIMARY KEY,
    search_id uuid REFERENCES appnix.search_history(id),
    provider text NOT NULL CHECK (provider IN ('google_places','google_pagespeed','website')),
    operation text NOT NULL CHECK (operation IN ('text_search','performance','website_get','website_head')),
    attempt integer NOT NULL CHECK (attempt > 0),
    reserved_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    http_status integer CHECK (http_status BETWEEN 100 AND 599),
    outcome text NOT NULL DEFAULT 'pending' CHECK (outcome IN ('pending','success','http_error','timeout','network_error','invalid_response')),
    success boolean,
    error_category text,
    request_units integer NOT NULL DEFAULT 0 CHECK (request_units IN (0,1)),
    billable_units numeric, -- Unknown: never infer billing from HTTP attempts.
    day_start timestamptz NOT NULL,
    month_start timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_logs_search ON appnix.api_usage_logs (search_id);
CREATE INDEX IF NOT EXISTS usage_logs_time ON appnix.api_usage_logs (reserved_at DESC);

REVOKE ALL ON ALL TABLES IN SCHEMA appnix FROM PUBLIC;
GRANT USAGE ON SCHEMA appnix TO appnix_backend;
GRANT SELECT, INSERT, UPDATE ON appnix.leads, appnix.app_settings,
    appnix.search_history, appnix.usage_counters, appnix.api_usage_logs TO appnix_backend;
GRANT SELECT ON appnix.schema_migrations TO appnix_backend;

DO $$ DECLARE t text; r text; BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA appnix FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA appnix FROM %I', r);
        END IF;
    END LOOP;
    FOREACH t IN ARRAY ARRAY['leads','app_settings','search_history','usage_counters','api_usage_logs','schema_migrations'] LOOP
        EXECUTE format('ALTER TABLE appnix.%I ENABLE ROW LEVEL SECURITY', t);
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='appnix' AND tablename=t AND policyname='backend_access') THEN
            EXECUTE format('CREATE POLICY backend_access ON appnix.%I TO appnix_backend USING (true) WITH CHECK (true)', t);
        END IF;
    END LOOP;
END $$;
INSERT INTO appnix.schema_migrations(version) VALUES ('001_persistence') ON CONFLICT DO NOTHING;
