import { useState, useEffect } from "react";
import DropZone from "../components/upload/DropZone";
import IntentAnalysis from "../components/upload/IntentAnalysis";
import TransformChat from "../components/chat/TransformChat";
import { uploadFiles, startChat, getExportUrl } from "../api/client";
import { useAppStore } from "../stores/appStore";

type Step = "upload" | "intent" | "starting" | "chat" | "done";

export default function UploadPage() {
  const { addUploads, setUploads } = useAppStore();
  const [step, setStep] = useState<Step>("upload");
  const [isUploading, setIsUploading] = useState(false);
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [uploadedName, setUploadedName] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [initialMessage, setInitialMessage] = useState("");
  const [initialHasScript, setInitialHasScript] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStep("upload");
    setError(null);
    setUploads([]);
  }, [setUploads]);

  const handleFilesSelected = async (files: File[]) => {
    if (files.length === 0) return;
    setIsUploading(true);
    setError(null);
    try {
      const uploaded = await uploadFiles([files[0]]);
      const u = uploaded[0];
      addUploads(uploaded);
      setUploadId(u.id);
      setUploadedName(u.original_name);
      // Go to intent selection instead of jumping straight to chat
      setStep("intent");
    } catch (e: any) {
      setError(e.message || "Unknown error");
      setStep("upload");
    } finally {
      setIsUploading(false);
    }
  };

  const handleProceedToChat = async () => {
    if (!uploadId) return;
    setStep("starting");
    setError(null);
    try {
      const chat = await startChat(uploadId);
      setSessionId(chat.session_id);
      setInitialMessage(chat.message);
      setInitialHasScript(chat.has_script || false);
      setStep("chat");
    } catch (e: any) {
      setError(e.message || "Failed to start chat session");
      setStep("intent");
    }
  };

  const handleExecuted = (newJobId: string) => {
    setJobId(newJobId);
    setStep("done");
  };

  const handleReset = () => {
    setStep("upload");
    setSessionId(null);
    setInitialMessage("");
    setUploadedName("");
    setUploadId(null);
    setJobId(null);
    setError(null);
  };

  const STEPS: Step[] = ["upload", "intent", "starting", "chat", "done"];
  const STEP_LABELS: Record<Step, string> = {
    upload: "Upload",
    intent: "Analyze",
    starting: "Preparing",
    chat: "Configure",
    done: "Download",
  };

  // Steps to show in the indicator (hide "starting" as it's a transient loading state)
  const VISIBLE_STEPS: Step[] = ["upload", "intent", "chat", "done"];

  return (
    <div className={step === "chat" ? "max-w-4xl" : "max-w-3xl"}>
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-2xl font-bold text-gray-900">
          {step === "intent"
            ? "Analyze File"
            : step === "chat"
            ? "Configure Migration"
            : "Upload & Migrate"}
        </h1>
        {(step === "chat" || step === "intent") && (
          <button onClick={handleReset} className="px-3 py-1.5 text-xs text-gray-500 border border-gray-300 rounded-lg hover:bg-gray-50">
            Start over
          </button>
        )}
      </div>
      <p className="text-gray-500 mb-4 text-sm">
        {step === "intent"
          ? `Choose what you want to do with ${uploadedName}.`
          : step === "chat"
          ? `Working on: ${uploadedName} — Tell Sunshine what you need.`
          : "Drop your source ERP file. Sunshine analyzes it and helps you transform it through conversation."}
      </p>

      {/* Step indicator */}
      <div className="flex items-center gap-2 mb-6">
        {VISIBLE_STEPS.map((s, i) => {
          const currentIdx = STEPS.indexOf(step);
          const thisIdx = STEPS.indexOf(s);
          const active = step === s || (step === "starting" && s === "intent");
          const past = currentIdx > thisIdx;
          return (
            <div key={s} className="flex items-center gap-2">
              <div className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium transition-colors
                ${active ? "bg-sunshine-500 text-white" : past ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-400"}`}>
                {past && <span>&#10003;</span>}
                {STEP_LABELS[s]}
              </div>
              {i < VISIBLE_STEPS.length - 1 && <span className="text-gray-300">&rarr;</span>}
            </div>
          );
        })}
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm flex items-start justify-between gap-2">
          <div>{error}</div>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-600 font-bold text-lg leading-none">&times;</button>
        </div>
      )}

      {step === "upload" && (
        <DropZone onFilesSelected={handleFilesSelected} isUploading={isUploading} />
      )}

      {step === "intent" && uploadId && (
        <IntentAnalysis
          uploadId={uploadId}
          filename={uploadedName}
          onProceed={handleProceedToChat}
        />
      )}

      {step === "starting" && (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
          <div className="inline-block w-8 h-8 border-4 border-sunshine-400 border-t-transparent rounded-full animate-spin mb-4" />
          <p className="text-gray-700 font-medium">Preparing <span className="text-sunshine-600">{uploadedName}</span>...</p>
          <p className="text-gray-400 text-sm mt-1">Setting up the transformation session</p>
        </div>
      )}

      {step === "chat" && sessionId && (
        <TransformChat
          sessionId={sessionId}
          initialMessage={initialMessage}
          initialHasScript={initialHasScript}
          uploadedName={uploadedName}
          onExecuted={handleExecuted}
        />
      )}

      {step === "done" && jobId && (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
          <div className="text-4xl mb-3">&#9989;</div>
          <h2 className="text-lg font-semibold text-gray-900 mb-1">Migration complete</h2>
          <p className="text-gray-500 text-sm mb-6">Your file has been transformed and is ready to download.</p>
          <div className="flex gap-3 justify-center">
            <a href={getExportUrl(jobId)} download
              className="px-6 py-2.5 bg-sunshine-500 text-white rounded-lg hover:bg-sunshine-600 font-medium text-sm transition-colors">
              Download Excel
            </a>
            <button onClick={handleReset} className="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">
              Migrate another file
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
