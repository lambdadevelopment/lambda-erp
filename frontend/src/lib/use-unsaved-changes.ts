// Tracks whether a form has edits that are not saved yet, and guards them:
// closing or reloading the tab triggers the browser's own "leave site?" prompt,
// and in-app navigation (links, prev/next, back) asks for confirmation first.
//
// Usage: call markDirty() from the user's edit handlers (not from loads or
// programmatic defaults), and markClean() when the record is loaded or saved.
// markClean() takes effect immediately, so a save's onSuccess may navigate
// right after calling it without tripping the guard.
import { useCallback, useEffect, useRef, useState } from "react";
import { useBlocker } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useConfirm } from "@/components/ui/dialog";

export function useUnsavedChanges() {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const [dirty, setDirty] = useState(false);
  // The blocker and save callbacks read the ref, so a navigate() in the same
  // tick as markClean() isn't blocked by the not-yet-rendered state.
  const dirtyRef = useRef(false);

  const markDirty = useCallback(() => {
    dirtyRef.current = true;
    setDirty(true);
  }, []);
  const markClean = useCallback(() => {
    dirtyRef.current = false;
    setDirty(false);
  }, []);

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirtyRef.current && currentLocation.pathname !== nextLocation.pathname,
  );

  // Ask once per blocked navigation; the blocker object itself is read through
  // a ref so a re-render while the dialog is open doesn't open a second one.
  const blockerRef = useRef(blocker);
  blockerRef.current = blocker;
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    confirm({
      title: t("common.unsavedTitle"),
      body: t("common.unsavedBody"),
      confirmLabel: t("common.unsavedLeave"),
      cancelLabel: t("common.unsavedStay"),
      danger: true,
    }).then((leave) => {
      const current = blockerRef.current;
      if (current.state !== "blocked") return;
      if (leave) {
        markClean();
        current.proceed();
      } else {
        current.reset();
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blocker.state]);

  return { dirty, markDirty, markClean };
}
