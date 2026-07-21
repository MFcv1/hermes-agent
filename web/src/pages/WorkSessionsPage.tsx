import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  BriefcaseBusiness,
  Github,
  MessageCircle,
  Play,
  RefreshCw,
  Search,
  Sparkles,
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

interface WorkSession {
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
  metadata?: Record<string, unknown>;
  updated_at: number;
  created_at: number;
}

interface GitHubRepository {
  name: string;
  nameWithOwner: string;
  description: string;
  isPrivate: boolean;
  url: string;
  updatedAt: string;
}

interface WorkSessionsResponse {
  work_sessions: WorkSession[];
}

interface RepositoryCatalogResponse {
  repositories: GitHubRepository[];
  owner: string;
  total: number;
}

interface ResumePacketResponse {
  resume_packet: Record<string, unknown>;
}

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

const STATUSES = ["", "open", "active", "blocked", "done", "failed", "archived"];
const PROVIDERS = [
  { id: "cloudflare", label: "Cloudflare" },
  { id: "supabase", label: "Supabase" },
];

function isTelegramMiniApp(): boolean {
  return typeof window !== "undefined" && Boolean(window.Telegram?.WebApp?.sendData);
}

function sendTelegramAction(payload: Record<string, unknown>): boolean {
  const webApp = window.Telegram?.WebApp;
  if (!webApp?.sendData) return false;
  webApp.sendData(JSON.stringify(payload));
  return true;
}

function formatTime(ts: number): string {
  if (!ts) return "jamais";
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
  const telegram = isTelegramMiniApp();
  const resumeInChatEnabled = isDashboardEmbeddedChatEnabled();
  const [sessions, setSessions] = useState<WorkSession[]>([]);
  const [repositories, setRepositories] = useState<GitHubRepository[]>([]);
  const [githubOwner, setGithubOwner] = useState("");
  const [selected, setSelected] = useState<WorkSession | null>(null);
  const [resumePacket, setResumePacket] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [repoFilter, setRepoFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [projectMode, setProjectMode] = useState<"existing" | "new">("existing");
  const [selectedRepo, setSelectedRepo] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [visibility, setVisibility] = useState<"private" | "public">("private");
  const [providers, setProviders] = useState<string[]>(["cloudflare"]);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (repoFilter.trim()) params.set("repo", repoFilter.trim());
      if (statusFilter) params.set("status", statusFilter);
      const qs = params.toString();
      const data = await fetchJSON<WorkSessionsResponse>(`/api/work-sessions${qs ? `?${qs}` : ""}`);
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
  }, [repoFilter, statusFilter]);

  const loadRepositories = useCallback(async () => {
    setCatalogLoading(true);
    setCatalogError("");
    try {
      const data = await fetchJSON<RepositoryCatalogResponse>("/api/project-catalog/github");
      setRepositories(data.repositories);
      setGithubOwner(data.owner);
      setSelectedRepo((current) => current || data.repositories[0]?.nameWithOwner || "");
    } catch (err) {
      setCatalogError(err instanceof Error ? err.message : String(err));
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  useEffect(() => {
    window.Telegram?.WebApp?.ready?.();
    void Promise.all([loadSessions(), loadRepositories()]);
  }, [loadRepositories, loadSessions]);

  useEffect(() => {
    setEnd(
      <Button onClick={() => void Promise.all([loadSessions(), loadRepositories()])} outlined size="sm">
        <RefreshCw className="h-4 w-4" />
        Actualiser
      </Button>,
    );
    return () => setEnd(null);
  }, [loadRepositories, loadSessions, setEnd]);

  useEffect(() => {
    if (!selected) {
      setResumePacket(null);
      return;
    }
    let cancelled = false;
    fetchJSON<ResumePacketResponse>(`/api/work-sessions/${encodeURIComponent(selected.id)}/resume-packet`)
      .then((data) => {
        if (!cancelled) setResumePacket(data.resume_packet);
      })
      .catch(() => {
        if (!cancelled) setResumePacket(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const grouped = useMemo(() => groupByRepo(sessions), [sessions]);
  const repoForCreation = projectMode === "existing"
    ? selectedRepo
    : `${githubOwner ? `${githubOwner}/` : ""}${newProjectName.trim()}`;
  const canCreate = Boolean(repoForCreation && (title.trim() || newProjectName.trim() || selectedRepo));

  function toggleProvider(provider: string) {
    setProviders((current) => current.includes(provider)
      ? current.filter((item) => item !== provider)
      : [...current, provider]);
  }

  async function createSession() {
    if (!canCreate) return;
    const cleanTitle = title.trim()
      || (projectMode === "existing" ? selectedRepo.split("/").pop() : newProjectName.trim())
      || "Nouvelle conversation";
    const payload = {
      action: "work_session.create",
      title: cleanTitle,
      repo: repoForCreation,
      workflow: "libre",
      origin_channel: telegram ? "telegram" : "dashboard",
      objective: objective.trim() || undefined,
      project_mode: projectMode,
      providers: projectMode === "new" ? providers : [],
      visibility,
      metadata: {
        project_mode: projectMode,
        providers: projectMode === "new" ? providers : [],
        visibility,
      },
    };
    if (telegram && sendTelegramAction(payload)) return;
    const data = await fetchJSON<{ work_session: WorkSession }>("/api/work-sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setTitle("");
    setObjective("");
    setNewProjectName("");
    await loadSessions();
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
    sendTelegramAction({ action: "work_session.resume", work_session_id: session.id });
  }

  async function setArchived(session: WorkSession, archived: boolean) {
    const payload = {
      action: archived ? "work_session.archive" : "work_session.unarchive",
      work_session_id: session.id,
    };
    if (telegram && sendTelegramAction(payload)) return;
    await fetchJSON(`/api/work-sessions/${encodeURIComponent(session.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: archived ? "archived" : "open" }),
    });
    await loadSessions();
  }

  async function deleteSession(session: WorkSession) {
    if (!window.confirm(`Supprimer définitivement « ${session.title} » ?`)) return;
    const payload = { action: "work_session.delete", work_session_id: session.id };
    if (telegram && sendTelegramAction(payload)) return;
    await fetchJSON(`/api/work-sessions/${encodeURIComponent(session.id)}`, { method: "DELETE" });
    setSelected(null);
    await loadSessions();
  }

  return (
    <div className="min-h-full bg-background text-text-primary">
      <div className="grid min-h-[calc(100vh-4rem)] grid-cols-1 border-t border-border lg:grid-cols-[22rem_1fr]">
        <aside className="border-b border-border bg-surface/60 lg:border-b-0 lg:border-r">
          <div className="space-y-4 border-b border-border p-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <BriefcaseBusiness className="h-4 w-4 text-text-secondary" />
              Projets
              {telegram && <Badge tone="secondary">Telegram</Badge>}
            </div>

            <div className="grid grid-cols-2 gap-2">
              <Button
                onClick={() => setProjectMode("existing")}
                outlined={projectMode !== "existing"}
                size="sm"
              >
                <Github className="h-4 w-4" />
                Repo existant
              </Button>
              <Button
                onClick={() => setProjectMode("new")}
                outlined={projectMode !== "new"}
                size="sm"
              >
                <Sparkles className="h-4 w-4" />
                Nouveau projet
              </Button>
            </div>

            {projectMode === "existing" ? (
              <div className="space-y-2">
                <label className="text-xs uppercase text-text-tertiary">Dépôt GitHub</label>
                <select
                  className="h-10 w-full border border-border bg-background px-2 text-sm"
                  value={selectedRepo}
                  onChange={(event) => setSelectedRepo(event.target.value)}
                  disabled={catalogLoading}
                >
                  {repositories.map((repo) => (
                    <option key={repo.nameWithOwner} value={repo.nameWithOwner}>
                      {repo.nameWithOwner}{repo.isPrivate ? " · privé" : ""}
                    </option>
                  ))}
                </select>
                {catalogLoading && <div className="text-xs text-text-secondary">Chargement des repos…</div>}
                {catalogError && <div className="text-xs text-destructive">{catalogError}</div>}
              </div>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="text-xs uppercase text-text-tertiary">Nom du nouveau projet</label>
                  <Input
                    value={newProjectName}
                    onChange={(event) => setNewProjectName(event.target.value)}
                    placeholder="mon-nouveau-projet"
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {PROVIDERS.map((provider) => (
                    <label key={provider.id} className="flex items-center gap-2 border border-border px-3 py-2 text-sm">
                      <input
                        type="checkbox"
                        checked={providers.includes(provider.id)}
                        onChange={() => toggleProvider(provider.id)}
                      />
                      {provider.label}
                    </label>
                  ))}
                </div>
                <select
                  className="h-10 w-full border border-border bg-background px-2 text-sm"
                  value={visibility}
                  onChange={(event) => setVisibility(event.target.value as "private" | "public")}
                >
                  <option value="private">Repo privé</option>
                  <option value="public">Repo public</option>
                </select>
              </div>
            )}

            <Input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Titre de la conversation"
            />
            <textarea
              className="min-h-24 w-full resize-y border border-border bg-background px-3 py-2 text-sm"
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              placeholder="Que veux-tu construire ou modifier ?"
            />
            <Button onClick={() => void createSession()} disabled={!canCreate} size="sm" className="w-full">
              <Play className="h-4 w-4" />
              {projectMode === "new" ? "Préparer le projet" : "Nouveau chat"}
            </Button>
            {projectMode === "new" && (
              <p className="text-xs text-text-secondary">
                Hermes préparera GitHub et les fournisseurs sélectionnés dans le chat, avec validation avant création.
              </p>
            )}
          </div>

          <div className="space-y-2 border-b border-border p-4">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-text-tertiary" />
              <Input
                className="pl-8"
                value={repoFilter}
                onChange={(event) => setRepoFilter(event.target.value)}
                placeholder="Filtrer les projets"
              />
            </div>
            <select
              className="h-9 w-full border border-border bg-background px-2 text-sm"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              {STATUSES.map((item) => (
                <option key={item || "all"} value={item}>{item || "toutes les discussions"}</option>
              ))}
            </select>
          </div>

          <div className="max-h-[calc(100vh-31rem)] overflow-y-auto p-2 lg:max-h-[calc(100vh-34rem)]">
            {loading && <Spinner className="m-4" />}
            {error && <div className="p-3 text-sm text-destructive">{error}</div>}
            {!loading && grouped.length === 0 && (
              <div className="p-3 text-sm text-text-secondary">Aucune discussion.</div>
            )}
            {grouped.map(([repoName, items]) => (
              <div key={repoName} className="mb-3">
                <div className="truncate px-2 py-1 text-xs font-medium uppercase text-text-tertiary">{repoName}</div>
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
                      <span>{item.status}</span>
                      <span>{formatTime(item.updated_at)}</span>
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
              Choisis un projet ou crée une discussion.
            </div>
          ) : (
            <div className="mx-auto max-w-5xl space-y-5">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4">
                <div className="min-w-0">
                  <h1 className="truncate text-2xl font-semibold">{selected.title}</h1>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Badge>{selected.status}</Badge>
                    {selected.repo && <Badge tone="secondary">{selected.repo}</Badge>}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {resumeInChatEnabled && selected.hermes_session_id && (
                    <Button onClick={() => resumeInChat(selected)} outlined size="sm">
                      <Play className="h-4 w-4" />
                      Ouvrir le chat
                    </Button>
                  )}
                  {telegram && selected.status !== "archived" && (
                    <Button onClick={() => resumeInTelegram(selected)} size="sm">
                      <MessageCircle className="h-4 w-4" />
                      Reprendre dans Telegram
                    </Button>
                  )}
                  <Button
                    onClick={() => void setArchived(selected, selected.status !== "archived")}
                    outlined
                    size="sm"
                  >
                    {selected.status === "archived"
                      ? <ArchiveRestore className="h-4 w-4" />
                      : <Archive className="h-4 w-4" />}
                    {selected.status === "archived" ? "Réactiver" : "Archiver"}
                  </Button>
                  <Button onClick={() => void deleteSession(selected)} destructive size="sm">
                    <Trash2 className="h-4 w-4" />
                    Supprimer
                  </Button>
                </div>
              </div>

              <section className="grid gap-3 text-sm md:grid-cols-2">
                {[
                  ["Projet", selected.repo],
                  ["Branche", selected.git_branch],
                  ["Pull request", selected.pr_url],
                  ["Preview", selected.preview_url],
                  ["Site live", selected.live_url],
                  ["Dernière activité", formatTime(selected.updated_at)],
                ].map(([label, value]) => (
                  <div key={label || ""} className="border-b border-border pb-2">
                    <div className="text-xs uppercase text-text-tertiary">{label}</div>
                    <div className="break-words">{value || "non défini"}</div>
                  </div>
                ))}
              </section>

              <section className="space-y-3">
                <h2 className="text-sm font-semibold uppercase text-text-secondary">Contexte du travail</h2>
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <div className="text-xs uppercase text-text-tertiary">Objectif</div>
                    <p className="mt-1 whitespace-pre-wrap text-sm">{selected.objective || "non défini"}</p>
                  </div>
                  <div>
                    <div className="text-xs uppercase text-text-tertiary">État actuel</div>
                    <p className="mt-1 whitespace-pre-wrap text-sm">
                      {selected.current_state || selected.summary || "non défini"}
                    </p>
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

              <details className="border-t border-border pt-3">
                <summary className="cursor-pointer text-xs uppercase text-text-tertiary">Détails de reprise</summary>
                <pre className="mt-3 max-h-96 overflow-auto border border-border bg-surface p-3 text-xs">
                  {resumePacket ? JSON.stringify(resumePacket, null, 2) : "Chargement…"}
                </pre>
              </details>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
