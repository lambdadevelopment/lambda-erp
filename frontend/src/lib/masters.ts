import type { FieldDef } from "./doctypes";

export interface MasterFilterDef {
  field: string;
  label: string;
}

export interface MasterActionDef {
  action: string;
  label: string;
  resultPath?: string;
  visibleWhen?: (record: Record<string, any>) => boolean;
  buildArgs?: (record: Record<string, any>) => Record<string, any>;
}

export interface MasterConfig {
  slug: string;
  label: string;
  fields: FieldDef[];
  listColumns: string[];
  /** Lightweight fields that may be selected on the list. Avoid bulk text. */
  columnOptions?: string[];
  listFilters?: MasterFilterDef[];
  searchFields?: string[];
  autoName?: boolean;
  allowCreate?: boolean;
  actions?: MasterActionDef[];
  columnLinks?: Record<string, (value: any, row: Record<string, any>) => string | null>;
}

const CONFIGS: Record<string, MasterConfig> = {};

export function registerMaster(config: MasterConfig) {
  CONFIGS[config.slug] = config;
}

export function getMasterConfig(slug: string): MasterConfig | undefined {
  return CONFIGS[slug];
}

export function getAllMasterConfigs(): MasterConfig[] {
  return Object.values(CONFIGS);
}
