import { describe, expect, it } from "vitest";
import { scoreAndFilter } from "./filters.js";
import { SERIES_A_CONFIG } from "./round-configs.js";
import type { RawResult } from "./types.js";

function result(title: string, url = "https://news.example.com/story", snippet = ""): RawResult {
  return {
    company_name_raw: "", amount_raw: "", round_type_raw: "", source_url: url,
    source_domain: new URL(url).hostname, snippet, title, query_source: "q1",
  };
}

const nameOf = (title: string) => scoreAndFilter([result(title)], SERIES_A_CONFIG).companies.map((c) => c.company_name);

describe("scoreAndFilter company names", () => {
  it("drops editorial lead-ins in front of the company name", () => {
    expect(nameOf("Exclusive: Danish legal research startup Pandektes raises €13.5M Series A")).toEqual(["Pandektes"]);
    expect(nameOf("Mumbai-based lending-tech startup Rezolv has raised $12.5 million in Series A")).toEqual(["Rezolv"]);
    expect(nameOf("Singapore fintech firm IPID raises $16M Series A")).toEqual(["IPID"]);
    expect(nameOf("Chilean Legal AI Platform Magnar secures $8M in Series A funding")).toEqual(["Magnar"]);
  });

  it("keeps names that contain a descriptor word", () => {
    expect(nameOf("The Company Store raises $20M Series A")).toEqual(["The Company Store"]);
    expect(nameOf("Open Platform Labs raises $15M Series A")).toEqual(["Open Platform Labs"]);
  });
});

describe("scoreAndFilter sources", () => {
  it("rejects known job boards and company-profile pages", () => {
    const out = scoreAndFilter([
      result("Tetrix - Series A $15M", "https://yespress.io/tetrix"),
      result("Strategic Account Executive at Aegis AI raises Series A", "https://www.remotesource.com/jobs/abc"),
      result("Cempra closes Series A investment round at $22 million", "https://jobs.biospace.com/article-releases-cempra"),
    ], SERIES_A_CONFIG);
    expect(out.companies).toEqual([]);
    expect(out.filtered_out.map((f) => f.reason)).toEqual(Array(3).fill("non-news source (job board/profile/event page)"));
  });

  it("keeps a funding article whose path happens to contain /jobs/", () => {
    const out = scoreAndFilter([result("Acme raises $10M Series A", "https://news.example.com/jobs/acme-series-a/")], SERIES_A_CONFIG);
    expect(out.companies.map((c) => c.company_name)).toEqual(["Acme"]);
  });

  it("scores a www. host the same as the bare tier domain", () => {
    const title = "Acme raises $10M Series A led by Example Capital";
    const [bare] = scoreAndFilter([result(title, "https://finsmes.com/acme")], SERIES_A_CONFIG).companies;
    const [www] = scoreAndFilter([result(title, "https://www.finsmes.com/acme")], SERIES_A_CONFIG).companies;
    expect(www.best_score).toBe(bare.best_score);
  });
});
