import { describe, expect, it } from "vitest";
import { companyDomain, mapAdditionalSignals, projectAdditionalSignals } from "./additional-signals.js";

const KEYS = ["type", "company", "domain", "logo", "headline", "metric", "summary", "tags", "location", "people", "date", "source", "sourceUrl", "details", "raisedAgain", "earlier"];

function map(input: {
  productLaunches?: Array<Record<string, unknown>>;
  gameSignals?: Array<Record<string, unknown>>;
  jobSignals?: Array<Record<string, unknown>>;
}) {
  return mapAdditionalSignals({
    productLaunches: input.productLaunches ?? [],
    gameSignals: input.gameSignals ?? [],
    jobSignals: input.jobSignals ?? [],
  });
}

describe("companyDomain", () => {
  it.each([
    ["mcp.crosswalk.to", "crosswalk.to"],
    ["https://legal.pilot5.ai/terms", "pilot5.ai"],
    ["https://ads.openai.com", "openai.com"],
    ["https://www.app.foo-labs.co.uk/about", "foo-labs.co.uk"],
    ["app.acme.com.au", "acme.com.au"],
  ])("normalizes %s to registrable domain %s", (value, domain) => {
    expect(companyDomain(value)).toBe(domain);
  });

  it.each(["https://links.producthunt.com", "https://news.techcrunch.com", "https://app.linkedin.com", "https://sub.t.co", "not_found", "https://127.0.0.1", "ramen.local", "https://app.svc.internal/x"])("rejects non-company host %s", (value) => {
    expect(companyDomain(value)).toBe("");
  });
});

describe("mapAdditionalSignals", () => {
  it("decodes double-escaped industry text and numeric entities", () => {
    const [launch] = map({
      productLaunches: [{
        company_name: "Acme",
        company_domain: "acme.com",
        source_url: "https://www.producthunt.com/posts/acme",
        description: "Plain robotics text",
        industry: "AI, Robotics &amp;amp; Automation",
        categories: ["Tools &#39;n&#39; kits"],
      }],
    });
    expect(launch.summary).toBe("Plain robotics text");
    expect(launch.details.industry).toBe("AI, Robotics & Automation");
    expect(launch.tags).toContain("Tools 'n' kits");
  });

  it("maps stored product, game, and job fields onto the shared signal shape", () => {
    const [role, launch, announcement] = map({
      productLaunches: [{
        discovered_date: "2026-09-02",
        company_name: "Acme &amp; Co",
        company_domain: "Acme.com",
        product_name: "Widget",
        tagline: "Ignored when a description exists",
        launch_type: "new_product",
        is_ai: true,
        score: 128,
        rank: 4,
        maker_website: "https://www.acme.com/about",
        source: "product_hunt",
        source_url: "https://www.producthunt.com/posts/widget",
        description: "Acme launched Widget.",
        categories: ["Developer Tools", "developer tools"],
        linkedin_url: "https://www.linkedin.com/company/acme",
        on_product_hunt: true,
        employee_count: 42,
        industry: "not_stated",
        linkedin_followers: 10,
        classification_reasoning: "internal note",
        created_at: "2026-09-30T00:00:00Z",
        logo_url: "https://evil.test/logo?token=secret",
      }],
      gameSignals: [{
        signal_type: "game_announcement",
        developer: "Rebel Wolves",
        developer_domain: "rebelwolves.com",
        publisher: "Bandai Namco",
        publisher_domain: "bandainamco.com",
        game_title: "Dawnwalker",
        funding_amount: "$99M",
        genre: "Action RPG",
        platform: "PC, PS5",
        article_date: "2026-08-15",
        date_detected: "2026-09-01",
        source_url: "https://www.ign.com/dawnwalker",
        summary: "Rebel Wolves announced Dawnwalker.",
        source_name: "The Studio Blog",
      }],
      jobSignals: [{
        job_id: "501",
        job_title: "Technical Animator",
        company_name: "Studio North",
        company_website: "https://www.studionorth.example",
        location_city: "Montreal",
        location_country: "Canada",
        job_type: "Full-time",
        categories: ["Gameplay Animators"],
        tags: ["Unreal"],
        signal_keywords: ["technical animator"],
        signal_strength: "high",
        job_url: "https://80.lv/jobs/technical-animator",
        date_posted: "2026-09-03T15:04:00Z",
        date_detected: "2026-09-04",
        description: "This raw posting text is not a stored column.",
        email: "ada@studio.example",
      }],
    });

    expect(Object.keys(launch)).toEqual(KEYS);
    expect(launch).toMatchObject({
      type: "product-launch",
      company: "Acme & Co",
      domain: "acme.com",
      logo: "https://www.google.com/s2/favicons?domain=acme.com&sz=128",
      headline: "New product: Widget",
      metric: { label: "Product Hunt score", value: "128", sort: 128 },
      summary: "Acme launched Widget.",
      tags: ["New product", "AI", "Developer Tools"],
      location: "",
      people: [],
      date: "2026-09-02",
      source: "Product Hunt",
      sourceUrl: "https://www.producthunt.com/posts/widget",
      raisedAgain: false,
      earlier: [],
    });
    expect(JSON.stringify(launch)).not.toContain("internal note");
    expect(JSON.stringify(launch)).not.toContain("token");
    expect(launch.details).toEqual({
      launchType: "new_product",
      productName: "Widget",
      rank: 4,
      employees: 42,
      linkedinFollowers: 10,
      linkedin: "https://www.linkedin.com/company/acme",
      onProductHunt: "true",
    });

    expect(announcement).toMatchObject({
      type: "gaming",
      company: "Rebel Wolves",
      domain: "rebelwolves.com",
      headline: "Game announcement: Dawnwalker",
      metric: null,
      summary: "Rebel Wolves announced Dawnwalker.",
      tags: ["Game announcement", "Action RPG", "PC", "PS5"],
      date: "2026-08-15",
      source: "The Studio Blog",
      sourceUrl: "https://www.ign.com/dawnwalker",
      people: [],
      raisedAgain: false,
      earlier: [],
    });
    expect(announcement.details).toEqual({
      kind: "game_announcement",
      gameTitle: "Dawnwalker",
      publisher: "Bandai Namco",
      genre: "Action RPG",
      platform: "PC, PS5",
    });
    expect(announcement.logo).toBe("https://www.google.com/s2/favicons?domain=rebelwolves.com&sz=128");

    expect(role).toMatchObject({
      type: "hiring",
      company: "Studio North",
      domain: "studionorth.example",
      headline: "Hiring: Technical Animator",
      metric: null,
      summary: "",
      tags: ["Gaming and animation", "Gameplay Animators", "Unreal", "technical animator", "Full-time"],
      location: "Montreal, Canada",
      people: [],
      date: "2026-09-03",
      source: "80.lv",
      sourceUrl: "https://80.lv/jobs/technical-animator",
      raisedAgain: false,
      earlier: [],
    });
    expect(role.details).toEqual({
      market: "Gaming and animation",
      jobType: "Full-time",
      animationMatch: "high",
      jobId: 501,
    });
  });

  it("keeps news launches, reported studio funding, and non-animation jobs honest", () => {
    const [news, funding, publisherOnly, weakRole, broadRole] = map({
      productLaunches: [{
        discovered_date: "2026-08-01",
        company_name: "Acme",
        product_name: "Widget",
        launch_type: "new_feature",
        is_ai: false,
        score: 99,
        source: "news",
        source_url: "https://techcrunch.com/widget",
        source_domain: "techcrunch.com",
        maker_website: "https://techcrunch.com/not-the-company",
        snippet: "Acme shipped a feature. Call 555-010-0199 or ada@acme.test.",
        query_source: "Q1",
      }],
      gameSignals: [
        {
          signal_type: "studio_funding",
          developer: "Moon Studio",
          developer_domain: "sec.gov",
          publisher: "Moon Studio",
          game_title: "undisclosed",
          funding_amount: "$5.7M",
          article_date: "not_stated",
          date_detected: "2026-07-20",
          source_url: "https://www.sec.gov/Archives/edgar/data/1",
          summary: "A filing reported studio funding.",
        },
        {
          signal_type: "game_announcement",
          developer: "",
          publisher: "Published Games",
          publisher_domain: "published.example",
          game_title: "Outer Rim",
          source_url: "https://www.gematsu.com/outer-rim",
          article_date: "2026-07-01",
        },
      ],
      jobSignals: [
        {
          job_id: 9,
          job_title: "Motion Graphics Artist",
          company_name: "Pixel Shop",
          company_domain: "80.lv",
          signal_strength: "medium",
          signal_keywords: [],
          job_url: "https://80.lv/jobs/motion-graphics",
          date_detected: "2026-06-01",
        },
        {
          job_id: 10,
          job_title: "Producer",
          company_name: "Pixel Shop",
          company_domain: "pixel.example",
          signal_strength: "medium",
          signal_keywords: [],
          categories: ["Production"],
          job_url: "http://80.lv/jobs/producer",
          date_posted: "yesterday",
          date_detected: "2026-06-02T00:00:00Z",
        },
      ],
    });

    expect(news).toMatchObject({
      type: "product-launch",
      domain: "",
      logo: null,
      headline: "New feature: Widget",
      metric: null,
      summary: "Acme shipped a feature. Call or.",
      tags: ["New feature"],
      source: "TechCrunch",
      sourceUrl: "https://techcrunch.com/widget",
    });
    expect(news.details).toEqual({ launchType: "new_feature", productName: "Widget" });
    expect(JSON.stringify(news)).not.toContain("ada@");
    expect(JSON.stringify(news)).not.toContain("555-010-0199");
    expect(JSON.stringify(news)).not.toContain("Q1");

    expect(funding).toMatchObject({
      type: "gaming",
      company: "Moon Studio",
      domain: "",
      headline: "Reported studio funding of $5.7M",
      metric: { label: "Reported", value: "$5.7M", sort: null },
      date: "2026-07-20",
      source: "SEC Form D",
      sourceUrl: "https://www.sec.gov/Archives/edgar/data/1",
      raisedAgain: false,
      earlier: [],
    });
    expect(funding.headline.toLowerCase()).not.toContain("round");
    expect(funding.details).toEqual({ kind: "studio_funding" });
    expect(funding.tags).toEqual(["Studio funding"]);

    expect(publisherOnly).toMatchObject({
      company: "Published Games",
      domain: "published.example",
      headline: "Game announcement: Outer Rim",
    });
    expect(publisherOnly.details.companyRole).toBe("publisher");

    expect(broadRole).toMatchObject({
      type: "hiring",
      headline: "Hiring: Motion Graphics Artist",
      domain: "",
      date: "2026-06-01",
      source: "80.lv",
    });
    expect(broadRole.details).toEqual({ market: "Gaming and animation", jobId: 9 });
    expect(weakRole).toMatchObject({
      headline: "Hiring: Producer",
      domain: "pixel.example",
      sourceUrl: "",
      source: "80.lv",
      date: "2026-06-02",
    });
    expect(weakRole.details).not.toHaveProperty("animationMatch");
  });

  it("drops rows without a producer identity or a public company, and withholds unsafe links", () => {
    const signals = map({
      productLaunches: [
        null as unknown as Record<string, unknown>,
        [] as unknown as Record<string, unknown>,
        { company_name: "   ", product_name: "Widget", source_url: "https://example.com/blank" },
        { company_name: "No Key", product_name: "Widget" },
        {
          company_name: "Unsafe",
          product_name: "Widget",
          source: "news",
          source_url: "https://user:pass@news.example/widget",
          discovered_date: "2026-02-31",
          linkedin_url: "https://www.linkedin.com/in/ada",
          maker_website: "https://apps.apple.com/app/widget",
        },
        {
          company_name: "Script",
          product_name: "Widget",
          source_url: "javascript:alert(1)",
          source_name: "Example",
        },
      ],
      gameSignals: [
        { signal_type: "noise", developer: "Nope", source_url: "https://example.com/noise" },
        { signal_type: "game_announcement", game_title: "Only A Title", source_url: "https://example.com/title" },
        { signal_type: "studio_funding", developer: "No Url Studio", funding_amount: "$1M" },
      ],
      jobSignals: [
        { job_title: "Rigger", company_name: "No Id", job_url: "https://80.lv/jobs/rigger" },
        { job_id: 0, job_title: "Rigger", company_name: "Zero" },
        { job_id: 12.5, job_title: "Rigger", company_name: "Fraction" },
      ],
    });

    expect(signals.map((signal) => signal.company)).toEqual(["Script", "Unsafe"]);
    expect(signals[0].sourceUrl).toBe("");
    expect(signals[0].source).toBe("Example");
    expect(signals[0].date).toBe("");
    expect(signals[1]).toMatchObject({ domain: "", logo: null, sourceUrl: "", date: "" });
    expect(JSON.stringify(signals)).not.toContain("linkedin.com/in");
    expect(JSON.stringify(signals)).not.toContain("user:pass");
    expect(JSON.stringify(signals)).not.toContain("javascript");
    expect(JSON.stringify(signals)).not.toContain("apps.apple.com");
  });

  it("keeps ordinary public query parameters and withholds credential links", () => {
    const [author, tracking, secret] = map({
      productLaunches: [
        {
          company_name: "Synthetic",
          product_name: "Synthetic Product",
          source: "product_hunt",
          source_url: "https://www.producthunt.com/posts/synthetic-product?author=editor",
          discovered_date: "2026-09-02",
        },
        {
          company_name: "Tracked",
          product_name: "Widget",
          source: "news",
          source_url: "https://techcrunch.com/widget?utm_source=x&utm_medium=social",
          discovered_date: "2026-09-01",
        },
        {
          company_name: "Hidden",
          product_name: "Widget",
          source: "news",
          source_url: "https://news.example/round?author=editor&token=abc",
          source_name: "Example",
          discovered_date: "2026-08-01",
        },
      ],
    });

    expect(author.sourceUrl).toBe("https://www.producthunt.com/posts/synthetic-product?author=editor");
    expect(author.source).toBe("Product Hunt");
    expect(tracking.sourceUrl).toBe("https://techcrunch.com/widget?utm_source=x&utm_medium=social");
    expect(secret.sourceUrl).toBe("");
    expect(secret.source).toBe("Example");
    expect(JSON.stringify([author, tracking, secret])).not.toContain("token=abc");
  });

  it("dedups on producer identity, keeps distinct jobs and products, and prefers a later known date", () => {
    const older = {
      company_name: "Acme",
      product_name: "Widget",
      source_url: "https://www.producthunt.com/posts/widget",
      source: "product_hunt",
      discovered_date: "2026-01-01",
      tagline: "Old",
      description: "This older row has a long description that must lose to the later date.",
    };
    const newer = {
      company_name: "Acme",
      product_name: "Widget",
      source_url: " https://www.producthunt.com/posts/widget ",
      source: "product_hunt",
      discovered_date: "2026-09-01",
      tagline: "New",
    };
    const sameDayShort = {
      company_name: "Beta",
      product_name: "One",
      source_url: "https://example.com/beta",
      source: "news",
      discovered_date: "2026-05-01",
      tagline: "Short",
    };
    const sameDayLong = {
      ...sameDayShort,
      description: "A fuller description of the same launch.",
    };
    const secondLaunch = {
      company_name: "Acme",
      product_name: "Other",
      source_url: "https://example.com/acme-other",
      source: "news",
      discovered_date: "2026-05-01",
    };
    const sharedUrlGame = {
      signal_type: "game_announcement",
      developer: "Acme",
      game_title: "Side",
      source_url: "https://example.com/acme-other",
      article_date: "2026-05-01",
    };

    const signals = map({
      productLaunches: [secondLaunch, sameDayShort, older, newer, sameDayLong],
      gameSignals: [sharedUrlGame],
      jobSignals: [
        { job_id: 7, job_title: "Rigger", company_name: "Zed", job_url: "https://80.lv/jobs/7", date_detected: "2026-04-01" },
        { job_id: 7, job_title: "Lead Rigger", company_name: "Zed", job_url: "https://80.lv/jobs/7b", date_posted: "2026-08-01" },
      ],
    });
    expect(signals.map((signal) => [signal.type, signal.company, signal.headline, signal.date])).toEqual([
      ["product-launch", "Acme", "New product: Widget", "2026-09-01"],
      ["hiring", "Zed", "Hiring: Lead Rigger", "2026-08-01"],
      ["product-launch", "Acme", "Product launch: Other", "2026-05-01"],
      ["product-launch", "Beta", "Product launch: One", "2026-05-01"],
      ["gaming", "Acme", "Game announcement: Side", "2026-05-01"],
    ]);
    expect(signals[0].summary).toBe("New");
    expect(signals[3].summary).toBe("Short");
    expect(signals.every((signal) => signal.earlier.length === 0 && signal.raisedAgain === false)).toBe(true);

    const tied = map({ productLaunches: [sameDayLong, sameDayShort] });
    expect(tied).toHaveLength(1);
    expect(tied[0].summary).toBe("A fuller description of the same launch.");

    const many = map({
      productLaunches: Array.from({ length: 501 }, (_, index) => ({
        company_name: "Bulk",
        product_name: `P${index}`,
        source: "news",
        source_url: `https://example.com/p/${index}`,
        discovered_date: "2026-03-01",
      })),
    });
    expect(many).toHaveLength(501);
    expect(new Set(many.map((signal) => signal.sourceUrl)).size).toBe(501);
  });

  it("returns nothing for empty or missing families", () => {
    expect(map({})).toEqual([]);
    expect(mapAdditionalSignals(undefined as unknown as {
      productLaunches: Array<Record<string, unknown>>;
      gameSignals: Array<Record<string, unknown>>;
      jobSignals: Array<Record<string, unknown>>;
    })).toEqual([]);
  });

  it("counts every producer row as excluded, merged, or published", () => {
    const projection = projectAdditionalSignals({
      productLaunches: [
        { company_name: "   ", product_name: "Nope", source_url: "https://example.com/blank", discovered_date: "2026-09-01", source: "news" },
        { company_name: "Acme", product_name: "Widget", source_url: "https://example.com/acme", discovered_date: "2026-05-01", source: "news" },
        { company_name: "Acme", product_name: "Widget", source_url: "https://example.com/acme", discovered_date: "2026-09-01", source: "news" },
      ],
      gameSignals: [
        { signal_type: "studio_noise", developer: "Nope", game_title: "X", source_url: "https://example.com/noise", article_date: "2026-01-01" },
        { signal_type: "studio_funding", developer: "Studio", game_title: "Funded", source_url: "https://example.com/fund", article_date: "2026-02-01", funding_amount: "$1M" },
      ],
      jobSignals: [
        { job_id: 0, job_title: "Zero", company_name: "Zed", job_url: "https://80.lv/jobs/0", date_detected: "2026-01-01" },
        { job_id: 9, job_title: "Rigger", company_name: "Zed", job_url: "https://80.lv/jobs/9", date_detected: "2026-04-01" },
        { job_id: 9, job_title: "Lead Rigger", company_name: "Zed", job_url: "https://80.lv/jobs/9b", date_posted: "2026-08-01" },
      ],
    });
    expect(projection.families.productLaunches).toEqual({ sourceRows: 3, excluded: 1, merged: 1, published: 1 });
    expect(projection.families.gameSignals).toEqual({ sourceRows: 2, excluded: 1, merged: 0, published: 1 });
    expect(projection.families.jobSignals).toEqual({ sourceRows: 3, excluded: 1, merged: 1, published: 1 });
    expect(projection.signals.map((signal) => [signal.type, signal.company, signal.headline, signal.date])).toEqual([
      ["product-launch", "Acme", "Product launch: Widget", "2026-09-01"],
      ["hiring", "Zed", "Hiring: Lead Rigger", "2026-08-01"],
      ["gaming", "Studio", "Reported studio funding of $1M", "2026-02-01"],
    ]);
  });
});
