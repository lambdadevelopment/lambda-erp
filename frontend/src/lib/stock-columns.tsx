/**
 * Extra columns for the stock view (Stock by Location & Batch).
 *
 * A deployment that keeps its own data per batch or storage location (a
 * certificate number, a temperature zone) adds it to the report rows on the
 * server (hook "Report:stock-detail") and registers a column for it here.
 */
import type { ReactNode } from "react";

export interface StockColumn {
  /** Key of the value in the report row. */
  key: string;
  label: string;
  /** Custom cell; default is the value as text. */
  render?: (row: Record<string, any>) => ReactNode;
}

const COLUMNS: StockColumn[] = [];

export function registerStockColumn(column: StockColumn) {
  const existing = COLUMNS.findIndex((c) => c.key === column.key);
  if (existing >= 0) COLUMNS[existing] = column;
  else COLUMNS.push(column);
}

export function getStockColumns(): StockColumn[] {
  return COLUMNS;
}
