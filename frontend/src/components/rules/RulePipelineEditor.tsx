import { useCallback, useEffect, useRef, useState } from "react";
import type { TransformRule, PreviewResponse } from "../../types";
import { previewRules } from "../../api/client";
import RuleCard from "./RuleCard";
import PreviewPanel from "./PreviewPanel";

interface RulePipelineEditorProps {
  uploadId: string;
  initialRules: TransformRule[];
  summary: string;
  entitiesFound: string[];
  confidence: number;
  onRulesChange: (rules: TransformRule[]) => void;
}

const ADD_RULE_OPTIONS = [
  { type: "filter_rows", label: "Filter rows", description: "Remove or keep rows by condition" },
  { type: "set_constant", label: "Set constant values", description: "Set columns to fixed values" },
  { type: "generate_id", label: "Generate IDs", description: "Create IDs from a pattern" },
  { type: "aggregate", label: "Aggregate rows", description: "Group and sum rows by key" },
  { type: "debit_credit_split", label: "Split debit/credit", description: "Split value into debit and credit" },
  { type: "map_columns", label: "Map columns", description: "Rename and reorder columns" },
];

export default function RulePipelineEditor({
  uploadId, initialRules, summary, entitiesFound, confidence, onRulesChange,
}: RulePipelineEditorProps) {
  const [rules, setRules] = useState<TransformRule[]>(initialRules);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [showAddMenu, setShowAddMenu] = useState(false);
  const debounceRef = useRef<number>(0);

  // Fetch preview whenever rules change
  const fetchPreview = useCallback(async (currentRules: TransformRule[]) => {
    setIsLoadingPreview(true);
    try {
      const result = await previewRules(uploadId, currentRules, 15);
      setPreview(result);
    } catch {
      setPreview({ headers: [], rows: [], total_rows: 0, errors: ["Preview failed"] });
    }
    setIsLoadingPreview(false);
  }, [uploadId]);

  useEffect(() => {
    clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      fetchPreview(rules);
      onRulesChange(rules);
    }, 400);
    return () => clearTimeout(debounceRef.current);
  }, [rules, fetchPreview, onRulesChange]);

  // Initial preview
  useEffect(() => {
    fetchPreview(initialRules);
  }, []);

  const updateRules = (newRules: TransformRule[]) => setRules([...newRules]);

  const handleToggle = (id: string) => {
    updateRules(rules.map(r => r.id === id ? { ...r, enabled: !r.enabled } : r));
  };

  const handleUpdateConfig = (id: string, config: Record<string, unknown>) => {
    updateRules(rules.map(r => r.id === id ? { ...r, config } : r));
  };

  const handleDelete = (id: string) => {
    updateRules(rules.filter(r => r.id !== id));
  };

  const handleMoveUp = (id: string) => {
    const idx = rules.findIndex(r => r.id === id);
    if (idx <= 0) return;
    const next = [...rules];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    updateRules(next);
  };

  const handleMoveDown = (id: string) => {
    const idx = rules.findIndex(r => r.id === id);
    if (idx >= rules.length - 1) return;
    const next = [...rules];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    updateRules(next);
  };

  const handleAddRule = (type: string) => {
    const option = ADD_RULE_OPTIONS.find(o => o.type === type);
    const newRule: TransformRule = {
      id: `r_${Date.now().toString(36)}`,
      type,
      label: option?.label || type,
      description: option?.description || "",
      enabled: true,
      ai_suggested: false,
      config: type === "filter_rows" ? {
        conditions: [{ column: "", operator: "is_zero" }],
        logic: "and",
        action: "remove",
      } : type === "set_constant" ? {
        assignments: { "new_column": "" },
      } : {},
    };
    // Insert before map_columns (last rule) if it exists
    const mapIdx = rules.findIndex(r => r.type === "map_columns");
    if (mapIdx >= 0) {
      const next = [...rules];
      next.splice(mapIdx, 0, newRule);
      updateRules(next);
    } else {
      updateRules([...rules, newRule]);
    }
    setShowAddMenu(false);
  };

  const enabledCount = rules.filter(r => r.enabled).length;

  return (
    <div className="flex gap-4 h-[calc(100vh-280px)] min-h-[500px]">
      {/* LEFT: Rule list */}
      <div className="w-[55%] flex flex-col">
        {/* Summary header */}
        <div className="bg-white rounded-xl border border-gray-200 p-4 mb-3">
          <div className="flex items-start justify-between mb-2">
            <p className="text-sm text-gray-700 flex-1">{summary}</p>
            <span className={`ml-2 px-2 py-0.5 rounded-full text-xs font-medium flex-shrink-0 ${
              confidence >= 0.8 ? "bg-green-100 text-green-700" : "bg-yellow-100 text-yellow-700"
            }`}>
              {Math.round(confidence * 100)}%
            </span>
          </div>
          {entitiesFound.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {entitiesFound.slice(0, 6).map(e => (
                <span key={e} className="px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded text-[10px]">{e}</span>
              ))}
              {entitiesFound.length > 6 && (
                <span className="px-1.5 py-0.5 bg-gray-100 text-gray-400 rounded text-[10px]">
                  +{entitiesFound.length - 6} more
                </span>
              )}
            </div>
          )}
        </div>

        {/* Rules label */}
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
            Pipeline ({enabledCount}/{rules.length} active)
          </h3>
          <div className="relative">
            <button
              onClick={() => setShowAddMenu(!showAddMenu)}
              className="px-2.5 py-1 bg-sunshine-50 text-sunshine-600 rounded-lg text-xs font-medium hover:bg-sunshine-100 transition-colors"
            >
              + Add rule
            </button>
            {showAddMenu && (
              <div className="absolute right-0 top-8 z-10 bg-white border border-gray-200 rounded-lg shadow-lg w-56 py-1">
                {ADD_RULE_OPTIONS.map(opt => (
                  <button
                    key={opt.type}
                    onClick={() => handleAddRule(opt.type)}
                    className="w-full text-left px-3 py-2 hover:bg-gray-50 text-xs"
                  >
                    <span className="font-medium text-gray-700">{opt.label}</span>
                    <br />
                    <span className="text-gray-400">{opt.description}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Rule cards */}
        <div className="flex-1 overflow-y-auto space-y-2 pr-1">
          {rules.map((rule, i) => (
            <RuleCard
              key={rule.id}
              rule={rule}
              index={i}
              onToggle={handleToggle}
              onUpdateConfig={handleUpdateConfig}
              onDelete={handleDelete}
              onMoveUp={handleMoveUp}
              onMoveDown={handleMoveDown}
              isFirst={i === 0}
              isLast={i === rules.length - 1}
            />
          ))}
        </div>
      </div>

      {/* RIGHT: Preview */}
      <div className="w-[45%] bg-white rounded-xl border border-gray-200 overflow-hidden">
        <PreviewPanel preview={preview} isLoading={isLoadingPreview} />
      </div>
    </div>
  );
}
