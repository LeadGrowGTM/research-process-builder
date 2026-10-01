BEGIN;

CREATE SCHEMA IF NOT EXISTS leadgrow_knowledge;

ALTER TABLE public.funding_discoveries
  ADD COLUMN IF NOT EXISTS raw_text text,
  ADD COLUMN IF NOT EXISTS website_url text,
  ADD COLUMN IF NOT EXISTS logo_url text,
  ADD COLUMN IF NOT EXISTS source_name text,
  ADD COLUMN IF NOT EXISTS employee_count integer,
  ADD COLUMN IF NOT EXISTS employee_range text,
  ADD COLUMN IF NOT EXISTS hq_location text,
  ADD COLUMN IF NOT EXISTS company_description text,
  ADD COLUMN IF NOT EXISTS products text,
  ADD COLUMN IF NOT EXISTS founded_year integer,
  ADD COLUMN IF NOT EXISTS linkedin_url text;

CREATE TABLE IF NOT EXISTS leadgrow_knowledge.founder_contacts (
  company_domain text NOT NULL,
  full_name text NOT NULL,
  title text,
  linkedin_url text NOT NULL,
  email text,
  email_status text,
  email_provider text,
  waterfall_path jsonb,
  found_at timestamptz NOT NULL DEFAULT now()
);

DO $migration$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_constraint
    WHERE conname = 'founder_contacts_unique_domain_linkedin'
      AND conrelid = 'leadgrow_knowledge.founder_contacts'::regclass
  ) THEN
    ALTER TABLE leadgrow_knowledge.founder_contacts
      ADD CONSTRAINT founder_contacts_unique_domain_linkedin
      UNIQUE (company_domain, linkedin_url);
  END IF;
END
$migration$;

ALTER TABLE leadgrow_knowledge.founder_contacts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON leadgrow_knowledge.founder_contacts FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA leadgrow_knowledge TO service_role;
GRANT SELECT, INSERT, UPDATE ON leadgrow_knowledge.founder_contacts TO service_role;

DO $migration$
BEGIN
  IF to_regclass('public.founder_contacts_public') IS NULL THEN
    EXECUTE $view$
      CREATE VIEW public.founder_contacts_public AS
      SELECT company_domain, full_name, title, linkedin_url
      FROM leadgrow_knowledge.founder_contacts
    $view$;
  ELSE
    -- Fail closed if a previous object exposes a different projection.
    -- Removing columns would require a destructive view replacement.
    IF (SELECT array_agg(attname::text ORDER BY attnum)
        FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.founder_contacts_public'::regclass
          AND attnum > 0 AND NOT attisdropped)
       IS DISTINCT FROM ARRAY['company_domain', 'full_name', 'title', 'linkedin_url'] THEN
      RAISE EXCEPTION 'founder_contacts_public must contain only the four public columns';
    END IF;
    EXECUTE $view$
      CREATE OR REPLACE VIEW public.founder_contacts_public AS
      SELECT company_domain, full_name, title, linkedin_url
      FROM leadgrow_knowledge.founder_contacts
    $view$;
  END IF;
END
$migration$;

GRANT SELECT ON public.founder_contacts_public TO anon, authenticated;

UPDATE public.funding_discoveries
SET industry = NULL
WHERE btrim(industry) IN ('not_stated', '');

UPDATE public.funding_discoveries
SET lead_investors = NULL
WHERE btrim(lead_investors) IN ('not_stated', '');

WITH source_hosts AS (
  SELECT
    id,
    lower(
      regexp_replace(
        substring(source_url FROM '(?i)^https?://([^/?#:@]+)'),
        '^www\.',
        '',
        'i'
      )
    ) AS hostname,
    lower(coalesce(discovered_by_pipeline, '')) AS pipeline,
    lower(source_url) AS source_url
  FROM public.funding_discoveries
  WHERE (source_url ~* '^https?://[^/?#]*'
    AND source_url !~* '^https?://[^/]*@')
    OR lower(coalesce(discovered_by_pipeline, '')) LIKE '%raisingfi%'
)
UPDATE public.funding_discoveries AS funding
SET source_name = CASE
  WHEN source.pipeline LIKE '%raisingfi%'
    OR source.source_url ~ '^https?://(www\.)?(x\.com|twitter\.com)/raisingfi/'
    THEN '@raisingfi on X'
  WHEN source.hostname = 'alleywatch.com' THEN 'AlleyWatch'
  WHEN source.hostname = 'bloomberg.com' THEN 'Bloomberg'
  WHEN source.hostname = 'businesswire.com' THEN 'Business Wire'
  WHEN source.hostname = 'einpresswire.com' THEN 'EIN Presswire'
  WHEN source.hostname = 'eu-startups.com' THEN 'EU-Startups'
  WHEN source.hostname = 'finsmes.com' THEN 'FinSMEs'
  WHEN source.hostname = 'infotechlead.com' THEN 'InfoTechLead'
  WHEN source.hostname = 'prnewswire.com' THEN 'PR Newswire'
  WHEN source.hostname = 'reuters.com' THEN 'Reuters'
  WHEN source.hostname IN ('sec.gov', 'www.sec.gov') THEN 'SEC Form D'
  WHEN source.hostname = 'tech.eu' THEN 'Tech.eu'
  WHEN source.hostname = 'techcrunch.com' THEN 'TechCrunch'
  WHEN source.hostname = 'techround.co.uk' THEN 'TechRound'
  WHEN source.hostname = 'thesaasnews.com' THEN 'The SaaS News'
  WHEN source.hostname = 'vcnewsdaily.com' THEN 'VC News Daily'
  WHEN source.hostname = 'venturebeat.com' THEN 'VentureBeat'
  ELSE nullif(source.hostname, '')
END
FROM source_hosts AS source
WHERE funding.id = source.id
  AND nullif(btrim(funding.source_name), '') IS NULL;

WITH normalized_domains AS (
  SELECT
    id,
    lower(
      regexp_replace(
        split_part(split_part(split_part(regexp_replace(company_domain, '^https?://', '', 'i'), '/', 1), '?', 1), '#', 1),
        '^www\.',
        '',
        'i'
      )
    ) AS domain
  FROM public.funding_discoveries
  WHERE company_domain IS NOT NULL
)
UPDATE public.funding_discoveries AS funding
SET logo_url = 'https://www.google.com/s2/favicons?domain=' || normalized.domain || '&sz=128'
FROM normalized_domains AS normalized
WHERE funding.id = normalized.id
  AND nullif(btrim(funding.logo_url), '') IS NULL
  AND normalized.domain ~ '^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$'
  AND normalized.domain NOT IN ('not_found', 'not_stated', 'not_enriched');

-- The public-feeds storage bucket is created by legion-funding-feed at runtime
-- via the Supabase Storage API before upload, not by this migration.

COMMIT;
