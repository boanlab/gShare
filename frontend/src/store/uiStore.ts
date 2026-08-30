import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type Theme = 'light' | 'dark';

export interface Toast {
  id: string;
  kind: 'info' | 'success' | 'warn' | 'error';
  message: string;
  /** Action offered alongside the message — the undo for a reversible change. */
  action?: { label: string; run: () => void };
}

interface UiState {
  theme: Theme;
  toasts: Toast[];
  toggleTheme(): void;
  applyTheme(): void;
  pushToast(kind: Toast['kind'], message: string, action?: Toast['action']): void;
  dismissToast(id: string): void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      theme: 'dark',
      toasts: [],
      toggleTheme() {
        const next: Theme = get().theme === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = next; // toggles light and dark
        set({ theme: next });
      },
      applyTheme() {
        document.documentElement.dataset.theme = get().theme;
      },
      pushToast(kind, message, action) {
        // crypto.randomUUID exists only in a secure context, so it can be missing over plain HTTP.
        // The lib types declare it as always present (which trips TS2774), hence the narrowing guard.
        const c = globalThis.crypto as Crypto | undefined;
        const id = c?.randomUUID ? c.randomUUID() : `t-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        set({ toasts: [...get().toasts, { id, kind, message, action }] });
        // Longer dwell when an action is offered.
        setTimeout(() => get().dismissToast(id), action ? 10000 : 5000);
      },
      dismissToast(id) {
        set({ toasts: get().toasts.filter((t) => t.id !== id) });
      },
    }),
    {
      name: 'gshare-ui',
      partialize: (s) => ({ theme: s.theme }),
    },
  ),
);
