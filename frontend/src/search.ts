/** Tokenized, separator-insensitive search — the client-side twin of the
 * backend's `app/catalog/search.py` ("yoga mat" matches
 * "Yoga-Mat-Cotton-Brown"). The query splits into alphanumeric tokens and a
 * row matches when any ONE field contains ALL the tokens; word order and
 * separators (spaces, hyphens, underscores) don't matter. Tokens don't mix
 * across fields, so a short numeric token can't drag in every barcode. An
 * empty or punctuation-only query matches everything, so callers can apply
 * it unconditionally. */
export function matchesSearch(
  query: string,
  ...fields: Array<string | null | undefined>
): boolean {
  const tokens = query.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  if (tokens.length === 0) return true;
  return fields.some((f) => {
    if (!f) return false;
    const low = f.toLowerCase();
    return tokens.every((t) => low.includes(t));
  });
}
