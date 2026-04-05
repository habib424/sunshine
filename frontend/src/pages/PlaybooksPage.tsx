import { useEffect } from "react";
import { BookOpen } from "lucide-react";
import { getPlaybooks } from "../api/client";
import { useAppStore } from "../stores/appStore";

export default function PlaybooksPage() {
  const { playbooks, setPlaybooks } = useAppStore();

  useEffect(() => {
    getPlaybooks().then(setPlaybooks);
  }, [setPlaybooks]);

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-2">Playbooks</h1>
      <p className="text-gray-500 mb-6">
        Available source ERP playbooks for data transformation.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {playbooks.map((playbook) => (
          <div
            key={playbook.name}
            className="bg-white rounded-xl border border-gray-200 p-6 hover:border-sunshine-300 transition-colors"
          >
            <div className="flex items-start gap-3">
              <BookOpen size={24} className="text-sunshine-500 mt-0.5" />
              <div>
                <h3 className="font-semibold text-gray-900">
                  {playbook.display_name}
                </h3>
                <p className="text-sm text-gray-500 mt-1">
                  {playbook.description}
                </p>
                <div className="flex flex-wrap gap-1.5 mt-3">
                  {playbook.file_types.map((ft) => (
                    <span
                      key={ft}
                      className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs"
                    >
                      {ft.replace(/_/g, " ")}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {playbooks.length === 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center text-gray-500">
          No playbooks found. Add playbook YAML files to the playbooks/ directory.
        </div>
      )}
    </div>
  );
}
