const BASE_URL = "/api";

// AuthGate listens for this to flip back to the sign-in screen when a
// session expires mid-use.
export const UNAUTHORIZED_EVENT = "sunshine:unauthorized";

function notifyUnauthorized(res: Response) {
  if (res.status === 401) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  }
}

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    notifyUnauthorized(res);
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export async function uploadFiles(files: File[]) {
  const formData = new FormData();
  files.forEach((f) => formData.append("files", f));
  const res = await fetch(`${BASE_URL}/uploads`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    notifyUnauthorized(res);
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || "Upload failed");
  }
  return res.json();
}

// --- Auth ---

export interface AuthConfig {
  auth_required: boolean;
  client_id: string;
  allowed_domain: string;
}

export interface AuthUser {
  email: string;
  name: string;
  picture: string;
}

export function getAuthConfig(): Promise<AuthConfig> {
  return request("/auth/config");
}

export function getMe(): Promise<AuthUser> {
  return request("/auth/me");
}

export function loginWithGoogle(credential: string): Promise<AuthUser> {
  return request("/auth/google", {
    method: "POST",
    body: JSON.stringify({ credential }),
  });
}

export function logout(): Promise<{ status: string }> {
  return request("/auth/logout", { method: "POST" });
}

export async function getUploads() {
  return request<any[]>("/uploads");
}

export async function getUploadPreview(id: string) {
  return request<any>(`/uploads/${id}/preview`);
}

export async function deleteUpload(id: string) {
  return request<any>(`/uploads/${id}`, { method: "DELETE" });
}

export async function getPlaybooks() {
  return request<any[]>("/playbooks");
}

export async function createJob(playbookName: string, uploadIds: string[]) {
  return request<any>("/jobs", {
    method: "POST",
    body: JSON.stringify({ playbook_name: playbookName, upload_ids: uploadIds }),
  });
}

export async function runJob(jobId: string) {
  return request<any>(`/jobs/${jobId}/run`, { method: "POST" });
}

export async function getJobs() {
  return request<any>("/jobs");
}

export async function getJob(jobId: string) {
  return request<any>(`/jobs/${jobId}`);
}

export async function getValidation(jobId: string) {
  return request<any>(`/jobs/${jobId}/validation`);
}

export function getExportUrl(jobId: string) {
  return `${BASE_URL}/jobs/${jobId}/export`;
}

export async function runReconciliation(
  journalUploadId: string,
  trialBalanceUploadIds: string[],
  tolerance = 0.01
) {
  return request<any>("/reconciliation/run", {
    method: "POST",
    body: JSON.stringify({
      journal_upload_id: journalUploadId,
      tb_upload_ids: trialBalanceUploadIds,
      tolerance,
    }),
  });
}

export async function identifyReconciliationFiles(uploadIds: string[]) {
  return request<any>("/reconciliation/identify", {
    method: "POST",
    body: JSON.stringify({ upload_ids: uploadIds }),
  });
}

export async function runAutoReconciliation(uploadIds: string[], tolerance = 0.01) {
  return request<any>("/reconciliation/auto-run", {
    method: "POST",
    body: JSON.stringify({ upload_ids: uploadIds, tolerance }),
  });
}

export async function analyzeUpload(uploadId: string) {
  return request<any>(`/analyze/${uploadId}`, { method: "POST" });
}

export async function runDirect(uploadId: string, playbookConfig: any, outputFilename: string) {
  return request<any>("/jobs/run-direct", {
    method: "POST",
    body: JSON.stringify({
      upload_id: uploadId,
      playbook_config: playbookConfig,
      output_filename: outputFilename,
    }),
  });
}

export async function startChat(uploadId: string, intent?: string, sheet?: string | null) {
  return request<any>(`/chat/start/${uploadId}`, {
    method: "POST",
    body: JSON.stringify({
      intent: intent || "convert_to_light_je",
      sheet: sheet || undefined,
    }),
  });
}

export async function sendChatMessage(sessionId: string, message: string) {
  return request<any>(`/chat/message/${sessionId}`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export async function executeChat(sessionId: string, outputFilename: string) {
  return request<any>(`/chat/execute/${sessionId}`, {
    method: "POST",
    body: JSON.stringify({ output_filename: outputFilename }),
  });
}

// ---------------------------------------------------------------------------
// Deterministic ingest API
// ---------------------------------------------------------------------------

export async function getIntents() {
  return request<any[]>("/ingest/intents");
}

export async function analyzeWithIntent(uploadId: string, intent: string) {
  return request<any>(`/ingest/analyze/${uploadId}`, {
    method: "POST",
    body: JSON.stringify({ intent }),
  });
}

export async function confirmLayout(uploadId: string, intent: string, layout: any) {
  return request<any>(`/ingest/confirm/${uploadId}`, {
    method: "POST",
    body: JSON.stringify({ intent, layout }),
  });
}

// ---------------------------------------------------------------------------
// Validation rules API
// ---------------------------------------------------------------------------

export async function getRulesContracts() {
  return request<string[]>("/rules/contracts");
}

export async function getRules(contract: string) {
  return request<any>(`/rules/${contract}`);
}

export async function getRule(contract: string, ruleId: string) {
  return request<any>(`/rules/${contract}/${ruleId}`);
}

export async function updateRule(contract: string, ruleId: string, updates: any) {
  return request<any>(`/rules/${contract}/${ruleId}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
}

export async function createRule(contract: string, rule: any) {
  return request<any>(`/rules/${contract}`, {
    method: "POST",
    body: JSON.stringify(rule),
  });
}

export async function deleteRule(contract: string, ruleId: string) {
  return request<any>(`/rules/${contract}/${ruleId}`, { method: "DELETE" });
}

export async function getCheckTypes() {
  return request<any>("/rules/check-types");
}

export async function generateRule(contract: string, description: string) {
  return request<any>("/rules/generate", {
    method: "POST",
    body: JSON.stringify({ contract, description }),
  });
}

// ---------------------------------------------------------------------------
// Legacy preview API
// ---------------------------------------------------------------------------

export async function previewRules(uploadId: string, rules: any[], limit = 20) {
  return request<any>(`/preview/${uploadId}`, {
    method: "POST",
    body: JSON.stringify({ rules, limit }),
  });
}

export async function getRuleTypes() {
  return request<any>("/preview/rule-types");
}
