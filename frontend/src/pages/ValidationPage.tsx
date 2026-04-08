import { useEffect, useState } from "react";
import { getRules, updateRule, createRule, deleteRule, generateRule } from "../api/client";
import { Shield, XCircle, AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Sparkles, Loader2, Trash2, Plus } from "lucide-react";

const CHECK_TYPE_LABELS: Record<string, string> = {
  required_columns: "Structure check",
  non_empty: "Required field check",
  at_least_one_populated: "Amount check",
  parseable_date: "Date format check",
  value_in_set: "Allowed values check",
  group_consistency: "Consistency check",
  group_balance: "Balance check",
};

const PHASE_LABELS: Record<string, string> = {
  structural: "Runs first",
  line: "Per line",
  group: "Per entry",
};

export default function ValidationPage() {
  const [rules, setRules] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedRule, setExpandedRule] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // AI rule creator state
  const [newRuleText, setNewRuleText] = useState("");
  const [generating, setGenerating] = useState(false);
  const [proposedRule, setProposedRule] = useState<any>(null);
  const [explanation, setExplanation] = useState("");
  const [saving, setSaving] = useState(false);

  const loadRules = async () => {
    setLoading(true);
    try {
      const data = await getRules("journal_entry");
      setRules(data.rules || []);
    } catch (e: any) {
      setError(e.message || "Failed to load rules");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRules();
  }, []);

  const handleToggle = async (ruleId: string, currentEnabled: boolean) => {
    try {
      await updateRule("journal_entry", ruleId, { enabled: !currentEnabled });
      setRules((prev) =>
        prev.map((r) => (r.id === ruleId ? { ...r, enabled: !currentEnabled } : r))
      );
    } catch (e: any) {
      setError(e.message || "Failed to update rule");
    }
  };

  const handleSeverityChange = async (ruleId: string, severity: string) => {
    try {
      await updateRule("journal_entry", ruleId, { severity });
      setRules((prev) =>
        prev.map((r) => (r.id === ruleId ? { ...r, severity } : r))
      );
    } catch (e: any) {
      setError(e.message || "Failed to update severity");
    }
  };

  const handleDelete = async (ruleId: string) => {
    try {
      await deleteRule("journal_entry", ruleId);
      setRules((prev) => prev.filter((r) => r.id !== ruleId));
    } catch (e: any) {
      setError(e.message || "Failed to delete rule");
    }
  };

  const handleGenerate = async () => {
    if (!newRuleText.trim()) return;
    setGenerating(true);
    setError(null);
    setProposedRule(null);
    try {
      const result = await generateRule("journal_entry", newRuleText.trim());
      setProposedRule(result.rule);
      setExplanation(result.explanation || "");
    } catch (e: any) {
      setError(e.message || "Failed to generate rule");
    } finally {
      setGenerating(false);
    }
  };

  const handleConfirmRule = async () => {
    if (!proposedRule) return;
    setSaving(true);
    try {
      await createRule("journal_entry", proposedRule);
      setRules((prev) => [...prev, { ...proposedRule }]);
      setProposedRule(null);
      setExplanation("");
      setNewRuleText("");
    } catch (e: any) {
      setError(e.message || "Failed to save rule");
    } finally {
      setSaving(false);
    }
  };

  const enabledCount = rules.filter((r) => r.enabled).length;
  const errorRules = rules.filter((r) => r.severity === "error" && r.enabled).length;
  const warningRules = rules.filter((r) => r.severity === "warning" && r.enabled).length;

  return (
    <div className="max-w-3xl">
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-2xl font-bold text-gray-900">Validation Rules</h1>
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <span className="px-2 py-0.5 bg-green-50 text-green-700 rounded-full">{enabledCount} active</span>
          <span className="px-2 py-0.5 bg-red-50 text-red-700 rounded-full">{errorRules} errors</span>
          <span className="px-2 py-0.5 bg-amber-50 text-amber-700 rounded-full">{warningRules} warnings</span>
        </div>
      </div>
      <p className="text-gray-500 mb-6 text-sm">
        These rules run automatically when you validate a journal entry file. Toggle rules on/off or add new ones using plain language.
      </p>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm flex justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600 font-bold">&times;</button>
        </div>
      )}

      {/* Rules list */}
      {loading ? (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
          <Loader2 className="w-6 h-6 text-sunshine-400 animate-spin mx-auto mb-2" />
          <p className="text-sm text-gray-400">Loading rules...</p>
        </div>
      ) : (
        <div className="space-y-2 mb-8">
          {rules.map((rule) => {
            const isExpanded = expandedRule === rule.id;
            const SevIcon = rule.severity === "error" ? XCircle : AlertTriangle;
            const sevColor = rule.severity === "error" ? "text-red-500" : "text-amber-500";

            return (
              <div
                key={rule.id}
                className={`bg-white rounded-xl border transition-all ${
                  rule.enabled ? "border-gray-200" : "border-gray-100 opacity-60"
                }`}
              >
                <div className="flex items-center gap-3 px-5 py-4">
                  {/* Toggle */}
                  <button
                    onClick={() => handleToggle(rule.id, rule.enabled)}
                    className={`relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ${
                      rule.enabled ? "bg-sunshine-400" : "bg-gray-200"
                    }`}
                  >
                    <div
                      className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
                        rule.enabled ? "translate-x-5" : "translate-x-0.5"
                      }`}
                    />
                  </button>

                  {/* Severity icon */}
                  <SevIcon className={`w-4 h-4 flex-shrink-0 ${sevColor}`} />

                  {/* Description */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-gray-800">{rule.description}</p>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-xs text-gray-400">{CHECK_TYPE_LABELS[rule.check_type] || rule.check_type}</span>
                      <span className="text-xs text-gray-300">|</span>
                      <span className="text-xs text-gray-400">{PHASE_LABELS[rule.phase] || rule.phase}</span>
                    </div>
                  </div>

                  {/* Issue code badge */}
                  <span className="text-xs font-mono text-gray-300 flex-shrink-0">{rule.issue_code}</span>

                  {/* Expand */}
                  <button
                    onClick={() => setExpandedRule(isExpanded ? null : rule.id)}
                    className="text-gray-300 hover:text-gray-500"
                  >
                    {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                  </button>
                </div>

                {/* Expanded details */}
                {isExpanded && (
                  <div className="px-5 pb-4 border-t border-gray-100 pt-3">
                    <div className="grid grid-cols-2 gap-3 text-xs mb-3">
                      <div>
                        <span className="text-gray-400">Severity:</span>
                        <select
                          value={rule.severity}
                          onChange={(e) => handleSeverityChange(rule.id, e.target.value)}
                          className="ml-2 px-2 py-0.5 border border-gray-200 rounded text-xs"
                        >
                          <option value="error">Error (blocks export)</option>
                          <option value="warning">Warning (informational)</option>
                        </select>
                      </div>
                      <div>
                        <span className="text-gray-400">Scope:</span>
                        <span className="ml-2 text-gray-600">{rule.scope}</span>
                      </div>
                    </div>
                    <div className="text-xs text-gray-400 mb-3">
                      <span>Parameters:</span>
                      <pre className="mt-1 p-2 bg-gray-50 rounded text-gray-600 overflow-x-auto">
                        {JSON.stringify(rule.params, null, 2)}
                      </pre>
                    </div>
                    <div className="flex justify-end">
                      <button
                        onClick={() => handleDelete(rule.id)}
                        className="flex items-center gap-1 text-xs text-red-400 hover:text-red-600"
                      >
                        <Trash2 className="w-3 h-3" /> Remove rule
                      </button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* AI Rule Creator */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-sunshine-500" />
          <h3 className="text-sm font-semibold text-gray-900">Add a new rule</h3>
        </div>
        <div className="p-5">
          <p className="text-xs text-gray-400 mb-3">
            Describe what you want to check in plain language. The AI will create the technical rule for you.
          </p>
          <div className="flex gap-2">
            <input
              type="text"
              value={newRuleText}
              onChange={(e) => setNewRuleText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !generating) handleGenerate(); }}
              placeholder="e.g., Check that no entry has more than 50 lines"
              className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-sm placeholder-gray-300 focus:outline-none focus:border-sunshine-400"
              disabled={generating}
            />
            <button
              onClick={handleGenerate}
              disabled={generating || !newRuleText.trim()}
              className="px-4 py-2 bg-sunshine-500 text-white text-sm rounded-lg hover:bg-sunshine-600 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 transition-colors"
            >
              {generating ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Thinking...</>
              ) : (
                <><Plus className="w-4 h-4" /> Generate</>
              )}
            </button>
          </div>

          {/* Proposed rule preview */}
          {proposedRule && (
            <div className="mt-4 border border-sunshine-200 bg-sunshine-50 rounded-lg p-4">
              <div className="flex items-start gap-2 mb-3">
                <CheckCircle2 className="w-4 h-4 text-sunshine-600 mt-0.5 flex-shrink-0" />
                <div>
                  <p className="text-sm font-medium text-gray-800">{proposedRule.description}</p>
                  {explanation && <p className="text-xs text-gray-500 mt-1">{explanation}</p>}
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 text-xs mb-3">
                <div className="bg-white rounded px-2 py-1">
                  <span className="text-gray-400">Type: </span>
                  <span className="text-gray-700">{CHECK_TYPE_LABELS[proposedRule.check_type] || proposedRule.check_type}</span>
                </div>
                <div className="bg-white rounded px-2 py-1">
                  <span className="text-gray-400">Severity: </span>
                  <span className={proposedRule.severity === "error" ? "text-red-600" : "text-amber-600"}>{proposedRule.severity}</span>
                </div>
                <div className="bg-white rounded px-2 py-1">
                  <span className="text-gray-400">Code: </span>
                  <span className="text-gray-700 font-mono">{proposedRule.issue_code}</span>
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => { setProposedRule(null); setExplanation(""); }}
                  className="px-3 py-1.5 text-xs text-gray-500 border border-gray-300 rounded-lg hover:bg-white"
                >
                  Cancel
                </button>
                <button
                  onClick={handleConfirmRule}
                  disabled={saving}
                  className="px-3 py-1.5 text-xs font-medium bg-sunshine-500 text-white rounded-lg hover:bg-sunshine-600 disabled:opacity-50 flex items-center gap-1"
                >
                  {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
                  Add this rule
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
