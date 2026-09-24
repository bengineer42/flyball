import { describe, expect, it } from "vitest";
import type { JsonSchema } from "@flyball/client";
import { argsOf, commandsOf, enforceWaitTimeSpelling, fromForm, inOrder, sameValue, splitStep, timeEntries, toForm, withTime } from "../src/programDoc.js";

/**
 * A minimal `GET /api/programs/schema` fixture for `wait` and `prompt`, in the wf1(b) shape
 * (C14): every step's `timeout` is a Duration, nested, never folded flat; `wait`'s one other
 * time field, `duration`, is the one folded flat beside it (`minutes`, `seconds`, ...) -- except
 * (C9) when `wait` also carries a `message`, where the loader refuses the flat spelling and
 * `duration` must be written nested too. `prompt` has no foldable field at all, so it takes no
 * flat time keys.
 */
const DURATION: JsonSchema = {
  title: "Duration",
  anyOf: [
    { type: "number" },
    {
      type: "object",
      properties: {
        nanoseconds: { type: "number" },
        microseconds: { type: "number" },
        milliseconds: { type: "number" },
        seconds: { type: "number" },
        minutes: { type: "number" },
        hours: { type: "number" },
        days: { type: "number" },
      },
    },
  ],
};

const programSchema: JsonSchema = {
  $defs: { Duration: DURATION },
  properties: {
    steps: {
      items: {
        oneOf: [
          {
            type: "object",
            required: ["wait"],
            properties: {
              wait: {
                type: "object",
                title: "Wait",
                properties: {
                  duration: { $ref: "#/$defs/Duration" },
                  message: { type: ["string", "null"], default: null },
                  timeout: { anyOf: [{ $ref: "#/$defs/Duration" }, { type: "null" }], default: null },
                  // the flat spelling `foldable()` hoists beside `duration`, its only non-timeout time field
                  nanoseconds: { type: "number" },
                  microseconds: { type: "number" },
                  milliseconds: { type: "number" },
                  seconds: { type: "number" },
                  minutes: { type: "number" },
                  hours: { type: "number" },
                  days: { type: "number" },
                },
              },
            },
          },
          {
            type: "object",
            required: ["prompt"],
            properties: {
              prompt: {
                anyOf: [
                  { type: "string" },
                  {
                    type: "object",
                    title: "Prompt",
                    properties: {
                      message: { type: "string" },
                      name: { type: ["string", "null"], default: null },
                      timeout: { anyOf: [{ $ref: "#/$defs/Duration" }, { type: "null" }], default: null },
                    },
                    required: ["message"],
                  },
                ],
              },
            },
          },
        ],
      },
    },
  },
};

describe("programDoc: timeout is never the folded field (C14)", () => {
  const commands = commandsOf(programSchema);
  const wait = commands.find((c) => c.tag === "wait")!;
  const prompt = commands.find((c) => c.tag === "prompt")!;

  it("finds `duration`, not `timeout`, as wait's composite time field", () => {
    expect(wait.time?.name).toBe("duration");
    expect(wait.time?.keys).toContain("minutes");
    expect(wait.time?.keys).not.toContain("timeout");
  });

  it("gives prompt no composite time field at all: it has no flat time keys to fold", () => {
    expect(prompt.time).toBeUndefined();
  });

  it("a prompt with a timeout serialises the timeout nested, never flat", () => {
    const value = { message: "close the door", timeout: { minutes: 5 } };
    const form = toForm(value, prompt);
    // No composite time field on prompt: the form holds `timeout` exactly as given, untouched.
    expect(form.timeout).toEqual({ minutes: 5 });
    const back = fromForm(form, prompt);
    expect(back).toEqual(value);
    expect(back).not.toHaveProperty("minutes"); // never hoisted flat
  });

  it("a wait with only a timeout (no message) folds a flat duration key beside it", () => {
    const value: Record<string, unknown> = { minutes: 10, timeout: { minutes: 3 } };
    const field = wait.time!;
    const entries = timeEntries(value, field);
    expect(entries).toEqual([["minutes", 10]]);
    const form = toForm(value, wait);
    expect(form).toEqual({ timeout: { minutes: 3 } });
    expect(form).not.toHaveProperty("minutes");
    expect(form).not.toHaveProperty("duration");
    const written = withTime(value, field, "minutes", 10);
    expect(written).toEqual({ minutes: 10, timeout: { minutes: 3 } });
  });
});

/**
 * C9: the loader refuses a timed `wait` that spells its duration flat while it also carries a
 * `message` (`wait: {minutes: 20, message: "soak"}`); it must be written nested instead
 * (`wait: {duration: {minutes: 20}, message: "soak"}`). No `message`: flat is fine, same as any
 * other folded field. `timeout` is never folded either way (C14). `enforceWaitTimeSpelling` is
 * what `ProgramBuilder` runs after every edit to a step's arguments to keep this true; parsing
 * (`splitStep`/`argsOf`) accepts either spelling on the way in regardless.
 */
describe("programDoc: a timed wait with a message nests its duration (C9)", () => {
  const commands = commandsOf(programSchema);
  const wait = commands.find((c) => c.tag === "wait")!;

  it("a wait WITH a message serialises duration nested, with timeout nested too", () => {
    const flat: Record<string, unknown> = { minutes: 20, message: "soak", timeout: { minutes: 10 } };
    const fixed = enforceWaitTimeSpelling("wait", flat, wait.time);
    expect(fixed).toEqual({ message: "soak", timeout: { minutes: 10 }, duration: { minutes: 20 } });
    expect(fixed).not.toHaveProperty("minutes");
  });

  it("a wait WITHOUT a message serialises flat, with timeout nested", () => {
    const flat: Record<string, unknown> = { minutes: 20, timeout: { minutes: 10 } };
    const fixed = enforceWaitTimeSpelling("wait", flat, wait.time);
    expect(fixed).toEqual({ minutes: 20, timeout: { minutes: 10 } }); // untouched: nothing to fix
    expect(fixed).not.toHaveProperty("duration");
  });

  it("leaves an already-nested duration alone, and leaves other commands' args alone", () => {
    const nested: Record<string, unknown> = { message: "soak", duration: { minutes: 20 } };
    expect(enforceWaitTimeSpelling("wait", nested, wait.time)).toBe(nested);
    const other: Record<string, unknown> = { minutes: 20, message: "soak" };
    expect(enforceWaitTimeSpelling("prompt", other, wait.time)).toBe(other);
  });

  it("parsing a doc round-trips both spellings: nested with a message, flat without one", () => {
    const nestedStep = { wait: { duration: { minutes: 5 }, message: "x", timeout: { minutes: 10 } } };
    const { tag: nestedTag, value: nestedValue } = splitStep(nestedStep, commands);
    expect(nestedTag).toBe("wait");
    const nestedCurrent = argsOf(nestedValue, wait);
    const nestedForm = toForm(nestedValue, wait);
    const nestedArgs = fromForm(nestedForm, wait);
    const nestedTime = Object.fromEntries(Object.entries(nestedCurrent).filter(([k]) => wait.time?.keys.includes(k)));
    const nestedRoundTripped = enforceWaitTimeSpelling("wait", inOrder({ ...nestedArgs, ...nestedTime }, nestedCurrent), wait.time);
    expect(sameValue(nestedRoundTripped, nestedCurrent)).toBe(true);
    expect(nestedRoundTripped.duration).toEqual({ minutes: 5 });
    expect(nestedRoundTripped.timeout).toEqual({ minutes: 10 });

    const flatStep = { wait: { minutes: 5, timeout: { minutes: 10 } } };
    const { tag: flatTag, value: flatValue } = splitStep(flatStep, commands);
    expect(flatTag).toBe("wait");
    const flatCurrent = argsOf(flatValue, wait);
    const flatForm = toForm(flatValue, wait);
    const flatArgs = fromForm(flatForm, wait);
    const flatTime = Object.fromEntries(Object.entries(flatCurrent).filter(([k]) => wait.time?.keys.includes(k)));
    const flatRoundTripped = enforceWaitTimeSpelling("wait", inOrder({ ...flatArgs, ...flatTime }, flatCurrent), wait.time);
    expect(sameValue(flatRoundTripped, flatCurrent)).toBe(true);
    expect(flatRoundTripped.minutes).toBe(5);
    expect(flatRoundTripped.timeout).toEqual({ minutes: 10 });
  });
});
