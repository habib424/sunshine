import type { PreviewResponse } from "../../types";

interface PreviewPanelProps {
  preview: PreviewResponse | null;
  isLoading: boolean;
}

export default function PreviewPanel({ preview, isLoading }: PreviewPanelProps) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        <div className="inline-block w-5 h-5 border-2 border-sunshine-400 border-t-transparent rounded-full animate-spin mr-2" />
        Previewing...
      </div>
    );
  }

  if (!preview) {
    return (
      <div className="flex items-center justify-center h-full text-gray-300 text-sm">
        Enable rules to see a preview
      </div>
    );
  }

  if (preview.errors.length > 0) {
    return (
      <div className="p-3">
        {preview.errors.map((err, i) => (
          <div key={i} className="p-2 bg-red-50 border border-red-200 rounded text-xs text-red-700 mb-2">
            {err}
          </div>
        ))}
      </div>
    );
  }

  if (preview.headers.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-300 text-sm">
        No data yet
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 py-2 border-b border-gray-100 flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">
          Preview ({preview.total_rows} rows)
        </span>
        <span className="text-[10px] text-gray-400">
          {preview.headers.length} columns
        </span>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="w-full text-[11px]">
          <thead className="bg-gray-50 sticky top-0">
            <tr>
              {preview.headers.map((h, i) => (
                <th key={i} className="px-2 py-1.5 text-left font-medium text-gray-500 whitespace-nowrap border-b border-gray-200">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {preview.rows.map((row: unknown[], ri) => (
              <tr key={ri} className="hover:bg-sunshine-50/30">
                {(row as any[]).map((cell, ci) => (
                  <td key={ci} className="px-2 py-1 whitespace-nowrap text-gray-600 max-w-[200px] truncate">
                    {cell === null || cell === "" || cell === "None" ? (
                      <span className="text-gray-300">-</span>
                    ) : typeof cell === "number" ? (
                      <span className="font-mono">{cell.toLocaleString()}</span>
                    ) : (
                      String(cell)
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
