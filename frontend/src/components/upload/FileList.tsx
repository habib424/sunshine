import { FileSpreadsheet, Trash2, Check } from "lucide-react";
import type { Upload } from "../../types";

interface FileListProps {
  uploads: Upload[];
  selectedIds: string[];
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
}

export default function FileList({
  uploads,
  selectedIds,
  onToggle,
  onDelete,
}: FileListProps) {
  if (!uploads.length) return null;

  return (
    <div className="space-y-2">
      {uploads.map((upload) => (
        <div
          key={upload.id}
          className={`flex items-center gap-3 p-3 rounded-lg border transition-colors cursor-pointer ${
            selectedIds.includes(upload.id)
              ? "border-sunshine-300 bg-sunshine-50"
              : "border-gray-200 bg-white hover:bg-gray-50"
          }`}
          onClick={() => onToggle(upload.id)}
        >
          <div
            className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
              selectedIds.includes(upload.id)
                ? "bg-sunshine-500 border-sunshine-500"
                : "border-gray-300"
            }`}
          >
            {selectedIds.includes(upload.id) && (
              <Check size={14} className="text-white" />
            )}
          </div>

          <FileSpreadsheet size={20} className="text-green-600 shrink-0" />

          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-gray-900 truncate">
              {upload.original_name}
            </p>
            <p className="text-xs text-gray-500">
              {upload.row_count ?? "?"} rows
              {upload.file_type && (
                <span className="ml-2 px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded text-xs">
                  {upload.file_type}
                </span>
              )}
            </p>
          </div>

          <button
            onClick={(e) => {
              e.stopPropagation();
              onDelete(upload.id);
            }}
            className="p-1 text-gray-400 hover:text-red-500 transition-colors"
          >
            <Trash2 size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}
