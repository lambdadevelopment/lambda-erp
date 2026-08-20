import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const PAGE_SIZE_OPTIONS = [25, 50, 100, 200];

type ListPagerProps = {
  page: number;
  totalPages: number;
  pageSize: number;
  total: number;
  rangeStart: number;
  rangeEnd: number;
  setPage: (page: number) => void;
  setPageSize: (pageSize: number) => void;
};

/** Pagination controls intended to be rendered above and below long lists. */
export function ListPager({
  page,
  totalPages,
  pageSize,
  total,
  rangeStart,
  rangeEnd,
  setPage,
  setPageSize,
}: ListPagerProps) {
  const { t } = useTranslation();
  const [pageInput, setPageInput] = useState(String(page + 1));

  useEffect(() => setPageInput(String(page + 1)), [page]);

  const commitPageInput = () => {
    const enteredPage = Number.parseInt(pageInput, 10);
    if (Number.isNaN(enteredPage)) {
      setPageInput(String(page + 1));
      return;
    }
    setPage(Math.min(totalPages, Math.max(1, enteredPage)) - 1);
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-fg-muted">
      <div>
        {t("common.showing")} <span className="font-medium text-fg">{rangeStart}–{rangeEnd}</span>{" "}
        {t("common.of")} <span className="font-medium text-fg">{total}</span>
      </div>
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1.5">
          <span className="text-xs text-fg-muted">{t("common.perPage")}</span>
          <select
            value={pageSize}
            onChange={(event) => setPageSize(Number(event.target.value))}
            className="h-8 rounded-md bg-surface px-2 text-sm text-fg ring-1 ring-line transition-all focus:outline-none focus:ring-2 focus:ring-brand/30"
          >
            {PAGE_SIZE_OPTIONS.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </label>
        <button
          onClick={() => setPage(Math.max(0, page - 1))}
          disabled={page === 0}
          className="rounded-md bg-surface px-3 py-1 text-sm text-fg ring-1 ring-line transition-colors hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-40"
        >
          {t("common.prev")}
        </button>
        <span className="flex items-center gap-1 text-xs">
          {t("common.page")}
          <input
            aria-label={t("common.page")}
            inputMode="numeric"
            value={pageInput}
            onChange={(event) => setPageInput(event.target.value.replace(/[^0-9]/g, ""))}
            onBlur={commitPageInput}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
            className="h-7 w-12 rounded-md bg-surface px-1 text-center text-sm font-medium text-fg ring-1 ring-line focus:outline-none focus:ring-2 focus:ring-brand/30"
          />
          {t("common.of")} <span className="font-medium text-fg">{totalPages}</span>
        </span>
        <button
          onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
          disabled={page >= totalPages - 1}
          className="rounded-md bg-surface px-3 py-1 text-sm text-fg ring-1 ring-line transition-colors hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-40"
        >
          {t("common.next")}
        </button>
      </div>
    </div>
  );
}
