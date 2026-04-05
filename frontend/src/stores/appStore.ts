import { create } from "zustand";
import type { Job, Playbook, Upload } from "../types";

interface AppState {
  uploads: Upload[];
  playbooks: Playbook[];
  jobs: Job[];
  selectedPlaybook: string | null;
  selectedUploads: string[];
  currentJob: Job | null;

  setUploads: (uploads: Upload[]) => void;
  addUploads: (uploads: Upload[]) => void;
  removeUpload: (id: string) => void;
  setPlaybooks: (playbooks: Playbook[]) => void;
  setSelectedPlaybook: (name: string | null) => void;
  toggleUploadSelection: (id: string) => void;
  selectAllUploads: () => void;
  clearSelection: () => void;
  setJobs: (jobs: Job[]) => void;
  setCurrentJob: (job: Job | null) => void;
}

export const useAppStore = create<AppState>((set, get) => ({
  uploads: [],
  playbooks: [],
  jobs: [],
  selectedPlaybook: null,
  selectedUploads: [],
  currentJob: null,

  setUploads: (uploads) => set({ uploads }),
  addUploads: (newUploads) =>
    set((state) => ({ uploads: [...newUploads, ...state.uploads] })),
  removeUpload: (id) =>
    set((state) => ({
      uploads: state.uploads.filter((u) => u.id !== id),
      selectedUploads: state.selectedUploads.filter((uid) => uid !== id),
    })),
  setPlaybooks: (playbooks) => set({ playbooks }),
  setSelectedPlaybook: (name) => set({ selectedPlaybook: name }),
  toggleUploadSelection: (id) =>
    set((state) => ({
      selectedUploads: state.selectedUploads.includes(id)
        ? state.selectedUploads.filter((uid) => uid !== id)
        : [...state.selectedUploads, id],
    })),
  selectAllUploads: () =>
    set((state) => ({
      selectedUploads: state.uploads.map((u) => u.id),
    })),
  clearSelection: () => set({ selectedUploads: [] }),
  setJobs: (jobs) => set({ jobs }),
  setCurrentJob: (job) => set({ currentJob: job }),
}));
