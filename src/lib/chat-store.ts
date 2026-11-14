/**
 * Local conversation storage (browser localStorage).
 * Swap the read/write functions for backend calls later — components only use the hooks/actions below.
 */
import { useSyncExternalStore } from "react";
import { DEFAULT_LANGUAGE } from "@/config/app";
import type { AnswerMode, ChatMessage, ChatThread } from "@/types/chat";

function createLocalStore<T>(key: string, fallback: T) {
  let cacheRaw: string | null | undefined;
  let cacheVal: T = fallback;
  const listeners = new Set<() => void>();

  const read = (): T => {
    if (typeof window === "undefined") return fallback;
    const raw = window.localStorage.getItem(key);
    if (raw !== cacheRaw) {
      cacheRaw = raw;
      try {
        cacheVal = raw ? (JSON.parse(raw) as T) : fallback;
      } catch {
        cacheVal = fallback;
      }
    }
    return cacheVal;
  };

  const write = (updater: (prev: T) => T) => {
    const next = updater(read());
    const raw = JSON.stringify(next);
    window.localStorage.setItem(key, raw);
    cacheRaw = raw;
    cacheVal = next;
    listeners.forEach((l) => l());
  };

  const subscribe = (listener: () => void) => {
    listeners.add(listener);
    const onStorage = (e: StorageEvent) => {
      if (e.key === key) listener();
    };
    window.addEventListener("storage", onStorage);
    return () => {
      listeners.delete(listener);
      window.removeEventListener("storage", onStorage);
    };
  };

  const useValue = () => useSyncExternalStore(subscribe, read, () => fallback);

  return { read, write, subscribe, useValue };
}

export interface Preferences {
  mode: AnswerMode | null;
  language: string;
  robotId: string | null;
}

const EMPTY_THREADS: ChatThread[] = [];
const DEFAULT_PREFS: Preferences = { mode: null, language: DEFAULT_LANGUAGE, robotId: null };

const threadsStore = createLocalStore<ChatThread[]>("roboguide.threads.v1", EMPTY_THREADS);
const prefsStore = createLocalStore<Preferences>("roboguide.prefs.v1", DEFAULT_PREFS);

export const useThreads = threadsStore.useValue;
export const usePreferences = prefsStore.useValue;

export const newId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "").slice(0, 12)
    : Math.random().toString(36).slice(2, 14);

export function setPreferences(patch: Partial<Preferences>) {
  prefsStore.write((p) => ({ ...p, ...patch }));
}

export function getThread(id: string) {
  return threadsStore.read().find((t) => t.id === id) ?? null;
}

export function createThread(init: {
  id?: string;
  mode: AnswerMode;
  robotId?: string | null;
  language?: string;
}) {
  const now = Date.now();
  const thread: ChatThread = {
    id: init.id ?? newId(),
    title: "New conversation",
    mode: init.mode,
    robotId: init.robotId ?? null,
    language: init.language ?? DEFAULT_LANGUAGE,
    createdAt: now,
    updatedAt: now,
    messages: [],
  };
  threadsStore.write((threads) =>
    threads.some((t) => t.id === thread.id) ? threads : [thread, ...threads],
  );
  return thread.id;
}

/** Idempotent: creates the thread only if it doesn't already exist. */
export function ensureThread(
  id: string,
  defaults: { mode: AnswerMode; robotId?: string | null; language?: string },
) {
  if (!getThread(id)) createThread({ id, ...defaults });
}

export function updateThread(id: string, patch: Partial<Omit<ChatThread, "id" | "messages">>) {
  threadsStore.write((threads) =>
    threads.map((t) => (t.id === id ? { ...t, ...patch, updatedAt: Date.now() } : t)),
  );
}

export function appendMessage(id: string, message: ChatMessage) {
  threadsStore.write((threads) =>
    threads.map((t) => {
      if (t.id !== id) return t;
      const isFirstUser = message.role === "user" && !t.messages.some((m) => m.role === "user");
      return {
        ...t,
        title: isFirstUser ? message.content.slice(0, 70) : t.title,
        messages: [...t.messages, message],
        updatedAt: Date.now(),
      };
    }),
  );
}

export function deleteThread(id: string) {
  threadsStore.write((threads) => threads.filter((t) => t.id !== id));
}
