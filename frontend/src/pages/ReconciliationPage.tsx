import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileCheck2,
  FileQuestion,
  FileSpreadsheet,
  GitCompare,
  Loader2,
  Play,
  Upload as UploadIcon,
  XCircle,
} from "lucide-react";
import DropZone from "../components/upload/DropZone";
import {
  getExportUrl,
  identifyReconciliationFiles,
  runAutoReconciliation,
  uploadFiles,
} from "../api/client";
import type {
  ReconciliationAccountDetail,
  ReconciliationFileClassification,
  ReconciliationResult,
  Upload as UploadRecord,
} from "../types";

type DetailFilter = "exceptions" | "all";

const STATUS_LABELS: Record<string, string> = {
  matched: "Matched",
  mismatch: "Mismatch",
  missing_in_journal: "Missing in journal",
  missing_in_trial_balance: "Missing in TB",
};

const KIND_LABELS: Record<ReconciliationFileClassification["kind"], string> = {
  journal: "Journal",
  trial_balance: "Trial balance",
  unknown: "Ignored",
};

function formatAmount(value: number | null | undefined) {
  const amount = Number(value || 0);
  const normalized = Math.abs(amount) < 0.005 ? 0 : amount;
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(normalized);
}

function formatCount(value: number | null | undefined) {
  return new Intl.NumberFormat().format(Number(value || 0));
}

function statusClass(status: string) {
  if (status === "matched") return "bg-green-50 text-green-700 border-green-100";
  if (status === "missing_in_journal") return "bg-red-50 text-red-700 border-red-100";
  if (status === "missing_in_trial_balance") return "bg-purple-50 text-purple-700 border-purple-100";
  return "bg-amber-50 text-amber-700 border-amber-100";
}

function kindClass(kind: ReconciliationFileClassification["kind"]) {
  if (kind === "journal") return "bg-blue-50 text-blue-700 border-blue-100";
  if (kind === "trial_balance") return "bg-green-50 text-green-700 border-green-100";
  return "bg-gray-50 text-gray-500 border-gray-100";
}

function kindIcon(kind: ReconciliationFileClassification["kind"]) {
  if (kind === "unknown") return FileQuestion;
  return FileCheck2;
}

function exceptionCount(result: ReconciliationResult | null) {
  if (!result) return 0;
  const summary = result.summary;
  return summary.mismatches + summary.missing_in_journal + summary.missing_in_trial_balance;
}

export default function ReconciliationPage() {
  const [batchUploads, setBatchUploads] = useState<UploadRecord[]>([]);
  const [classifications, setClassifications] = useState<ReconciliationFileClassification[]>([]);
  const [tolerance, setTolerance] = useState(0.01);
  const [isUploading, setIsUploading] = useState(false);
  const [isIdentifying, setIsIdentifying] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<ReconciliationResult | null>(null);
  const [detailFilter, setDetailFilter] = useState<DetailFilter>("exceptions");
  const [error, setError] = useState<string | null>(null);

  const uploadIds = useMemo(() => batchUploads.map((upload) => upload.id), [batchUploads]);
  const journals = classifications.filter((file) => file.kind === "journal");
  const trialBalances = classifications.filter((file) => file.kind === "trial_balance");
  const ignored = classifications.filter((file) => file.kind === "unknown");

  const filteredDetails = useMemo(() => {
    const details = result?.account_details || [];
    if (detailFilter === "all") return details;
    return details.filter((row) => row.status !== "matched");
  }, [result, detailFilter]);

  const runBatch = async (ids: string[]) => {
    if (!ids.length) return;
    setIsRunning(true);
    setError(null);
    setResult(null);
    try {
      const data = await runAutoReconciliation(ids, tolerance);
      setResult(data);
      setClassifications(data.file_classifications || []);
      setDetailFilter("exceptions");
    } catch (e: any) {
      setError(e.message || "Reconciliation failed");
    } finally {
      setIsRunning(false);
    }
  };

  const handleFilesSelected = async (files: File[]) => {
    if (files.length === 0) return;
    setIsUploading(true);
    setIsIdentifying(false);
    setError(null);
    setResult(null);
    setClassifications([]);

    try {
      const uploaded = await uploadFiles(files);
      const ids = uploaded.map((upload: UploadRecord) => upload.id);
      setBatchUploads(uploaded);

      setIsIdentifying(true);
      const identified = await identifyReconciliationFiles(ids);
      setClassifications(identified.file_classifications || []);
      setIsIdentifying(false);

      if (identified.journal_upload_ids.length === 0) {
        setError("No Light journal file was identified in this upload batch");
        return;
      }
      if (identified.tb_upload_ids.length === 0) {
        setError("No trial balance file was identified in this upload batch");
        return;
      }

      await runBatch(ids);
    } catch (e: any) {
      setError(e.message || "Upload failed");
    } finally {
      setIsUploading(false);
      setIsIdentifying(false);
    }
  };

  const handleRunAgain = async () => {
    await runBatch(uploadIds);
  };

  return (
    <div className="max-w-6xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <GitCompare className="w-6 h-6 text-sunshine-600" />
          Reconciliation
        </h1>
        <p className="text-gray-500 mt-1 text-sm">Account-level journal to trial balance matching</p>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm flex items-start justify-between gap-2">
          <div>{error}</div>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600 font-bold text-lg leading-none">
            &times;
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-5 mb-6">
        <div className="space-y-5">
          <section className="bg-white border border-gray-200 rounded-lg p-4">
            <div className="flex items-center gap-2 mb-3">
              <UploadIcon className="w-4 h-4 text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-900">Upload batch</h2>
            </div>
            <DropZone onFilesSelected={handleFilesSelected} isUploading={isUploading || isIdentifying || isRunning} />
          </section>

          <section className="bg-white border border-gray-200 rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-900">Run</h2>
              <label className="flex items-center gap-2 text-xs text-gray-500">
                Tolerance
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={tolerance}
                  onChange={(e) => setTolerance(Number(e.target.value) || 0)}
                  className="w-20 px-2 py-1 border border-gray-200 rounded text-right text-gray-700 focus:outline-none focus:border-sunshine-400"
                />
              </label>
            </div>
            <button
              onClick={handleRunAgain}
              disabled={!uploadIds.length || isUploading || isIdentifying || isRunning}
              className="w-full px-4 py-2.5 bg-sunshine-500 text-white rounded-lg hover:bg-sunshine-600 disabled:opacity-50 disabled:cursor-not-allowed font-medium text-sm flex items-center justify-center gap-2"
            >
              {isRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              Reconcile batch
            </button>
            {result && (
              <a
                href={getExportUrl(result.job_id)}
                download
                className="mt-3 w-full px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 font-medium text-sm flex items-center justify-center gap-2"
              >
                <Download className="w-4 h-4" />
                Download report
              </a>
            )}
          </section>
        </div>

        <section className="bg-white border border-gray-200 rounded-lg p-4 min-w-0">
          <div className="flex items-center justify-between gap-3 mb-4">
            <div className="flex items-center gap-2">
              <FileSpreadsheet className="w-4 h-4 text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-900">Identified files</h2>
            </div>
            {(isUploading || isIdentifying || isRunning) && (
              <div className="flex items-center gap-2 text-xs text-gray-500">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                {isUploading ? "Uploading" : isIdentifying ? "Identifying" : "Reconciling"}
              </div>
            )}
          </div>

          {batchUploads.length === 0 ? (
            <div className="border border-dashed border-gray-200 rounded-lg py-12 text-center text-sm text-gray-400">
              No batch uploaded
            </div>
          ) : (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-3">
                {[
                  { label: "Journal", value: journals.length, color: "text-blue-700" },
                  { label: "Trial balances", value: trialBalances.length, color: "text-green-700" },
                  { label: "Ignored", value: ignored.length, color: "text-gray-500" },
                ].map((item) => (
                  <div key={item.label} className="border border-gray-200 rounded-lg p-3">
                    <p className="text-xs text-gray-400">{item.label}</p>
                    <p className={`text-xl font-semibold ${item.color}`}>{formatCount(item.value)}</p>
                  </div>
                ))}
              </div>

              <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                {classifications.map((file) => {
                  const Icon = kindIcon(file.kind);
                  return (
                    <div key={file.upload_id} className="border border-gray-200 rounded-lg p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-800 truncate">{file.filename}</p>
                          <p className="text-xs text-gray-400 mt-0.5 truncate">{file.reason}</p>
                        </div>
                        <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full border text-xs font-medium whitespace-nowrap ${kindClass(file.kind)}`}>
                          <Icon className="w-3 h-3" />
                          {KIND_LABELS[file.kind]}
                        </span>
                      </div>
                    </div>
                  );
                })}
                {classifications.length === 0 && (
                  <div className="py-8 text-center text-sm text-gray-400">Waiting for identification</div>
                )}
              </div>
            </div>
          )}
        </section>
      </div>

      {isRunning && (
        <div className="bg-white border border-gray-200 rounded-lg p-8 text-center">
          <Loader2 className="w-7 h-7 text-sunshine-500 animate-spin mx-auto mb-3" />
          <p className="text-sm font-medium text-gray-700">Reconciling accounts...</p>
        </div>
      )}

      {result && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[
              {
                label: "Exceptions",
                value: formatCount(exceptionCount(result)),
                icon: exceptionCount(result) ? AlertTriangle : CheckCircle2,
                color: exceptionCount(result) ? "text-amber-600" : "text-green-600",
              },
              { label: "Matched", value: formatCount(result.summary.matched), icon: CheckCircle2, color: "text-green-600" },
              { label: "Accounts", value: formatCount(result.summary.accounts), icon: FileSpreadsheet, color: "text-gray-600" },
              { label: "Max variance", value: formatAmount(result.summary.max_abs_variance), icon: XCircle, color: "text-red-600" },
            ].map(({ label, value, icon: Icon, color }) => (
              <div key={label} className="bg-white border border-gray-200 rounded-lg p-4">
                <div className="flex items-center gap-2 text-sm text-gray-500">
                  <Icon className={`w-4 h-4 ${color}`} />
                  {label}
                </div>
                <p className="text-2xl font-bold text-gray-900 mt-2">{value}</p>
              </div>
            ))}
          </div>

          <section className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-gray-900">Entity summary</h2>
              <span className="text-xs text-gray-400">{formatCount(result.summary.entities)} entities</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Entity</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Matched</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Mismatch</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Missing JE</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Light balance</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">TB balance</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Variance</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {result.entity_summary.map((row) => (
                    <tr key={row.entity} className="hover:bg-gray-50">
                      <td className="px-4 py-2 font-medium text-gray-800 whitespace-nowrap">{row.entity}</td>
                      <td className="px-4 py-2 text-right text-green-700">{formatCount(row.matched)}</td>
                      <td className="px-4 py-2 text-right text-amber-700">{formatCount(row.mismatches)}</td>
                      <td className="px-4 py-2 text-right text-red-700">{formatCount(row.missing_in_journal)}</td>
                      <td className="px-4 py-2 text-right font-mono text-xs">{formatAmount(row.light_balance)}</td>
                      <td className="px-4 py-2 text-right font-mono text-xs">{formatAmount(row.trial_balance)}</td>
                      <td className="px-4 py-2 text-right font-mono text-xs">{formatAmount(row.variance)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-gray-900">Account detail</h2>
              <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1">
                {(["exceptions", "all"] as DetailFilter[]).map((filter) => (
                  <button
                    key={filter}
                    onClick={() => setDetailFilter(filter)}
                    className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                      detailFilter === filter ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"
                    }`}
                  >
                    {filter === "exceptions" ? "Exceptions" : "All"}
                  </button>
                ))}
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Entity</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Account</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Status</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Light</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">TB</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Variance</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Lines</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {filteredDetails.map((row: ReconciliationAccountDetail) => (
                    <tr key={`${row.entity}-${row.account_code}-${row.status}`} className="hover:bg-gray-50">
                      <td className="px-4 py-2 text-gray-700 whitespace-nowrap">{row.entity}</td>
                      <td className="px-4 py-2 min-w-72">
                        <div className="font-mono text-xs text-gray-900">{row.account_code}</div>
                        <div className="text-xs text-gray-500 truncate max-w-80">{row.account_description}</div>
                      </td>
                      <td className="px-4 py-2 whitespace-nowrap">
                        <span className={`inline-flex px-2 py-1 rounded-full border text-xs font-medium ${statusClass(row.status)}`}>
                          {STATUS_LABELS[row.status] || row.status}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-right font-mono text-xs">{formatAmount(row.light_balance)}</td>
                      <td className="px-4 py-2 text-right font-mono text-xs">{formatAmount(row.trial_balance)}</td>
                      <td className="px-4 py-2 text-right font-mono text-xs">{formatAmount(row.variance)}</td>
                      <td className="px-4 py-2 text-right text-gray-500">{formatCount(row.light_lines)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filteredDetails.length === 0 && (
                <div className="py-8 text-center text-sm text-gray-400">No rows for this filter.</div>
              )}
            </div>
          </section>

          <section className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-100">
              <h2 className="text-sm font-semibold text-gray-900">Source mapping</h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Trial balance file</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Entity</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Balance column</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Accounts</th>
                    <th className="text-right px-4 py-2 font-medium text-gray-600">Total</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {result.trial_balance_mappings.map((row) => (
                    <tr key={row.source_file} className="hover:bg-gray-50">
                      <td className="px-4 py-2 text-gray-700">{row.source_file}</td>
                      <td className="px-4 py-2 text-gray-700 whitespace-nowrap">{row.entity}</td>
                      <td className="px-4 py-2 text-gray-500">{row.balance_column}</td>
                      <td className="px-4 py-2 text-right">{formatCount(row.accounts)}</td>
                      <td className="px-4 py-2 text-right font-mono text-xs">{formatAmount(row.total_balance)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
