import { doubleMetaphone } from "double-metaphone";

// Lowercase ASCII letters only. Strips accents, apostrophes, hyphens and the spaces of a spelled-out name ("S M Y T H").
export const normalizeName = (name: string) =>
  name
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .replace(/[^a-z]/g, "");

export const surnamesMatch = (stated: string, recorded: string) =>
  soundsAlike(stated, recorded) || nearlySpelledAlike(stated, recorded);

function soundsAlike(stated: string, recorded: string): boolean {
  const statedCodes = doubleMetaphone(normalizeName(stated));
  const recordedCodes = doubleMetaphone(normalizeName(recorded));
  return statedCodes.some((code) => code !== "" && recordedCodes.includes(code));
}

// Speech recognition drops or swaps a sound that double-metaphone keeps (Brennan heard as Brendan, Lindqvist as
// Lindquist). Allow one wrong letter per five, never in the first letter, so short names and names that start
// differently (Kim and Kin, Larson and Carson) still have to sound the same.
function nearlySpelledAlike(stated: string, recorded: string): boolean {
  const a = normalizeName(stated);
  const b = normalizeName(recorded);
  if (a === "" || a[0] !== b[0]) return false;
  return editDistance(a, b) <= Math.floor(Math.max(a.length, b.length) / 5);
}

function editDistance(a: string, b: string): number {
  let previous = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const current = [i];
    for (let j = 1; j <= b.length; j++) {
      current[j] = Math.min(previous[j]! + 1, current[j - 1]! + 1, previous[j - 1]! + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    previous = current;
  }
  return previous[b.length]!;
}
