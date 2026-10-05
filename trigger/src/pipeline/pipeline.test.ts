import { describe, expect, it } from "vitest";
import { extractDomainFromArticle } from "./pipeline.js";
import { htmlToText } from "./scrape.js";

describe("extractDomainFromArticle", () => {
  it("ignores an absolute publisher link even when its path contains the company domain", () => {
    const sourceUrl = "https://publisher.com/news/acme";
    const article = htmlToText('<a href="/company/acme.com">Acme profile</a>', sourceUrl);
    expect(article).toBe("Acme profile (https://publisher.com/company/acme.com)");
    expect(extractDomainFromArticle(article, "Acme", sourceUrl)).toBeNull();
    expect(extractDomainFromArticle(`${article} Website (https://acme.com/)`, "Acme", sourceUrl)).toBe("acme.com");
  });
});
