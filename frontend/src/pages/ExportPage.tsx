import { useEffect, useState } from "react";
import { Download, CheckCircle, AlertTriangle, Loader } from "lucide-react";
import { getJobs, getValidation, getExportUrl } from "../api/client";
import { useAppStore } from "../stores/appStore";
import StatusBadge from "../components/common/StatusBadge";
import type { ValidationSummary } from "../types";

export default function ExportPage() {
  const { jobs, setJobs, currentJob } = useAppStore();
  const [validation, setValidation] = useState<ValidationSummary | null>(null);

  useEffect(() => {
    getJobs().then((data) => setJobs(data.jobs));
  }, [setJobs]);

  const completedJobs = jobs.filter((j) => j.status === "completed");
  const displayJob = currentJob || completedJobs[0];

  useEffect(() => {
    if (displayJob?.id) {
      getValidation(displayJob.id).then(setValidation);
    }
  }, [displayJob?.id]);

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-2">Export</h1>
      <p className="text-gray-500 mb-6">
        Download your transformed files in Light.inc format.
      </p>

      {!displayJob ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <Loader className="mx-auto text-gray-400 mb-4" size={48} />
          <p className="text-gray-500">No completed jobs yet.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Job summary */}
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="font-semibold text-gray-900">
                  Job: {displayJob.id.slice(0, 8)}...
                </h2>
                <p className="text-sm text-gray-500 mt-1">
                  Playbook: {displayJob.playbook_name} · Created:{" "}
                  {new Date(displayJob.created_at).toLocaleString()}
                </p>
              </div>
              <StatusBadge status={displayJob.status} />
            </div>
          </div>

          {/* Validation summary */}
          {validation && (
            <div className="bg-white rounded-xl border border-gray-200 p-6">
              <h2 className="font-semibold text-gray-900 mb-4">
                Validation Results
              </h2>
              <div className="grid grid-cols-3 gap-4 mb-4">
                <div className="flex items-center gap-2">
                  <AlertTriangle size={16} className="text-red-500" />
                  <span className="text-sm">
                    {validation.errors} error{validation.errors !== 1 && "s"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <AlertTriangle size={16} className="text-yellow-500" />
                  <span className="text-sm">
                    {validation.warnings} warning{validation.warnings !== 1 && "s"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <CheckCircle size={16} className="text-green-500" />
                  <span className="text-sm">{validation.info} info</span>
                </div>
              </div>

              {validation.issues.length > 0 && (
                <div className="overflow-x-auto border rounded-lg">
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="text-left px-3 py-2 font-medium text-gray-600">Severity</th>
                        <th className="text-left px-3 py-2 font-medium text-gray-600">Row</th>
                        <th className="text-left px-3 py-2 font-medium text-gray-600">Column</th>
                        <th className="text-left px-3 py-2 font-medium text-gray-600">Message</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {validation.issues.slice(0, 20).map((issue) => (
                        <tr key={issue.id} className="hover:bg-gray-50">
                          <td className="px-3 py-2">
                            <StatusBadge status={issue.severity} />
                          </td>
                          <td className="px-3 py-2">{issue.row_number ?? "—"}</td>
                          <td className="px-3 py-2">{issue.column_name ?? "—"}</td>
                          <td className="px-3 py-2">{issue.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Download button */}
          {displayJob.status === "completed" && (
            <div className="flex justify-center">
              <a
                href={getExportUrl(displayJob.id)}
                download
                className="px-8 py-3 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors font-medium flex items-center gap-2"
              >
                <Download size={20} />
                Download Transformed File
              </a>
            </div>
          )}
        </div>
      )}

      {/* All completed jobs */}
      {completedJobs.length > 1 && (
        <div className="mt-8 bg-white rounded-xl border border-gray-200">
          <div className="p-4 border-b border-gray-100">
            <h2 className="font-semibold text-gray-900">All Completed Jobs</h2>
          </div>
          <div className="divide-y divide-gray-100">
            {completedJobs.map((job) => (
              <div
                key={job.id}
                className="flex items-center justify-between px-4 py-3 hover:bg-gray-50"
              >
                <div>
                  <span className="text-sm font-mono">{job.id.slice(0, 8)}...</span>
                  <span className="text-sm text-gray-500 ml-3">
                    {job.playbook_name}
                  </span>
                </div>
                <a
                  href={getExportUrl(job.id)}
                  download
                  className="text-sm text-sunshine-600 hover:text-sunshine-700 font-medium flex items-center gap-1"
                >
                  <Download size={14} /> Download
                </a>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
