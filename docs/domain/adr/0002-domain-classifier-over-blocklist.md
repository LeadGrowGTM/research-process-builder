# ADR-0002: LLM-Backed Domain Classifier Replaces Hardcoded Blocklist

## Status
Accepted

## Context

The pipeline was rejecting bad domains (news sites, CDN hosts, law firms) using an ever-growing hardcoded `BLOCKED_DOMAINS` set in `domain_resolver.py`. Every time a new slip-through occurred (e.g., `gobiernu.cw`, `cdninstagram.com`, `anu.edu.au` assigned as company domains), someone manually appended to the list. After the backfill pass in April 2026, 19 bad domains were identified across production records. The blocklist had reached ~40 entries and was still missing categories entirely (CDNs, trackers, short URL services).

Two approaches considered:
1. **Regex + heuristic rules** — pattern-match on TLD, domain structure, known prefixes. Free to run, but requires ongoing maintenance as new domain patterns emerge. Misses novel domains.
2. **LLM classifier with persistent cache** — call gpt-4o-mini once per unknown domain, cache the verdict permanently. High accuracy on novel domains, cost amortizes to near-zero after ~30 runs.

## Decision

Replace `BLOCKED_DOMAINS` as the primary rejection mechanism with `domain_classifier.py`:
- On every domain resolution, call `classify_domain(domain)` before accepting.
- Cache verdicts in `data/domain_classifications.json` — committed to git so all machines share learned classifications.
- Only `real_company` verdict accepts. `unknown` rejects conservatively (prefer false negative over false positive).
- Cost: ~$0.0001/classification. After ~30 production runs, cache covers the long tail.

The hardcoded `NEWS_DOMAINS`, `LEGAL_SERVICES_DOMAINS`, etc. sets in `domain_resolver.py` are retained as a fast-path lookup before hitting the classifier, to avoid redundant API calls on already-known domains.

The key rule from `.claude/rules/pipeline-safety.md`: "Fix domain slip-throughs by improving the classifier or seeding its cache — never by appending to `BLOCKED_DOMAINS`."

## Consequences

**Good:**
- Novel domain types (tracker, short_url, edu) are handled without code changes.
- The classifier cache compounds — each production run makes the next run cheaper.
- Domain classification decisions are transparent and auditable (the cache file is committed).
- Removes the whack-a-mole pattern of appending to a list after every slip-through.

**Bad:**
- Requires `OPENAI_API_KEY` at runtime. Without it, `unknown` verdict rejects the domain — this is intentional (conservative) but means cold starts with no key will reject all novel domains.
- Cache is a committed JSON file — merge conflicts possible if two branches independently classify the same domain with different verdicts.
- Classification is a point-in-time verdict. A domain that changes from news to company (unlikely but possible) won't be re-classified without a cache invalidation mechanism.
