import { useState, useRef, useEffect } from "react";
import { sendChatMessage, executeChat, getExportUrl } from "../../api/client";

interface Message {
  role: "user" | "assistant";
  content: string;
  has_script?: boolean;
}

interface TransformChatProps {
  sessionId: string;
  initialMessage: string;
  initialHasScript: boolean;
  uploadedName: string;
  intent?: string;
  onExecuted: (jobId: string) => void;
}

export default function TransformChat({
  sessionId, initialMessage, initialHasScript, uploadedName, intent, onExecuted,
}: TransformChatProps) {
  const isReconcile = intent === "reconcile_je_to_gl";
  const isDeferral = intent === "migrate_deferred_cost_to_light_je" || intent === "migrate_deferred_revenue_to_light_je";
  const isOpenAp = intent === "upload_open_ap_to_light_ap";
  const isFx = intent === "fx_currency_adjustment";
  const [messages, setMessages] = useState<Message[]>([
    { role: "assistant", content: initialMessage, has_script: initialHasScript },
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [hasScript, setHasScript] = useState(initialHasScript);
  const [isExecuting, setIsExecuting] = useState(false);
  const [executionResult, setExecutionResult] = useState<any>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || isLoading) return;

    setInput("");
    setMessages(prev => [...prev, { role: "user", content: text }]);
    setIsLoading(true);

    try {
      const result = await sendChatMessage(sessionId, text);
      setMessages(prev => [...prev, {
        role: "assistant",
        content: result.message,
        has_script: result.has_script,
      }]);
      if (result.has_script) setHasScript(true);
    } catch (e: any) {
      setMessages(prev => [...prev, {
        role: "assistant",
        content: `Error: ${e.message}`,
      }]);
    }
    setIsLoading(false);
    inputRef.current?.focus();
  };

  const handleExecute = async () => {
    setIsExecuting(true);
    try {
      const suffix = isReconcile
        ? "_Reconciliation.xlsx"
        : isDeferral
        ? "_Light_JE_Upload.xlsx"
        : isOpenAp
        ? "_Light_AP_Upload.xlsx"
        : isFx
        ? "_FX_Adjustment.xlsx"
        : "_Light_Upload.xlsx";
      const outputName = uploadedName.replace(/\.[^.]+$/, "") + suffix;
      const result = await executeChat(sessionId, outputName);
      setExecutionResult(result);
      if (result.success) {
        onExecuted(result.job_id);
      }
    } catch (e: any) {
      setExecutionResult({ success: false, error: e.message });
    }
    setIsExecuting(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-240px)] min-h-[500px]">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-3 pb-4">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[85%] rounded-xl px-4 py-3 text-sm ${
              msg.role === "user"
                ? "bg-sunshine-500 text-white"
                : "bg-white border border-gray-200 text-gray-700"
            }`}>
              <MessageContent content={msg.content} isAssistant={msg.role === "assistant"} />
              {msg.has_script && msg.role === "assistant" && (
                <div className="mt-3 pt-3 border-t border-gray-100">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-green-600 font-medium">{isDeferral || isOpenAp || isFx ? "Deterministic plan ready" : "Script ready"}</span>
                    {!executionResult && (
                      <button
                        onClick={handleExecute}
                        disabled={isExecuting}
                        className="px-3 py-1 bg-sunshine-500 text-white text-xs rounded-lg hover:bg-sunshine-600 disabled:opacity-50 font-medium"
                      >
                        {isExecuting ? "Running..." : isReconcile ? "Run Reconciliation" : isOpenAp ? "Run AP Migration" : isFx ? "Run FX Adjustment" : "Run Migration"}
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="flex justify-start">
            <div className="bg-white border border-gray-200 rounded-xl px-4 py-3">
              <div className="flex items-center gap-2 text-gray-400 text-sm">
                <div className="w-4 h-4 border-2 border-sunshine-400 border-t-transparent rounded-full animate-spin" />
                Thinking...
              </div>
            </div>
          </div>
        )}

        {executionResult && (
          <div className={`mx-4 p-4 rounded-xl border ${
            executionResult.success
              ? "bg-green-50 border-green-200"
              : "bg-red-50 border-red-200"
          }`}>
            {executionResult.success ? (
              <div>
                <p className="text-green-700 font-medium text-sm">
                  &#9989; {isReconcile ? "Reconciliation" : isOpenAp ? "AP upload" : isFx ? "FX adjustment" : "Migration"} complete — {executionResult.rows} rows generated
                </p>
                {executionResult.preview && (
                  <div className="mt-3 overflow-x-auto">
                    <table className="text-[11px] w-full">
                      <thead>
                        <tr>
                          {executionResult.preview.headers.slice(0, 8).map((h: string, i: number) => (
                            <th key={i} className="px-2 py-1 text-left font-medium text-green-600 whitespace-nowrap">{h}</th>
                          ))}
                          {executionResult.preview.headers.length > 8 && (
                            <th className="px-2 py-1 text-green-400">+{executionResult.preview.headers.length - 8}</th>
                          )}
                        </tr>
                      </thead>
                      <tbody>
                        {executionResult.preview.rows.slice(0, 3).map((row: any[], ri: number) => (
                          <tr key={ri}>
                            {row.slice(0, 8).map((cell: any, ci: number) => (
                              <td key={ci} className="px-2 py-0.5 text-green-700 whitespace-nowrap truncate max-w-[150px]">
                                {cell === null || cell === "" ? "-" : String(cell)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ) : (
              <div>
                <p className="text-red-700 font-medium text-sm">Execution failed</p>
                <p className="text-red-600 text-xs mt-1">{executionResult.error}</p>
              </div>
            )}
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-gray-200 pt-3">
        <div className="flex gap-2">
          <textarea
            ref={inputRef as any}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={hasScript ? "Adjust the plan, or click Run Migration..." : "Tell Sunshine what to adjust..."}
            className="flex-1 px-4 py-2.5 border border-gray-300 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-sunshine-300 focus:border-sunshine-400 resize-none min-h-[42px] max-h-[160px]"
            rows={Math.min(input.split("\n").length, 6) || 1}
            disabled={isLoading}
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            className="px-4 py-2.5 bg-sunshine-500 text-white rounded-xl hover:bg-sunshine-600 disabled:opacity-50 text-sm font-medium transition-colors"
          >
            Send
          </button>
        </div>
        <div className="flex gap-2 mt-2">
          {(isReconcile ? [
            "Show debit/credit breakdown",
            "Exclude zero-balance accounts",
            "Looks good, run it",
          ] : isDeferral ? [
            "currency is EUR",
            "posting date 2026-03-01",
            "release template is 12 Months - Deferred Revenue",
          ] : isOpenAp ? [
            "entity is causaLens",
            "currency is GBP",
            "do not split vendor code",
          ] : isFx ? [
            "clearing account is 900300",
            "posting date 31-07-2026",
            "document year is 2025",
          ] : [
            "Remove zero-balance lines",
            "Change date to 2024-12-31",
            "Looks good, run it",
          ]).map(suggestion => (
            <button
              key={suggestion}
              onClick={() => { setInput(suggestion); }}
              className="px-2.5 py-1 bg-gray-50 border border-gray-200 rounded-lg text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700 transition-colors"
            >
              {suggestion}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function MessageContent({ content, isAssistant }: { content: string; isAssistant: boolean }) {
  if (!isAssistant) return <span className="whitespace-pre-wrap">{content}</span>;

  // Parse markdown-like formatting for assistant messages
  const parts = content.split(/(```python[\s\S]*?```)/);

  return (
    <div className="space-y-2">
      {parts.map((part, i) => {
        if (part.startsWith("```python")) {
          const code = part.replace(/```python\s*\n?/, "").replace(/\s*```$/, "");
          return (
            <details key={i} className="bg-gray-50 rounded-lg border border-gray-200">
              <summary className="px-3 py-2 cursor-pointer text-xs font-medium text-gray-600 hover:bg-gray-100 rounded-lg">
                View generated script
              </summary>
              <pre className="px-3 pb-3 text-[11px] text-gray-600 overflow-x-auto font-mono leading-relaxed">
                {code}
              </pre>
            </details>
          );
        }
        // Simple markdown: bold, numbered lists
        return (
          <div key={i} className="whitespace-pre-wrap leading-relaxed">
            {part.split("\n").map((line, j) => {
              const boldLine = line.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
              const isNumbered = /^\d+\.\s/.test(line);
              return (
                <div key={j} className={isNumbered ? "pl-2 py-0.5" : ""}>
                  <span dangerouslySetInnerHTML={{ __html: boldLine }} />
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
