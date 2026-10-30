// Drag-to-reorder list without a dependency. Pointer events cover mouse, pen
// and touch; the handle also takes ArrowUp / ArrowDown for keyboard users.
// The list reorders live as the pointer crosses a sibling's midpoint, and
// scrolls when the pointer nears the top or bottom of the viewport.
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";

export interface SortableHandleProps {
  onPointerDown: (e: PointerEvent<HTMLElement>) => void;
  onPointerMove: (e: PointerEvent<HTMLElement>) => void;
  onPointerUp: (e: PointerEvent<HTMLElement>) => void;
  onPointerCancel: (e: PointerEvent<HTMLElement>) => void;
  onKeyDown: (e: KeyboardEvent<HTMLElement>) => void;
  style: { touchAction: "none"; cursor: "grab" | "grabbing" };
}

const EDGE = 80; // px from the viewport edge where dragging scrolls

function scrollParent(el: HTMLElement | null): HTMLElement | Window {
  for (let node = el?.parentElement; node; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if ((overflowY === "auto" || overflowY === "scroll") && node.scrollHeight > node.clientHeight) return node;
  }
  return window;
}

export function Sortable<T>({ items, keyOf, onReorder, className, children }: {
  items: T[];
  keyOf: (item: T) => string;
  onReorder: (next: T[]) => void;
  className?: string;
  children: (item: T, ctx: { handle: SortableHandleProps; dragging: boolean; index: number }) => ReactNode;
}) {
  const refs = useRef(new Map<string, HTMLLIElement>());
  const list = useRef(items);
  list.current = items;
  const [dragging, setDragging] = useState<string | null>(null);
  const pointerY = useRef(0);
  const frame = useRef(0);

  const moveTo = useCallback((key: string, to: number) => {
    const next = [...list.current];
    const from = next.findIndex((item) => keyOf(item) === key);
    if (from < 0 || to < 0 || to >= next.length || from === to) return;
    next.splice(to, 0, ...next.splice(from, 1));
    list.current = next; // later events in the same frame see the new order
    onReorder(next);
  }, [keyOf, onReorder]);

  // Where the dragged entry belongs: after every sibling whose midpoint is
  // above the pointer. Stable whichever side of a sibling the entry sits on.
  const follow = useCallback((key: string) => {
    const above = list.current.filter((item) => {
      if (keyOf(item) === key) return false;
      const rect = refs.current.get(keyOf(item))?.getBoundingClientRect();
      return !!rect && rect.top + rect.height / 2 < pointerY.current;
    }).length;
    moveTo(key, above);
  }, [keyOf, moveTo]);

  useEffect(() => {
    if (!dragging) return;
    const target = scrollParent(refs.current.get(dragging) ?? null);
    const tick = () => {
      const y = pointerY.current;
      const speed = y < EDGE ? -(EDGE - y) / 4 : y > innerHeight - EDGE ? (y - innerHeight + EDGE) / 4 : 0;
      if (speed) {
        target.scrollBy(0, speed);
        follow(dragging);
      }
      frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame.current);
  }, [dragging, follow]);

  const handle = (key: string, index: number): SortableHandleProps => ({
    onPointerDown: (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.currentTarget.setPointerCapture(e.pointerId);
      pointerY.current = e.clientY;
      setDragging(key);
    },
    onPointerMove: (e) => {
      if (dragging !== key) return;
      pointerY.current = e.clientY;
      follow(key);
    },
    onPointerUp: () => setDragging(null),
    onPointerCancel: () => setDragging(null),
    onKeyDown: (e) => {
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      e.preventDefault();
      moveTo(key, index + (e.key === "ArrowUp" ? -1 : 1));
    },
    style: { touchAction: "none", cursor: dragging === key ? "grabbing" : "grab" },
  });

  return (
    <ul className={className}>
      {items.map((item, index) => {
        const key = keyOf(item);
        return (
          <li key={key} ref={(el) => { if (el) refs.current.set(key, el); else refs.current.delete(key); }}>
            {children(item, { handle: handle(key, index), dragging: dragging === key, index })}
          </li>
        );
      })}
    </ul>
  );
}
