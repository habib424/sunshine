import { useState, useEffect } from "react";
import DropZone from "../components/upload/DropZone";
import TransformChat from "../components/chat/TransformChat";
import { uploadFiles, startChat, getExportUrl, getIntents } from "../api/client";
import { useAppStore } from "../stores/appStore";
import { Target, FileSearch, GitCompare, Loader2 } from "lucide-react";

type Step = "upload" | "intent" | "starting" | "chat" | "done";

const INTENT_ICONS: Record<string, typeof Target> = {
  convert_to_light_je: Target,
  validate_je: FileSearch,
  reconcile_je_to_gl: GitCompare,
};

export default function UploadPage() {
  const { addUploads, setUploads } = useAppStore();
  const [step, setStep] = useState<Step>("upload");
  const [isUploading, setIsUploading] = useState(false);
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [uploadedName, setUploadedName] = useState("");
  const [selectedIntent, setSelectedIntent] = useState<string | null>(null);
  const [intents, setIntents] = useState<any[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [initialMessage, setInitialMessage] = useState("");
  const [initialHasScript, setInitialHasScript] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStep("upload");
    setError(null);
    setUploads([]);
    getIntents().then(setIntents).catch(() => {});
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
      setStep("intent");
    } catch (e: any) {
      setError(e.message || "Unknown error");
      setStep("upload");
    } finally {
      setIsUploading(false);
    }
  };

  const handleIntentSelected = async (intent: string) => {
    if (!uploadId) return;
    setSelectedIntent(intent);
    setStep("starting");
    setError(null);
    try {
      const chat = await startChat(uploadId, intent);
      setSessionId(chat.session_id);
      setInitialMessage(chat.message);
      setInitialHasScript(chat.has_script || false);
      setStep("chat");
    } catch (e: any) {
      setError(e.message || "Failed to start session");
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
    setSelectedIntent(null);
    setJobId(null);
    setError(null);
  };

  const VISIBLE_STEPS: Step[] = ["upload", "intent", "chat", "done"];
  const STEP_LABELS: Record<Step, string> = {
    upload: "Upload",
    intent: "Intent",
    starting: "Preparing",
    chat: "Configure",
    done: "Download",
  };

  return (
    <div className={step === "chat" ? "max-w-4xl" : "max-w-3xl"}>
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-2xl font-bold text-gray-900">
          {step === "intent" ? "What do you need?" : step === "chat" ? "Configure Migration" : "Upload & Migrate"}
        </h1>
        {(step === "chat" || step === "intent") && (
          <button onClick={handleReset} className="px-3 py-1.5 text-xs text-gray-500 border border-gray-300 rounded-lg hover:bg-gray-50">
            Start over
          </button>
        )}
      </div>
      <p className="text-gray-500 mb-4 text-sm">
        {step === "intent"
          ? `Choose what to do with ${uploadedName}`
          : step === "chat"
          ? `Working on: ${uploadedName}`
          : "Drop your source ERP file. Sunshine analyzes it and helps you transform it through conversation."}
      </p>

      {/* Step indicator */}
      <div className="flex items-center gap-2 mb-6">
        {VISIBLE_STEPS.map((s, i) => {
          const currentIdx = ["upload", "intent", "starting", "chat", "done"].indexOf(step);
          const thisIdx = ["upload", "intent", "starting", "chat", "done"].indexOf(s);
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

      {step === "intent" && (
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <div className="grid gap-3">
            {intents.map((intent) => {
              const Icon = INTENT_ICONS[intent.name] || Target;
              return (
                <button
                  key={intent.name}
                  onClick={() => handleIntentSelected(intent.name)}
                  className="flex items-start gap-3 p-4 rounded-lg border border-gray-200 hover:border-sunshine-400 hover:bg-sunshine-50 text-left transition-all cursor-pointer"
                >
                  <Icon className="w-5 h-5 mt-0.5 flex-shrink-0 text-gray-400" />
                  <div>
                    <div className="text-sm font-medium text-gray-800">{intent.label}</div>
                    <div className="text-xs text-gray-400 mt-0.5">{intent.description}</div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {step === "starting" && (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
          <Loader2 className="w-8 h-8 text-sunshine-400 animate-spin mx-auto mb-4" />
          <p className="text-gray-700 font-medium">Analyzing <span className="text-sunshine-600">{uploadedName}</span>...</p>
          <p className="text-gray-400 text-sm mt-1">Detecting file structure and preparing transformation plan</p>
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
