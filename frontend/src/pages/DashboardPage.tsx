import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Upload, Play, CheckCircle, AlertTriangle } from "lucide-react";
import { getJobs } from "../api/client";
import { useAppStore } from "../stores/appStore";
import StatusBadge from "../components/common/StatusBadge";

export default function DashboardPage() {
  const { jobs, setJobs } = useAppStore();

  useEffect(() => {
    getJobs().then((data) => setJobs(data.jobs));
  }, [setJobs]);

  const stats = {
    total: jobs.length,
    completed: jobs.filter((j) => j.status === "completed").length,
    failed: jobs.filter((j) => j.status === "failed").length,
    running: jobs.filter((j) => j.status === "running").length,
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-gray-500 mt-1">
            Overview of your data migration jobs
          </p>
        </div>
        <Link
          to="/upload"
          className="px-4 py-2 bg-sunshine-500 text-white rounded-lg hover:bg-sunshine-600 transition-colors font-medium text-sm flex items-center gap-2"
        >
          <Upload size={16} /> New Migration
        </Link>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        {[
          { label: "Total Jobs", value: stats.total, icon: Play, color: "text-gray-600" },
          { label: "Completed", value: stats.completed, icon: CheckCircle, color: "text-green-600" },
          { label: "Running", value: stats.running, icon: Play, color: "text-blue-600" },
          { label: "Failed", value: stats.failed, icon: AlertTriangle, color: "text-red-600" },
        ].map(({ label, value, icon: Icon, color }) => (
          <div key={label} className="bg-white rounded-xl border border-gray-200 p-5">
            <div className="flex items-center gap-3">
              <Icon size={20} className={color} />
              <span className="text-sm text-gray-500">{label}</span>
            </div>
            <p className="text-3xl font-bold mt-2">{value}</p>
          </div>
        ))}
      </div>

      {/* Recent Jobs */}
      <div className="bg-white rounded-xl border border-gray-200">
        <div className="p-4 border-b border-gray-100">
          <h2 className="font-semibold text-gray-900">Recent Jobs</h2>
        </div>
        {jobs.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            <p>No jobs yet. Start by uploading files.</p>
            <Link
              to="/upload"
              className="inline-block mt-3 text-sunshine-600 hover:text-sunshine-700 font-medium text-sm"
            >
              Go to Upload →
            </Link>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left px-4 py-3 font-medium text-gray-600">ID</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Playbook</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Status</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Stage</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {jobs.slice(0, 10).map((job) => (
                <tr key={job.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono text-xs">{job.id.slice(0, 8)}...</td>
                  <td className="px-4 py-3">{job.playbook_name}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="px-4 py-3 text-gray-500">{job.current_stage || "—"}</td>
                  <td className="px-4 py-3 text-gray-500">
                    {new Date(job.created_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
