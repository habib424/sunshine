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
