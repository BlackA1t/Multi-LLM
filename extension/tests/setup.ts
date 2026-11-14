import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => cleanup());

const storage = new Map<string, unknown>();

Object.defineProperty(globalThis, "chrome", {
  configurable: true,
  value: {
    runtime: {
      id: "abcdefghijklmnopabcdefghijklmnop",
      getURL: (path: string) => `chrome-extension://abcdefghijklmnopabcdefghijklmnop/${path}`,
      sendMessage: vi.fn(),
      onMessage: { addListener: vi.fn(), removeListener: vi.fn() },
    },
    storage: {
      local: {
        get: vi.fn(async (key: string) => ({ [key]: storage.get(key) })),
        set: vi.fn(async (values: Record<string, unknown>) => {
          Object.entries(values).forEach(([key, value]) => storage.set(key, value));
        }),
        remove: vi.fn(async (key: string) => storage.delete(key)),
      },
    },
    tabs: { create: vi.fn(async () => undefined) },
  },
});

if (!globalThis.crypto.randomUUID) {
  Object.defineProperty(globalThis.crypto, "randomUUID", {
    value: () => "123e4567-e89b-42d3-a456-426614174000",
  });
}
