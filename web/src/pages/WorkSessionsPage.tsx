import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BriefcaseBusiness,
  MessageCircle,
  Play,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Input } from "@nous-research/ui/ui/components/input";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { fetchJSON } from "@/lib/api";
import { cn } from "@/lib/utils";
import { usePageHeader } from "@/contexts/usePageHeader";
import { isDashboardEmbeddedChatEnabled } from "@/lib/dashboard-flags";

type WorkSession = {
  id: string;
  title: string;
  status: string;
  workflow: string;
  origin_channel: string;
  repo?: string | null;
  provider?: string | null;
  cockpit_task_id?: string | null;
  hermes_session_id?: string | null;
  gateway_session_key?: string | null;
  git_branch?: string | null;
  pr_url?: string | null;
  preview_url?: string | null;
  live_url?: string | null;
  objective?: string | null;
  summary?: string | null;
  current_state?: string | null;
  next_actions?: string[];
  updated_at: number;
  created_at: number;
};

type WorkSessionsResponse = {
  work_sessions: WorkSession[];
};

type ResumePacketResponse = {
  resume_packet: Record<string, unknown>;
};

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        ready?: () => void;
        close?: () => void;
        sendData?: (data: string) => void;
        openLink?: (url: string) => void;
      };
    };
  }
}

const WORKFLOWS = ["supervisor", "pilote", "autopilot", "ask_review", "libre", "debug", "deploy"];
const STATUSES = ["", "open", "active", "blocked", "done", "failed"];

function isTelegramMiniApp(): boolean {
  return typeof window !== "undefined" && Boolean(window.Telegram?.WebApp?.sendData);
}

function sendTelegramAction(payload: Record<string, unknown>): boolean {
  const webApp = window.Telegram?.WebApp;
  if (!webApp?.sendData) return false;
  webApp.sendData(JSON.stringify(payload));
  // Telegram closes a sendData Mini App itself. A second synchronous close
  // can win the race on Desktop before the payload reaches the bot.
  return true;
}

function formatTime(ts: number): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleString();
}

function groupByRepo(sessions: WorkSession[]): Array<[string, WorkSession[]]> {
  const grouped = new Map<string, WorkSession[]>();
  for (const session of sessions) {
    const key = session.repo || "Clavardages libres";
    grouped.set(key, [...(grouped.get(key) || []), session]);
  }
  return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
}

export default function WorkSessionsPage() {
  const { setEnd } = usePageHeader();
  const [sessions, setSessions] = useState<WorkSession[]>([]);
  const [selected, setSelected] = useState<WorkSession | null>(null);
  const [resumePacket, setResumePacket] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [repoFilter, setRepoFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [title, setTitle] = useState("");
  const [repo, setRepo] = useState("");
  const [workflow, setWorkflow] = useState("supervisor");

  const telegram = isTelegramMiniApp();
  const resumeInChatEnabled = isDashboardEmbeddedChatEnabled();
  // The Mini App is served by the same private dashboard and receives the
  // ephemeral dashboard token injected into its HTML. Keep this API behind
  // normal dashboard authentication instead of exposing a second public path.
  const workSessionsApi = "/api/work-sessions";

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (repoFilter.trim()) params.set("repo", repoFilter.trim());
      if (statusFilter) params.set("status", statusFilter);
      const qs = params.toString();
      const data = await fetchJSON<WorkSessionsResponse>(`${workSessionsApi}${qs ? `?${qs}` : ""}`);
      setSessions(data.work_sessions);
      setSelected((current) => {
        if (!current) return data.work_sessions[0] ?? null;
        return data.work_sessions.find((item) => item.id === current.id) ?? data.work_sessions[0] ?? null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [repoFilter, statusFilter, workSessionsApi]);

  useEffect(() => {
    window.Telegram?.WebApp?.ready?.();
    void load();
  }, [load]);

  useEffect(() => {
    setEnd(
      <Button onClick={() => void load()} outlined size="sm">
        <RefreshCw className="h-4 w-4" />
        Refresh
      </Button>,
    );
    return () => setEnd(null);
  }, [load, setEnd]);

  useEffect(() => {
    if (!selected) {
      setResumePacket(null);
      return;
    }
    let cancelled = false;
    const url = `/api/work-sessions/${encodeURIComponent(selected.id)}/resume-packet`;
    fetchJSON<ResumePacketResponse>(url)
      .then((data) => {
        if (!cancelled) setResumePacket(data.resume_packet);
      })
      .catch(() => {
        if (!cancelled) setResumePacket(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selected, telegram]);

  const grouped = useMemo(() => groupByRepo(sessions), [sessions]);

  async function createSession() {
    const payload = {
      action: "work_session.create",
      title: title.trim() || repo.trim() || "Nouvelle session",
      repo: repo.trim() || undefined,
      workflow,
      origin_channel: "telegram",
    };
    if (telegram && sendTelegramAction(payload)) return;
    const data = await fetchJSON<{ work_session: WorkSession }>("/api/work-sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setTitle("");
    setRepo("");
    await load();
    setSelected(data.work_session);
  }

  function resumeInChat(session: WorkSession) {
    const sessionId = session.hermes_session_id;
    if (!sessionId) return;
    const url = new URL("chat", window.location.href.endsWith("/") ? window.location.href : `${window.location.href}/`);
    url.pathname = `${window.__HERMES_BASE_PATH__ || ""}/chat`.replace(/\/+/g, "/");
    url.searchParams.set("resume", sessionId);
    const openLink = window.Telegram?.WebApp?.openLink;
    if (openLink) {
      openLink(url.toString());
      return;
    }
    window.location.assign(url.toString());
  }

  function resumeInTelegram(session: WorkSession) {
    sendTelegramAction({
      action: "work_session.resume",
      work_session_id: session.id,
    });
  }

  async function deleteSession(session: WorkSession) {
    if (!window.confirm(`Supprimer définitivement "${session.title}" ?`)) return;
    const payload = { action: "work_session.delete", work_session_id: session.id };
    if (telegram && sendTelegramAction(payload)) return;
    await fetchJSON(`/api/work-sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" });
    setSelected(null);
    await load();
  }

  return (
    <div className="min-h-full bg-background text-text-primary">
      <div className="grid min-h-[calc(100vh-4rem)] grid-cols-1 border-t border-border lg:grid-cols-[20rem_1fr]">
        <aside className="border-b border-border bg-surface/60 lg:border-b-0 lg:border-r">
          <div className="space-y-3 border-b border-border p-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <BriefcaseBusiness className="h-4 w-4 text-text-secondary" />
              Worker Sessions
              {telegram && <Badge tone="secondary">Telegram</Badge>}
            </div>
            <div className="grid gap-2">
              <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Titre" />
              <Input value={repo} onChange={(event) => setRepo(event.target.value)} placeholder="Repo" />
              <select
                className="h-9 border border-border bg-background px-2 text-sm"
                value={workflow}
                onChange={(event) => setWorkflow(event.target.value)}
              >
                {WORKFLOWS.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
              <Button onClick={() => void createSession()} size="sm">
                <Play className="h-4 w-4" />
                Nouveau chat
              </Button>
            </div>
          </div>
          <div className="space-y-2 border-b border-border p-4">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-text-tertiary" />
              <Input
                className="pl-8"
                value={repoFilter}
                onChange={(event) => setRepoFilter(event.target.value)}
                placeholder="Filtrer repo"
              />
            </div>
            <select
              className="h-9 w-full border border-border bg-background px-2 text-sm"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              {STATUSES.map((item) => (
                <option key={item || "all"} value={item}>{item || "tous statuts"}</option>
              ))}
            </select>
          </div>
          <div className="max-h-[calc(100vh-21rem)] overflow-y-auto p-2">
            {loading && <Spinner className="m-4" />}
            {error && <div className="p-3 text-sm text-destructive">{error}</div>}
            {!loading && grouped.length === 0 && (
              <div className="p-3 text-sm text-text-secondary">Aucune session.</div>
            )}
            {grouped.map(([repoName, items]) => (
              <div key={repoName} className="mb-3">
                <div className="px-2 py-1 text-xs font-medium uppercase text-text-tertiary">{repoName}</div>
                {items.map((item) => (
                  <button
                    key={item.id}
                    className={cn(
                      "flex w-full flex-col gap-1 border border-transparent px-3 py-2 text-left text-sm hover:bg-surface",
                      selected?.id === item.id && "border-border bg-surface",
                    )}
                    onClick={() => setSelected(item)}
                  >
                    <span className="truncate font-medium">{item.title}</span>
                    <span className="flex items-center gap-2 text-xs text-text-secondary">
                      <span>{item.workflow}</span>
                      <span>{item.status}</span>
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </aside>

        <main className="min-w-0 p-5">
          {!selected ? (
            <div className="flex h-full items-center justify-center text-sm text-text-secondary">
              Sélectionne ou crée une session.
            </div>
          ) : (
            <div className="mx-auto max-w-5xl space-y-5">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
                <div className="min-w-0">
                  <h1 className="truncate text-2xl font-semibold">{selected.title}</h1>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Badge>{selected.status}</Badge>
                    <Badge tone="secondary">{selected.workflow}</Badge>
                    {selected.repo && <Badge tone="secondary">{selected.repo}</Badge>}
                  </div>
                </div>
                <div className="flex gap-2">
                  {resumeInChatEnabled && selected.hermes_session_id && (
                    <Button
                      onClick={() => resumeInChat(selected)}
                      outlined
                      size="sm"
                      title="Reprendre dans le terminal"
                    >
                      <Play className="h-4 w-4" />
                      Terminal
                    </Button>
                  )}
                  {telegram && (
                    <Button
                      onClick={() => resumeInTelegram(selected)}
                      size="sm"
                      title="Reprendre dans Telegram"
                    >
                      <MessageCircle className="h-4 w-4" />
                      Reprendre dans Telegram
                    </Button>
                  )}
                  <Button onClick={() => void deleteSession(selected)} destructive size="sm">
                    <Trash2 className="h-4 w-4" />
                    Supprimer
                  </Button>
                </div>
              </div>

              <section className="grid gap-3 text-sm md:grid-cols-2">
                {[
                  ["ID", selected.id],
                  ["Repo", selected.repo],
                  ["Provider", selected.provider],
                  ["Task Cockpit", selected.cockpit_task_id],
                  ["Branche", selected.git_branch],
                  ["PR", selected.pr_url],
                  ["Preview", selected.preview_url],
                  ["Live", selected.live_url],
                  ["Dernière activité", formatTime(selected.updated_at)],
                ].map(([label, value]) => (
                  <div key={label || ""} className="border-b border-border pb-2">
                    <div className="text-xs uppercase text-text-tertiary">{label}</div>
                    <div className="break-words">{value || "non défini"}</div>
                  </div>
                ))}
              </section>

              <section className="space-y-3">
                <h2 className="text-sm font-semibold uppercase text-text-secondary">État de reprise</h2>
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <div className="text-xs uppercase text-text-tertiary">Objectif</div>
                    <p className="mt-1 whitespace-pre-wrap text-sm">{selected.objective || "non défini"}</p>
                  </div>
                  <div>
                    <div className="text-xs uppercase text-text-tertiary">État actuel</div>
                    <p className="mt-1 whitespace-pre-wrap text-sm">{selected.current_state || selected.summary || "non défini"}</p>
                  </div>
                </div>
                <div>
                  <div className="text-xs uppercase text-text-tertiary">Prochaines actions</div>
                  {(selected.next_actions || []).length ? (
                    <ul className="mt-1 list-disc pl-5 text-sm">
                      {(selected.next_actions || []).map((item) => <li key={item}>{item}</li>)}
                    </ul>
                  ) : (
                    <p className="mt-1 text-sm text-text-secondary">Aucune action enregistrée.</p>
                  )}
                </div>
              </section>

              <section className="space-y-2">
                <h2 className="text-sm font-semibold uppercase text-text-secondary">Resume Packet</h2>
                <pre className="max-h-96 overflow-auto border border-border bg-surface p-3 text-xs">
                  {resumePacket ? JSON.stringify(resumePacket, null, 2) : "Chargement..."}
                </pre>
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
