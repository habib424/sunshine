import { Routes, Route } from "react-router-dom";
import AppShell from "./components/layout/AppShell";
import DashboardPage from "./pages/DashboardPage";
import UploadPage from "./pages/UploadPage";
import TransformPage from "./pages/TransformPage";
import ValidationPage from "./pages/ValidationPage";
import ExportPage from "./pages/ExportPage";
import PlaybooksPage from "./pages/PlaybooksPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/transform" element={<TransformPage />} />
        <Route path="/validation" element={<ValidationPage />} />
        <Route path="/export" element={<ExportPage />} />
        <Route path="/playbooks" element={<PlaybooksPage />} />
      </Route>
    </Routes>
  );
}
