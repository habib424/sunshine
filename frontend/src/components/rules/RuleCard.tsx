import { useState } from "react";
import type { TransformRule } from "../../types";

const RULE_ICONS: Record<string, string> = {
  source_mapping: "📄",
  unpivot_entities: "🔀",
  currency_lookup: "💱",
  debit_credit_split: "➗",
  filter_rows: "🔍",
  set_constant: "📌",
  generate_id: "🏷️",
  map_columns: "🗂️",
  aggregate: "📊",
};

interface RuleCardProps {
  rule: TransformRule;
  index: number;
  onToggle: (id: string) => void;
  onUpdateConfig: (id: string, config: Record<string, unknown>) => void;
  onDelete: (id: string) => void;
  onMoveUp: (id: string) => void;
  onMoveDown: (id: string) => void;
  isFirst: boolean;
  isLast: boolean;
}

export default function RuleCard({
  rule, index, onToggle, onUpdateConfig, onDelete, onMoveUp, onMoveDown, isFirst, isLast,
}: RuleCardProps) {
  const [expanded, setExpanded] = useState(false);
  const icon = RULE_ICONS[rule.type] || "⚙️";

  return (
    <div className={`border rounded-lg transition-all ${
      rule.enabled ? "border-gray-200 bg-white" : "border-gray-100 bg-gray-50 opacity-60"
    }`}>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2.5">
        {/* Reorder buttons */}
        <div className="flex flex-col gap-0.5">
          <button
            onClick={() => onMoveUp(rule.id)}
            disabled={isFirst}
            className="text-gray-300 hover:text-gray-500 disabled:opacity-30 text-xs leading-none"
          >▲</button>
          <button
            onClick={() => onMoveDown(rule.id)}
            disabled={isLast}
            className="text-gray-300 hover:text-gray-500 disabled:opacity-30 text-xs leading-none"
          >▼</button>
        </div>

        {/* Toggle */}
        <button
          onClick={() => onToggle(rule.id)}
          className={`w-9 h-5 rounded-full transition-colors flex-shrink-0 relative ${
            rule.enabled ? "bg-sunshine-500" : "bg-gray-300"
          }`}
        >
          <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
            rule.enabled ? "left-[18px]" : "left-0.5"
          }`} />
        </button>

        {/* Icon + Label */}
        <span className="text-base">{icon}</span>
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-medium truncate ${rule.enabled ? "text-gray-900" : "text-gray-400"}`}>
            {rule.label}
          </p>
        </div>

        {/* Badges */}
        {rule.ai_suggested && (
          <span className="px-1.5 py-0.5 bg-purple-50 text-purple-600 rounded text-[10px] font-medium flex-shrink-0">
            AI
          </span>
        )}
        <span className="px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded text-[10px] font-mono flex-shrink-0">
          {rule.type}
        </span>

        {/* Expand / Delete */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-gray-400 hover:text-gray-600 text-sm flex-shrink-0"
        >
          {expanded ? "▾" : "▸"}
        </button>
        <button
          onClick={() => onDelete(rule.id)}
          className="text-gray-300 hover:text-red-400 text-sm flex-shrink-0"
        >✕</button>
      </div>

      {/* Description */}
      {!expanded && (
        <p className="px-3 pb-2 text-xs text-gray-400 truncate pl-[68px]">
          {rule.description}
        </p>
      )}

      {/* Expanded: config editor */}
      {expanded && (
        <div className="border-t border-gray-100 px-3 py-3 space-y-2">
          <p className="text-xs text-gray-500">{rule.description}</p>
          <ConfigEditor rule={rule} onUpdate={(newConfig) => onUpdateConfig(rule.id, newConfig)} />
        </div>
      )}
    </div>
  );
}

function ConfigEditor({ rule, onUpdate }: { rule: TransformRule; onUpdate: (config: Record<string, unknown>) => void }) {
  const config = rule.config;

  // Special renderers per rule type
  if (rule.type === "filter_rows") {
    return <FilterConfig config={config} onUpdate={onUpdate} />;
  }
  if (rule.type === "set_constant") {
    return <SetConstantConfig config={config} onUpdate={onUpdate} />;
  }
  if (rule.type === "generate_id") {
    return <GenerateIdConfig config={config} onUpdate={onUpdate} />;
  }

  // Generic JSON editor fallback
  return (
    <div>
      <textarea
        className="w-full text-xs font-mono bg-gray-50 border border-gray-200 rounded p-2 focus:outline-none focus:ring-1 focus:ring-sunshine-300"
        rows={6}
        defaultValue={JSON.stringify(config, null, 2)}
        onBlur={(e) => {
          try { onUpdate(JSON.parse(e.target.value)); } catch {}
        }}
      />
    </div>
  );
}

function FilterConfig({ config, onUpdate }: { config: Record<string, unknown>; onUpdate: (c: Record<string, unknown>) => void }) {
  const conditions = (config.conditions as any[]) || [];
  const action = (config.action as string) || "remove";

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-gray-500">Action:</span>
        <select
          value={action}
          onChange={(e) => onUpdate({ ...config, action: e.target.value })}
          className="border border-gray-200 rounded px-2 py-1 text-xs"
        >
          <option value="remove">Remove matching rows</option>
          <option value="keep">Keep only matching rows</option>
        </select>
      </div>
      {conditions.map((cond: any, i: number) => (
        <div key={i} className="flex items-center gap-2 text-xs bg-gray-50 rounded p-2">
          <span className="text-gray-500 font-mono">{cond.column}</span>
          <span className="text-gray-400">{cond.operator}</span>
          {cond.value !== undefined && <span className="text-gray-600 font-mono">{String(cond.value)}</span>}
        </div>
      ))}
    </div>
  );
}

function SetConstantConfig({ config, onUpdate }: { config: Record<string, unknown>; onUpdate: (c: Record<string, unknown>) => void }) {
  const assignments = (config.assignments as Record<string, any>) || {};
  const editableKeys = Object.entries(assignments).filter(([, v]) => v !== null);

  return (
    <div className="space-y-1">
      {editableKeys.map(([key, value]) => (
        <div key={key} className="flex items-center gap-2 text-xs">
          <span className="text-gray-500 font-mono w-48 truncate flex-shrink-0">{key}</span>
          <input
            type="text"
            defaultValue={String(value)}
            onBlur={(e) => {
              const newAssignments = { ...assignments, [key]: e.target.value === "0" ? 0 : e.target.value };
              onUpdate({ ...config, assignments: newAssignments });
            }}
            className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs"
          />
        </div>
      ))}
    </div>
  );
}

function GenerateIdConfig({ config, onUpdate }: { config: Record<string, unknown>; onUpdate: (c: Record<string, unknown>) => void }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-gray-500">Pattern:</span>
        <input
          type="text"
          defaultValue={String(config.pattern || "")}
          onBlur={(e) => onUpdate({ ...config, pattern: e.target.value })}
          className="flex-1 border border-gray-200 rounded px-2 py-1 text-xs font-mono"
        />
      </div>
      <p className="text-[10px] text-gray-400">Use {"{column_name}"} for dynamic values</p>
    </div>
  );
}
