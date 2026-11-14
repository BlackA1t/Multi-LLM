import { describe, expect, it } from "vitest";

import { isHelloResult, parseNativeResponse } from "../src/native/contracts";

describe("native response validation", () => {
  it("accepts the real result envelope", () => {
    expect(parseNativeResponse({ v: 1, id: "request", ok: true, result: { value: 1 } })).toEqual({
      v: 1, id: "request", ok: true, result: { value: 1 },
    });
  });

  it("rejects data aliases, malformed errors, and unsupported versions", () => {
    expect(parseNativeResponse({ v: 1, id: "request", ok: true, data: {} })).toBeNull();
    expect(parseNativeResponse({ v: 2, id: "request", ok: true, result: {} })).toBeNull();
    expect(parseNativeResponse({ v: 1, id: null, ok: false, error: { code: "X", message: "x" } })).toBeNull();
  });

  it("requires protocol one and a string operation catalog", () => {
    expect(isHelloResult({ protocol_version: 1, operations: ["system.hello"] })).toBe(true);
    expect(isHelloResult({ protocol_version: 2, operations: ["system.hello"] })).toBe(false);
    expect(isHelloResult({ protocol_version: 1, operations: [1] })).toBe(false);
  });
});
