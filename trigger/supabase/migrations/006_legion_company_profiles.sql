-- Public-safe company profiles for the Legion funding feed.
-- One row per funded company domain, written once by legion-funding-feed's
-- find-people waterfall (QuickEnrich dataset search, AI Ark people search fallback).
-- Holds only what the public feed shows: no emails, no phone numbers.
BEGIN;

CREATE TABLE IF NOT EXISTS leadgrow_knowledge.legion_company_profiles (
  domain        text PRIMARY KEY,
  hq            text,
  employees     text,
  founders      jsonb NOT NULL DEFAULT '[]'::jsonb,
  sources       text[] NOT NULL DEFAULT '{}',
  cost_usd      numeric NOT NULL DEFAULT 0,
  enriched_at   timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE leadgrow_knowledge.legion_company_profiles ENABLE ROW LEVEL SECURITY;

COMMIT;
