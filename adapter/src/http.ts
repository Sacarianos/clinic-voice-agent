import { zValidator } from "@hono/zod-validator";
import type { ZodType } from "zod";

// Request bodies carry names and dates of birth, so a rejected body is never echoed back.
export const validJson = <T extends ZodType>(schema: T) =>
  zValidator("json", schema, (result, c) => {
    if (!result.success) return c.json({ error: "invalid_request" }, 400);
  });

export const validQuery = <T extends ZodType>(schema: T) =>
  zValidator("query", schema, (result, c) => {
    if (!result.success) return c.json({ error: "invalid_request" }, 400);
  });
