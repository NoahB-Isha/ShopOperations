/* How the strip fills its five slots. Pure, and in its own module rather than
   exported from SuggestedStrip.tsx — exporting a non-component from a
   component file breaks Fast Refresh (the same trap centerSignals.ts exists
   for), and a strip whose handlers silently stop updating mid-session is a
   miserable thing to debug.

   People first: a floor ask is someone standing at an empty shelf, so asks
   take slots before anything the numbers found. */
export interface SlotSplit<A, S> {
  asks: A[];
  suggestions: S[];
  /** how many of the two lists didn't fit */
  hidden: number;
  total: number;
}

export function splitSlots<A, S>(asks: A[], suggestions: S[], shown: number): SlotSplit<A, S> {
  const room = Math.max(0, shown);
  const takenAsks = asks.slice(0, room);
  const takenSuggestions = suggestions.slice(0, Math.max(0, room - takenAsks.length));
  const total = asks.length + suggestions.length;
  return {
    asks: takenAsks,
    suggestions: takenSuggestions,
    hidden: total - takenAsks.length - takenSuggestions.length,
    total,
  };
}
