-- One row per real funding round, merged from the per-report rows in
-- public.funding_discoveries by legion-funding-feed (see src/pipeline/funding-rounds.ts).
-- Joins legion_company_profiles on domain: rounds are the events, profiles the companies.
-- secondary_* caches the Brave lookup for rounds only raisingfi reported, so each round
-- is searched at most once.
BEGIN;

CREATE TABLE IF NOT EXISTS leadgrow_knowledge.legion_funding_rounds (
  round_key             text PRIMARY KEY,          -- "<company key>|<first report date>"
  company_key           text NOT NULL,             -- domain, else lowercased company name
  company               text NOT NULL,
  domain                text,
  round                 text,
  amount                text,
  amount_usd            numeric,
  investors             text,
  announced_date        date NOT NULL,
  last_reported         date NOT NULL,
  reports               integer NOT NULL DEFAULT 1,
  sources               jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{name, url}] from every report
  secondary_source      jsonb,                                -- {name, url} found by Brave
  secondary_status      text,                                 -- 'found' | 'none'
  secondary_checked_at  timestamptz,
  seen_at               timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS legion_funding_rounds_company_idx ON leadgrow_knowledge.legion_funding_rounds (company_key, announced_date DESC);

ALTER TABLE leadgrow_knowledge.legion_funding_rounds ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON leadgrow_knowledge.legion_funding_rounds TO service_role;

COMMIT;
