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

  it.each(["/company/acme.com", "../company/acme.com", "company/acme.com", "/company?website=acme.com"])("ignores unresolved relative link paths (%s)", (href) => {
    const sourceUrl = "https://publisher.com/news/acme";
    const article = `Profile (${href})`;
    expect(extractDomainFromArticle(article, "Acme", sourceUrl)).toBeNull();
    expect(extractDomainFromArticle(`${article} Visit acme.com`, "Acme", sourceUrl)).toBe("acme.com");
  });

  it.each([
    ["publisher.com", "news.publisher.com"],
    ["news.publisher.com", "publisher.com"],
    ["news.publisher.com", "profiles.publisher.com"],
    ["news.publisher.co.uk", "profiles.publisher.co.uk"],
  ])("excludes publisher domains and subdomains (%s -> %s)", (sourceHost, linkHost) => {
    const sourceUrl = `https://${sourceHost}/news/acme`;
    const article = `Profile (https://${linkHost}/company/acme.com) Website https://${linkHost}/company/acme.com Website https://${linkHost}/company/acme.com`;
    expect(extractDomainFromArticle(article, "Publisher", sourceUrl)).toBeNull();
    expect(extractDomainFromArticle(`${article} Website (https://acme.com/)`, "Acme", sourceUrl)).toBe("acme.com");
  });

  it("uses the complete hostname of an absolute company link", () => {
    expect(extractDomainFromArticle("Website (https://acme.example.com/company/other.com)", "Acme", "https://publisher.com/story"))
      .toBe("acme.example.com");
  });

  it.each(["Visit acme.com", "Acme builds tools at acme.com.", "Website (https://www.acme.com/about)", "Contact hi@acme.com"])("keeps legitimate company domains (%s)", (article) => {
    expect(extractDomainFromArticle(article, "Acme", "https://publisher.com/story")).toBe("acme.com");
  });

  it("keeps other companies on the same country-code suffix", () => {
    expect(extractDomainFromArticle("Website (https://acme.co.uk/about)", "Acme", "https://news.publisher.co.uk/story"))
      .toBe("acme.co.uk");
  });
});
