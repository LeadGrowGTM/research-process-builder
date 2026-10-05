export interface QueryDef {
  id: string;
  query: string;
  num: number;
  desc: string;
}

export interface RawResult {
  company_name_raw: string;
  amount_raw: string;
  round_type_raw: string;
  source_url: string;
  source_domain: string;
  snippet: string;
  title: string;
  query_source: string;
}

export type ConfidenceLevel = "high" | "medium" | "low";

export interface SignalScores {
  nameQuality: ConfidenceLevel;
  fundingExplicit: ConfidenceLevel;
  sourceTier: ConfidenceLevel;
  composite: ConfidenceLevel;
  reasons: string[];
}

export interface CandidateSource {
  url: string;
  domain: string;
  score: number;
  query_source: string;
  title: string;
  snippet: string;
}

export interface Candidate {
  company_name: string;
  company_name_normalized: string;
  amount: string;
  round_type: string;
  needs_disambiguation: boolean;
  sources: CandidateSource[];
  best_score: number;
  best_source_url: string;
  confidence?: ConfidenceLevel;
  confidenceReasons?: string[];
}

export interface FilteredItem {
  title: string;
  reason: string;
  url: string;
}

export interface Stage2Result {
  companies: Candidate[];
  filtered_out: FilteredItem[];
  stats: {
    raw_count: number;
    company_count: number;
    filtered_count: number;
  };
}

export interface ExtractedData {
  company_name: string;
  company_domain: string | null;
  amount_raised: string | null;
  round_type: string | null;
  lead_investors: string | null;
  round_reasoning: string | null;
  industry: string | null;
  location: string | null;
  funding_date: string | null;
}

export interface EnrichedRecord {
  company_name: string;
  company_domain: string;
  amount_raised: string;
  round_type: string;
  source_url: string;
  lead_investors: string | null;
  round_reasoning: string | null;
  industry?: string | null;
  location?: string | null;
  article_text: string | null;
  source_count: number;
  score: number;
  discovered_by: string;
  discovered_by_pipeline: string;
  amount_raised_usd?: number | null;
  amount_raised_currency?: string | null;
  funding_date?: string | null;
  confidence?: ConfidenceLevel;
}

export type RoundType = "series_a" | "series_b" | "series_c";

export interface RoundConfig {
  roundType: RoundType;
  roundLabel: string;
  roundPattern: RegExp;
  nonRoundPattern: RegExp;
  softNonPattern: RegExp;
  noisePatterns: RegExp;
  notRoundSentinel: string;
  queries: QueryDef[];
  supabaseTable: string;
  webhookUrl: string;
  webhookAuthToken: string;
  extractionPrompt: string;
}

export interface PipelineConfig {
  roundConfig: RoundConfig;
  pipelineId: string;
  tbs: string;
  date: string;
  skipEnrich: boolean;
  maxEnrich: number;
  dryRun: boolean;
  skipKnownCompanies?: boolean;
  skipKnownDays?: number;
  stage?: number;
  /** Epoch ms by which the run must finish; enrichment stops early to leave time for delivery. */
  deadlineAt?: number;
}

export interface PipelineResult {
  date: string;
  companyCount: number;
  companies: EnrichedRecord[];
  stats: {
    rawResults: number;
    candidatesAfterFilter: number;
    enrichedCount: number;
    durationMs: number;
  };
}
