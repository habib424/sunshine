import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Upload,
  Play,
  CheckCircle,
  Download,
  BookOpen,
  GitCompare,
  LogOut,
} from "lucide-react";
import { logout } from "../../api/client";
import { useAuthStore } from "../../stores/authStore";

const navItems = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/upload", icon: Upload, label: "Upload" },
  { to: "/transform", icon: Play, label: "Transform" },
  { to: "/validation", icon: CheckCircle, label: "Validation" },
  { to: "/reconciliation", icon: GitCompare, label: "Reconcile" },
  { to: "/export", icon: Download, label: "Export" },
  { to: "/playbooks", icon: BookOpen, label: "Playbooks" },
];

export default function Sidebar() {
  const user = useAuthStore((s) => s.user);

  const handleSignOut = async () => {
    try {
      await logout();
    } finally {
      window.location.reload();
    }
  };

  return (
    <aside className="w-64 bg-white border-r border-gray-200 flex flex-col">
      <div className="p-6 border-b border-gray-200">
        <h1 className="text-2xl font-bold text-sunshine-600 flex items-center gap-2">
          <span className="text-3xl">☀️</span> Sunshine
        </h1>
        <p className="text-xs text-gray-500 mt-1">ERP Data Migration</p>
      </div>
      <nav className="flex-1 p-4 space-y-1">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? "bg-sunshine-50 text-sunshine-700"
                  : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
              }`
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>
      {user?.email && (
        <div className="p-4 border-t border-gray-200 flex items-center gap-2">
          {user.picture ? (
            <img src={user.picture} alt="" className="w-7 h-7 rounded-full" referrerPolicy="no-referrer" />
          ) : (
            <div className="w-7 h-7 rounded-full bg-sunshine-100 text-sunshine-700 flex items-center justify-center text-xs font-semibold">
              {user.email[0].toUpperCase()}
            </div>
          )}
          <span className="flex-1 text-xs text-gray-500 truncate" title={user.email}>
            {user.email}
          </span>
          <button
            onClick={handleSignOut}
            title="Sign out"
            className="text-gray-400 hover:text-gray-700 transition-colors"
          >
            <LogOut size={15} />
          </button>
        </div>
      )}
    </aside>
  );
}
