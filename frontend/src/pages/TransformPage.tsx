import { useEffect } from "react";
import { getJobs } from "../api/client";
import { useAppStore } from "../stores/appStore";
import StatusBadge from "../components/common/StatusBadge";

const STAGES = ["ingest", "detect", "map", "transform", "validate", "export"];

export default function TransformPage() {
  const { jobs, setJobs } = useAppStore();

  useEffect(() => {
    getJobs().then((data) => setJobs(data.jobs));
  }, [setJobs]);

  const activeJobs = jobs.filter((j) =>
    ["pending", "running"].includes(j.status)
  );
  const recentJobs = jobs.slice(0, 5);

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-2">Transform</h1>
      <p className="text-gray-500 mb-6">Monitor transformation pipeline progress.</p>

      {activeJobs.length > 0 ? (
        activeJobs.map((job) => (
          <div
            key={job.id}
            className="bg-white rounded-xl border border-gray-200 p-6 mb-4"
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold">
                Job {job.id.slice(0, 8)}... — {job.playbook_name}
              </h3>
              <StatusBadge status={job.status} />
            </div>
            <div className="flex gap-2">
              {STAGES.map((stage) => {
                const currentIdx = job.current_stage
                  ? STAGES.indexOf(job.current_stage)
                  : -1;
                const stageIdx = STAGES.indexOf(stage);
                let color = "bg-gray-100 text-gray-500";
                if (stageIdx < currentIdx) color = "bg-green-100 text-green-700";
                if (stageIdx === currentIdx) color = "bg-blue-100 text-blue-700";

                return (
                  <span
                    key={stage}
                    className={`px-3 py-1 rounded-full text-xs font-medium ${color}`}
                  >
                    {stage}
                  </span>
                );
              })}
            </div>
          </div>
        ))
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center text-gray-500">
          No active transformations. Start a migration from the Upload page.
        </div>
      )}

      {recentJobs.length > 0 && (
        <div className="mt-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-3">
            Recent Jobs
          </h2>
          <div className="bg-white rounded-xl border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Job</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Playbook</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Status</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {recentJobs.map((job) => (
                  <tr key={job.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-mono text-xs">{job.id.slice(0, 8)}...</td>
                    <td className="px-4 py-3">{job.playbook_name}</td>
                    <td className="px-4 py-3"><StatusBadge status={job.status} /></td>
                    <td className="px-4 py-3 text-gray-500">{new Date(job.updated_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
