import { useEffect, useState } from "react";
import { getJobs, getValidation } from "../api/client";
import { useAppStore } from "../stores/appStore";
import StatusBadge from "../components/common/StatusBadge";
import type { ValidationSummary } from "../types";

export default function ValidationPage() {
  const { jobs, setJobs } = useAppStore();
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationSummary | null>(null);

  useEffect(() => {
    getJobs().then((data) => {
      setJobs(data.jobs);
      if (data.jobs.length > 0) setSelectedJobId(data.jobs[0].id);
    });
  }, [setJobs]);

  useEffect(() => {
    if (selectedJobId) {
      getValidation(selectedJobId).then(setValidation);
    }
  }, [selectedJobId]);

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-2">Validation</h1>
      <p className="text-gray-500 mb-6">Review validation results for your transformation jobs.</p>

      <div className="mb-4">
        <select
          value={selectedJobId || ""}
          onChange={(e) => setSelectedJobId(e.target.value || null)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
        >
          <option value="">Select a job...</option>
          {jobs.map((j) => (
            <option key={j.id} value={j.id}>
              {j.id.slice(0, 8)}... — {j.playbook_name} ({j.status})
            </option>
          ))}
        </select>
      </div>

      {validation ? (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-red-50 rounded-lg p-4 text-center">
              <p className="text-2xl font-bold text-red-700">{validation.errors}</p>
              <p className="text-sm text-red-600">Errors</p>
            </div>
            <div className="bg-yellow-50 rounded-lg p-4 text-center">
              <p className="text-2xl font-bold text-yellow-700">{validation.warnings}</p>
              <p className="text-sm text-yellow-600">Warnings</p>
            </div>
            <div className="bg-blue-50 rounded-lg p-4 text-center">
              <p className="text-2xl font-bold text-blue-700">{validation.info}</p>
              <p className="text-sm text-blue-600">Info</p>
            </div>
          </div>

          {validation.issues.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left px-4 py-3 font-medium text-gray-600">Severity</th>
                    <th className="text-left px-4 py-3 font-medium text-gray-600">Row</th>
                    <th className="text-left px-4 py-3 font-medium text-gray-600">Column</th>
                    <th className="text-left px-4 py-3 font-medium text-gray-600">Validator</th>
                    <th className="text-left px-4 py-3 font-medium text-gray-600">Message</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {validation.issues.map((issue) => (
                    <tr key={issue.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3"><StatusBadge status={issue.severity} /></td>
                      <td className="px-4 py-3">{issue.row_number ?? "—"}</td>
                      <td className="px-4 py-3">{issue.column_name ?? "—"}</td>
                      <td className="px-4 py-3 text-gray-500">{issue.validator_name}</td>
                      <td className="px-4 py-3">{issue.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {validation.issues.length === 0 && (
            <div className="bg-green-50 rounded-xl p-8 text-center">
              <p className="text-green-700 font-medium">All validations passed!</p>
            </div>
          )}
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center text-gray-500">
          Select a job to view validation results.
        </div>
      )}
    </div>
  );
}
