import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useRowSelection } from "../design/ContextMenu";

const plain = { shiftKey: false, metaKey: false, ctrlKey: false };
const shift = { ...plain, shiftKey: true };
const cmd = { ...plain, metaKey: true };

describe("useRowSelection", () => {
  it("plain click selects one; clicking it again deselects", () => {
    const { result } = renderHook(() => useRowSelection([1, 2, 3, 4]));
    act(() => result.current.click(2, plain));
    expect([...result.current.selected]).toEqual([2]);
    act(() => result.current.click(2, plain));
    expect(result.current.selected.size).toBe(0);
  });

  it("cmd-click toggles rows in and out", () => {
    const { result } = renderHook(() => useRowSelection([1, 2, 3, 4]));
    act(() => result.current.click(1, plain));
    act(() => result.current.click(3, cmd));
    expect([...result.current.selected].sort()).toEqual([1, 3]);
    act(() => result.current.click(1, cmd));
    expect([...result.current.selected]).toEqual([3]);
  });

  it("shift-click extends a range from the anchor over visible order", () => {
    const { result } = renderHook(() => useRowSelection([10, 20, 30, 40, 50]));
    act(() => result.current.click(20, plain));
    act(() => result.current.click(40, shift));
    expect([...result.current.selected].sort((a, b) => a - b)).toEqual([20, 30, 40]);
    // shift upward from the same anchor replaces the range
    act(() => result.current.click(10, shift));
    expect([...result.current.selected].sort((a, b) => a - b)).toEqual([10, 20]);
  });

  it("right-click keeps a multi-selection but selects a lone unselected row", () => {
    const { result } = renderHook(() => useRowSelection([1, 2, 3]));
    act(() => result.current.click(1, plain));
    act(() => result.current.click(2, cmd));
    let effective: Set<number> = new Set();
    act(() => {
      effective = result.current.forContext(2);
    });
    expect([...effective].sort()).toEqual([1, 2]); // kept
    act(() => {
      effective = result.current.forContext(3);
    });
    expect([...effective]).toEqual([3]); // reset to the clicked row
  });
});
