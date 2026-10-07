import { doubleMetaphone } from "double-metaphone";

// Lowercase ASCII letters only. Strips accents, apostrophes, hyphens and the spaces of a spelled-out name ("S M Y T H").
export const normalizeName = (name: string) =>
  name
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .replace(/[^a-z]/g, "");

export function soundsAlike(stated: string, recorded: string): boolean {
  const statedCodes = doubleMetaphone(normalizeName(stated));
  const recordedCodes = doubleMetaphone(normalizeName(recorded));
  return statedCodes.some((code) => code !== "" && recordedCodes.includes(code));
}
