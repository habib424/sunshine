import { useState, useEffect } from "react";
import { getIntents, analyzeWithIntent } from "../../api/client";
import { Target, FileSearch, GitCompare, CheckCircle2, AlertTriangle, XCircle, Loader2, ChevronDown, ChevronUp } from "lucide-react";

interface Props {
  uploadId: string;
  filename: string;
  onProceed: () => void;
}

const INTENT_ICONS: Record<string, typeof Target> = {
  convert_to_light_je: Target,
  validate_je: FileSearch,
  reconcile_je_to_gl: GitCompare,
};

const SEVERITY_CONFIG = {
  error: { icon: XCircle, color: "text-red-600", bg: "bg-red-50", border: "border-red-200" },
  warning: { icon: AlertTriangle, color: "text-amber-600", bg: "bg-amber-50", border: "border-amber-200" },
};

export default function IntentAnalysis({ uploadId, filename, onProceed }: Props) {
  const [intents, setIntents] = useState<any[]>([]);
  const [selectedIntent, setSelectedIntent] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);

  useEffect(() => {
    getIntents().then(setIntents).catch(() => {});
  }, []);

  const handleReset = () => {
    setSelectedIntent(null);
    setResult(null);
    setError(null);
    setExpandedCode(null);
  };

  const handleAnalyze = async (intent: string) => {
    setSelectedIntent(intent);
    setAnalyzing(true);
    setResult(null);
    setError(null);
    try {
      const res = await analyzeWithIntent(uploadId, intent);
      setResult(res);
    } catch (e: any) {
      setError(e.message || "Analysis failed");
    } finally {
      setAnalyzing(false);
    }
  };

  const issueSummary = result?.issue_summary ? Object.values(result.issue_summary) as any[] : [];
  const errorCount = issueSummary.filter((s: any) => s.severity === "error").reduce((a: number, s: any) => a + s.count, 0);
  const warningCount = issueSummary.filter((s: any) => s.severity === "warning").reduce((a: number, s: any) => a + s.count, 0);

  return (
    <div className="space-y-4">
      {/* Intent Selection */}
      <div className="bg-white rounded-xl border border-gray-200 p-6">
        <h3 className="text-sm font-semibold text-gray-900 mb-1">What do you want to do with this file?</h3>
        <p className="text-xs text-gray-400 mb-4">
          Working on: <span className="font-medium text-gray-600">{filename}</span>
        </p>

        <div className="grid gap-3">
          {intents.map((intent) => {
            const Icon = INTENT_ICONS[intent.name] || Target;
            const isSelected = selectedIntent === intent.name;
            return (
              <button
                key={intent.name}
                onClick={() => handleAnalyze(intent.name)}
                disabled={analyzing}
                className={`flex items-start gap-3 p-4 rounded-lg border text-left transition-all
                  ${isSelected
                    ? "border-sunshine-400 bg-sunshine-50 ring-1 ring-sunshine-300"
                    : "border-gray-200 hover:border-sunshine-300 hover:bg-gray-50"
                  }
                  ${analyzing ? "opacity-60 cursor-wait" : "cursor-pointer"}
                `}
              >
                <Icon className={`w-5 h-5 mt-0.5 flex-shrink-0 ${isSelected ? "text-sunshine-600" : "text-gray-400"}`} />
                <div>
                  <div className={`text-sm font-medium ${isSelected ? "text-sunshine-800" : "text-gray-800"}`}>
                    {intent.label}
                  </div>
                  <div className="text-xs text-gray-400 mt-0.5">{intent.description}</div>
                </div>
                {isSelected && analyzing && (
                  <Loader2 className="w-4 h-4 ml-auto animate-spin text-sunshine-500 flex-shrink-0" />
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* Needs confirmation / help */}
      {result && !analyzing && (result.status === "needs_confirmation" || result.status === "needs_help") && (
        <div className="bg-white rounded-xl border border-amber-200 p-6">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" />
            <div>
              <h3 className="text-sm font-semibold text-gray-900 mb-1">
                {result.status === "needs_help"
                  ? "Could not detect the file structure"
                  : "Please confirm the detected structure"}
              </h3>
              <p className="text-xs text-gray-500 mb-3">
                Detected sheet: <span className="font-medium">{result.layout?.sheet || "unknown"}</span>,
                header row: <span className="font-medium">{result.layout?.header_row ?? "unknown"}</span>,
                confidence: <span className="font-medium">{result.confidence ? Math.round(result.confidence * 100) + "%" : "low"}</span>
              </p>
              {result.unresolved?.length > 0 && (
                <ul className="space-y-1 mb-3">
                  {result.unresolved.map((msg: string, i: number) => (
                    <li key={i} className="text-xs text-amber-700 bg-amber-50 px-2 py-1 rounded">{msg}</li>
                  ))}
                </ul>
              )}
              {result.layout?.column_roles && Object.keys(result.layout.column_roles).length > 0 && (
                <div className="text-xs text-gray-500 mb-3">
                  Mapped columns: {Object.entries(result.layout.column_roles).map(([src, role]) => (
                    <span key={src} className="inline-block bg-green-50 text-green-700 px-1.5 py-0.5 rounded mr-1 mb-1">{src} &rarr; {role as string}</span>
                  ))}
                </div>
              )}
              {result.layout?.missing_required?.length > 0 && (
                <p className="text-xs text-amber-600 mt-2">
                  Missing columns are reported as validation findings below.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Analysis Results — show whenever we have rows (any status with data) */}
      {result && !analyzing && result.rows > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          {/* Header with summary */}
          <div className="p-6 border-b border-gray-100">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-gray-900">Analysis Results</h3>
              <div className="flex items-center gap-3 text-xs">
                {result.confidence !== undefined && (
                  <span className={`px-2 py-0.5 rounded-full font-medium
                    ${result.confidence >= 0.85 ? "bg-green-100 text-green-700" : result.confidence >= 0.5 ? "bg-amber-100 text-amber-700" : "bg-red-100 text-red-700"}`}>
                    {Math.round(result.confidence * 100)}% confidence
                  </span>
                )}
                <span className="text-gray-400">{result.rows} rows</span>
              </div>
            </div>

            {/* Quick stats */}
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-gray-50 rounded-lg p-3 text-center">
                <div className="text-lg font-bold text-gray-900">{result.rows || 0}</div>
                <div className="text-xs text-gray-400">Rows parsed</div>
              </div>
              <div className={`rounded-lg p-3 text-center ${errorCount > 0 ? "bg-red-50" : "bg-green-50"}`}>
                <div className={`text-lg font-bold ${errorCount > 0 ? "text-red-700" : "text-green-700"}`}>{errorCount}</div>
                <div className={`text-xs ${errorCount > 0 ? "text-red-400" : "text-green-400"}`}>Errors</div>
              </div>
              <div className={`rounded-lg p-3 text-center ${warningCount > 0 ? "bg-amber-50" : "bg-green-50"}`}>
                <div className={`text-lg font-bold ${warningCount > 0 ? "text-amber-700" : "text-green-700"}`}>{warningCount}</div>
                <div className={`text-xs ${warningCount > 0 ? "text-amber-400" : "text-green-400"}`}>Warnings</div>
              </div>
            </div>
          </div>

          {/* Issue list */}
          {issueSummary.length > 0 && (
            <div className="divide-y divide-gray-100">
              {issueSummary.map((s: any) => {
                const sev = SEVERITY_CONFIG[s.severity as keyof typeof SEVERITY_CONFIG] || SEVERITY_CONFIG.warning;
                const SevIcon = sev.icon;
                const isExpanded = expandedCode === s.code;
                const relatedIssues = (result.validation_issues || []).filter((i: any) => i.issue_code === s.code);
                return (
                  <div key={s.code}>
                    <button
                      onClick={() => setExpandedCode(isExpanded ? null : s.code)}
                      className="w-full flex items-center gap-3 px-6 py-3 hover:bg-gray-50 transition-colors text-left"
                    >
                      <SevIcon className={`w-4 h-4 flex-shrink-0 ${sev.color}`} />
                      <div className="flex-1 min-w-0">
                        <span className="text-xs font-mono text-gray-400">{s.code}</span>
                        <span className="mx-2 text-gray-300">|</span>
                        <span className="text-sm text-gray-700">{s.count} occurrence{s.count > 1 ? "s" : ""}</span>
                      </div>
                      {relatedIssues.length > 0 && (
                        isExpanded
                          ? <ChevronUp className="w-4 h-4 text-gray-300" />
                          : <ChevronDown className="w-4 h-4 text-gray-300" />
                      )}
                    </button>
                    {isExpanded && relatedIssues.length > 0 && (
                      <div className={`px-6 pb-3 ${sev.bg} border-l-2 ${sev.border} ml-6 mr-6 mb-2 rounded`}>
                        <div className="py-2 space-y-1 max-h-48 overflow-y-auto">
                          {relatedIssues.slice(0, 20).map((issue: any, idx: number) => (
                            <div key={idx} className="text-xs text-gray-600 font-mono">
                              {issue.entry_id && <span className="text-gray-400">entry {issue.entry_id}: </span>}
                              {issue.row_number && <span className="text-gray-400">row {issue.row_number}: </span>}
                              {issue.message}
                            </div>
                          ))}
                          {relatedIssues.length > 20 && (
                            <div className="text-xs text-gray-400 italic">...and {relatedIssues.length - 20} more</div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Clean file */}
          {issueSummary.length === 0 && (
            <div className="p-6 text-center">
              <CheckCircle2 className="w-8 h-8 text-green-500 mx-auto mb-2" />
              <p className="text-sm font-medium text-green-700">File passes all contract checks</p>
              <p className="text-xs text-gray-400 mt-1">No issues found — ready for transformation</p>
            </div>
          )}

          {/* Layout info (collapsed) */}
          {result.layout && (
            <div className="px-6 py-3 bg-gray-50 border-t border-gray-100">
              <div className="flex items-center gap-4 text-xs text-gray-400">
                <span>Sheet: <span className="text-gray-600 font-medium">{result.layout.sheet}</span></span>
                <span>Header row: <span className="text-gray-600 font-medium">{result.layout.header_row}</span></span>
                <span>Fingerprint: <span className="text-gray-600 font-mono">{result.fingerprint?.slice(0, 16)}</span></span>
                <span>Source: <span className="text-gray-600">{result.source}</span></span>
              </div>
            </div>
          )}

          {/* Action buttons */}
          <div className="px-6 py-4 bg-white border-t border-gray-100 flex items-center gap-3 justify-between">
            <div className="text-xs text-gray-400">
              {errorCount > 0 && (
                <span className="text-red-500">{errorCount} error{errorCount > 1 ? "s" : ""} found</span>
              )}
              {errorCount > 0 && warningCount > 0 && <span className="mx-1">|</span>}
              {warningCount > 0 && (
                <span className="text-amber-500">{warningCount} warning{warningCount > 1 ? "s" : ""}</span>
              )}
              {errorCount === 0 && warningCount === 0 && (
                <span className="text-green-500">No issues found</span>
              )}
            </div>
            <div className="flex gap-3">
              {selectedIntent === "validate_je" ? (
                <button
                  onClick={handleReset}
                  className="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50"
                >
                  Validate another file
                </button>
              ) : (
                <button
                  onClick={onProceed}
                  className="px-5 py-2 text-sm font-medium bg-sunshine-500 text-white rounded-lg hover:bg-sunshine-600 transition-colors"
                >
                  Proceed to transformation
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
