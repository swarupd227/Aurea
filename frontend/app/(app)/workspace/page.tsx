"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as api from "./api";

interface ThreadSummary {
  id: string;
  kind: string;
  title: string;
  subject_id?: string;
  created_at: string;
}

export default function WorkspacePage() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedKind, setSelectedKind] = useState<string>("ask_astra");
  const [title, setTitle] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const router = useRouter();

  // Load threads on mount
  useEffect(() => {
    const loadThreads = async () => {
      try {
        setIsLoading(true);
        const data = await api.fetchThreads();
        setThreads(data.threads || []);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load threads"
        );
      } finally {
        setIsLoading(false);
      }
    };

    loadThreads();
  }, []);

  const handleCreateThread = async () => {
    if (!title.trim()) return;

    setIsCreating(true);
    setError(null);

    try {
      const data = await api.createThread({
        kind: selectedKind,
        title: title.trim(),
      });

      // Navigate to the new thread
      router.push(`/workspace/${data.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create thread");
    } finally {
      setIsCreating(false);
      setTitle("");
    }
  };

  return (
    <div className="flex h-screen bg-paper">
      {/* Sidebar */}
      <div className="w-80 border-r border-border flex flex-col">
        {/* Header */}
        <div className="border-b border-border p-6">
          <h1 className="text-xl font-bold text-ink">Astra for Wealth</h1>
          <p className="text-xs text-ink-muted mt-1">Conversation Workspace</p>
        </div>

        {/* New Thread Form */}
        <div className="border-b border-border p-4 space-y-3">
          <div>
            <label className="block text-xs font-medium text-ink mb-2">
              Thread Type
            </label>
            <select
              value={selectedKind}
              onChange={(e) => setSelectedKind(e.target.value)}
              className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-ink focus:outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="ask_astra">Ask Astra</option>
              <option value="household">Household</option>
              <option value="onboarding_case">Onboarding Case</option>
              <option value="incident">Incident</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-ink mb-2">
              Title
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !isCreating) {
                  handleCreateThread();
                }
              }}
              placeholder="e.g., Portfolio Review"
              className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-ink placeholder-ink-muted focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </div>

          <button
            onClick={handleCreateThread}
            disabled={isCreating || !title.trim()}
            className="w-full bg-accent text-ink px-3 py-2 rounded font-medium text-sm hover:bg-yellow-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isCreating ? "Creating..." : "New Thread"}
          </button>

          {error && (
            <p className="text-xs text-crit bg-crit-bg rounded p-2">{error}</p>
          )}
        </div>

        {/* Thread List */}
        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            <div className="p-4 text-center text-ink-muted text-xs">
              Loading threads...
            </div>
          ) : threads.length === 0 ? (
            <div className="p-4 text-center text-ink-muted text-xs">
              No threads yet. Create one to start.
            </div>
          ) : (
            <div className="space-y-1 p-2">
              {threads.map((thread) => (
                <Link
                  key={thread.id}
                  href={`/workspace/${thread.id}`}
                  className="block p-3 rounded hover:bg-surface transition-colors text-sm"
                >
                  <p className="font-medium text-ink truncate">{thread.title}</p>
                  <p className="text-xs text-ink-muted mt-1">{thread.kind}</p>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Main Area */}
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center">
          <div className="w-16 h-16 rounded-full bg-surface border-2 border-accent border-opacity-30 flex items-center justify-center mx-auto mb-4">
            <span className="text-2xl font-bold text-accent">A</span>
          </div>
          <h2 className="text-xl font-semibold text-ink mb-2">
            Welcome to Astra
          </h2>
          <p className="text-sm text-ink-muted max-w-xs">
            Create or select a thread to start a conversation with Astra and
            the team.
          </p>
        </div>
      </div>
    </div>
  );
}
