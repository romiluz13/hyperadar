/** Shared ObjectId helper — validates input, rejects invalid (no raw fallback). */

import { ObjectId } from "mongodb";

/** Parse a string to ObjectId. Throws on invalid input (caller should validate first). */
export function toObjectId(id: string): ObjectId {
  return new ObjectId(id);
}

/** Runtime validation: must be a string and a valid ObjectId. Use before any MongoDB query. */
export function isValidObjectId(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && ObjectId.isValid(value);
}
