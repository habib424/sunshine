interface DataPreviewProps {
  headers: string[];
  rows: unknown[][];
  maxRows?: number;
}

export default function DataPreview({
  headers,
  rows,
  maxRows = 10,
}: DataPreviewProps) {
  const displayRows = rows.slice(0, maxRows);

  return (
    <div className="overflow-x-auto border rounded-lg">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50">
          <tr>
            {headers.map((h, i) => (
              <th
                key={i}
                className="px-3 py-2 text-left font-medium text-gray-600 whitespace-nowrap"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {displayRows.map((row, ri) => (
            <tr key={ri} className="hover:bg-gray-50">
              {(row as unknown[]).map((cell, ci) => (
                <td
                  key={ci}
                  className="px-3 py-1.5 text-gray-700 whitespace-nowrap"
                >
                  {String(cell ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > maxRows && (
        <p className="text-xs text-gray-500 p-2 text-center">
          Showing {maxRows} of {rows.length} rows
        </p>
      )}
    </div>
  );
}
