import { useCallback, useRef, useState } from "react";
import { Upload } from "lucide-react";

interface DropZoneProps {
  onFilesSelected: (files: File[]) => void;
  isUploading: boolean;
}

export default function DropZone({ onFilesSelected, isUploading }: DropZoneProps) {
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const files = Array.from(e.dataTransfer.files).filter(
        (f) =>
          f.name.endsWith(".xlsx") ||
          f.name.endsWith(".xls") ||
          f.name.endsWith(".csv") ||
          f.name.endsWith(".xlsm")
      );
      if (files.length) onFilesSelected(files);
    },
    [onFilesSelected]
  );

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || []);
      if (files.length) onFilesSelected(files);
      // Reset so same file can be re-selected
      if (inputRef.current) inputRef.current.value = "";
    },
    [onFilesSelected]
  );

  return (
    <div
      onClick={() => inputRef.current?.click()}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-all ${
        isDragging
          ? "border-sunshine-400 bg-sunshine-50"
          : "border-gray-300 hover:border-sunshine-300 hover:bg-gray-50"
      } ${isUploading ? "opacity-50 pointer-events-none" : ""}`}
    >
      <input
        ref={inputRef}
        type="file"
        id="sunshine-file-input"
        multiple
        accept=".xlsx,.xls,.csv,.xlsm"
        onChange={handleChange}
        className="hidden"
      />
      <Upload className="mx-auto mb-4 text-gray-400" size={48} />
      <p className="text-lg font-medium text-gray-700">
        {isUploading ? "Uploading..." : "Drop files here or click to browse"}
      </p>
      <p className="text-sm text-gray-500 mt-2">
        Supports .xlsx, .xls, .xlsm, and .csv files
      </p>
    </div>
  );
}
