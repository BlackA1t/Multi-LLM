import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("Manifest V3 permissions", () => {
  const manifest = JSON.parse(readFileSync(resolve("public/manifest.json"), "utf8"));

  it("uses only the approved permissions and no site access", () => {
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.permissions).toEqual(["sidePanel", "nativeMessaging", "storage"]);
    expect(manifest).not.toHaveProperty("host_permissions");
    expect(manifest).not.toHaveProperty("content_scripts");
    expect(manifest).not.toHaveProperty("externally_connectable");
    expect(manifest).not.toHaveProperty("optional_host_permissions");
  });

  it("declares a local side panel and module service worker", () => {
    expect(manifest.side_panel.default_path).toBe("sidepanel.html");
    expect(manifest.background).toEqual({ service_worker: "background.js", type: "module" });
  });
});
