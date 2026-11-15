import { describe, expect, it, vi } from "vitest";

import { openOfficialProvider, PROVIDERS } from "../src/state/provider-catalog";

describe("official provider catalog", () => {
  it("contains nine unique fixed HTTPS destinations without prompt data", () => {
    expect(PROVIDERS).toHaveLength(9);
    expect(new Set(PROVIDERS.map((item) => item.id)).size).toBe(9);
    for (const provider of PROVIDERS) {
      const url = new URL(provider.officialUrl);
      expect(url.protocol).toBe("https:");
      expect(url.search).toBe("");
      expect(url.hash).toBe("");
    }
  });

  it("opens only the catalog URL after an explicit call", () => {
    const create = chrome.tabs.create as unknown as ReturnType<typeof vi.fn>;
    create.mockClear();
    openOfficialProvider("gemini");
    expect(create).toHaveBeenCalledOnce();
    expect(create).toHaveBeenCalledWith({ url: "https://gemini.google.com/" });
    expect(() => openOfficialProvider("unknown")).toThrow("Unknown provider");
  });
});
