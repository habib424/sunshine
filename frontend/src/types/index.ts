export interface Upload {
  id: string;
  filename: string;
  original_name: string;
  file_type: string | null;
  status: string;
  uploaded_at: string;
  row_count: number | null;
  column_headers: string[] | null;
  sheet_names: string[] | null;
}

export interface FilePreview {
  id: string;
  filename: string;
  headers: string[];
  rows: unknown[][];
  total_rows: number;
}

export interface Job {
  id: string;
  playbook_name: string;
  status: string;
  current_stage: string | null;
  created_at: string;
  updated_at: string;
  error_message: string | null;
  output_path: string | null;
}

export interface Playbook {
  name: string;
  display_name: string;
  description: string;
  file_types: string[];
}

export interface ValidationIssue {
  id: string;
  severity: string;
  row_number: number | null;
  column_name: string | null;
  message: string;
  validator_name: string;
}

export interface ValidationSummary {
  errors: number;
  warnings: number;
  info: number;
  total: number;
  issues: ValidationIssue[];
}

// Rule engine types
export interface TransformRule {
  id: string;
  type: string;
  label: string;
  description: string;
  enabled: boolean;
  ai_suggested: boolean;
  config: Record<string, unknown>;
}

export interface AnalysisV2 {
  summary: string;
  goal: string;
  target_schema: string;
  rules: TransformRule[];
  entities_found: string[];
  sheet_names: string[];
  confidence: number;
}

export interface PreviewResponse {
  headers: string[];
  rows: unknown[][];
  total_rows: number;
  errors: string[];
}

export interface ReconciliationSummary {
  journal_file: string;
  journal_files?: string[];
  trial_balance_files: string[];
  tolerance: number;
  entities: number;
  accounts: number;
  matched: number;
  mismatches: number;
  missing_in_journal: number;
  missing_in_trial_balance: number;
  light_balance: number;
  trial_balance: number;
  variance: number;
  max_abs_variance: number;
}

export interface ReconciliationEntitySummary {
  entity: string;
  accounts: number;
  matched: number;
  mismatches: number;
  missing_in_journal: number;
  missing_in_trial_balance: number;
  light_balance: number;
  trial_balance: number;
  variance: number;
  max_abs_variance: number;
}

export interface ReconciliationAccountDetail {
  entity: string;
  account_code: string;
  account_description: string;
  currency: string;
  light_debit: number;
  light_credit: number;
  light_balance: number;
  trial_balance: number;
  variance: number;
  status: string;
  light_lines: number;
  light_entries: number;
  tb_rows: number;
  tb_source_file: string;
}

export interface ReconciliationMapping {
  source_file: string;
  entity: string;
  confidence: number;
  balance_column: string;
  rows: number;
  accounts: number;
  total_balance: number;
}

export interface ReconciliationFileClassification {
  upload_id: string;
  filename: string;
  stored_filename: string;
  kind: "journal" | "trial_balance" | "unknown";
  confidence: number;
  reason: string;
}

export interface ReconciliationResult {
  job_id: string;
  status: string;
  output_filename: string;
  summary: ReconciliationSummary;
  entity_summary: ReconciliationEntitySummary[];
  account_details: ReconciliationAccountDetail[];
  trial_balance_mappings: ReconciliationMapping[];
  file_classifications: ReconciliationFileClassification[];
}
