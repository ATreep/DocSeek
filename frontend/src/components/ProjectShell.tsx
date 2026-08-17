import {
  ChangeEvent,
  FormEvent,
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  BrainCircuit,
  Bug,
  ChevronDown,
  FilePlus2,
  FolderOpen,
  FolderPlus,
  FolderTree,
  MessageSquareText,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Search,
  Settings2,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { Project, Property, PropertyImportConfirmResult, PropertyUploadResult, request, streamRequest, logout } from "../api";
import GraphCanvas from "./GraphCanvas";
import PropertyOverlay from "./PropertyOverlay";
import PropertyRenameDialog from "./PropertyRenameDialog";
import RegroupPropertiesDialog from "./RegroupPropertiesDialog";
import ImportPropertyDialog from "./ImportPropertyDialog";
import ProcessingErrorDialog from "./ProcessingErrorDialog";
import RelationOverlay from "./RelationOverlay";
import ProjectEmptyState from "./ProjectEmptyState";
import EntityOverlay, { type Entity } from "./EntityOverlay";
import AccountMenu from "./AccountMenu";
import LanguageSwitcher from "./LanguageSwitcher";
import type { ChatCitation, ChatMessage } from "./AIQueryChat";
import PropertyTree from "./PropertyTree";
import SettingsPanel from "./SettingsPanel";
import { canLeaveProject, closeMcpBeforeSwitch } from "../project-switch";
import { useLanguage } from "../i18n";
import {
  applyAIQueryEvent,
  type AIQueryStreamEvent,
} from "../ai-query-stream";
import { resolveAIQueryCitation } from "../ai-query-citation";
import {
  processingElapsedLabel,
  processingRefreshKey,
  processingStageLabel,
  shouldRefreshProjectCatalog,
} from "../processing-status";
import { entitiesOwnedByProperty } from "../property-entities";
import {
  cancelProcessing as cancelProcessingRequest,
  retryProcessing as retryProcessingRequest,
} from "../processing-actions";
import {
  relationsForNode,
  type GraphData,
  type GraphRelationDetail,
} from "../graph-relations";

const AIQueryChat = lazy(() => import("./AIQueryChat"));

type Graph = GraphData;
type ProcessingStatus = {
  locked: boolean;
  status: string;
  stage?: string | null;
  stage_started_at?: string | null;
  stage_detail?: string | null;
  timings?: Record<string, number>;
  error?: string | null;
  error_detail?: string | null;
  job_id?: string | null;
  candidate_snapshot?: string | null;
  active_snapshot?: string | null;
};
type PropertyRenameTarget = {
  projectId: string;
  propertyId: string;
  currentFilename: string;
  defaultFilename: string;
};
type PropertyImportTarget = {
  projectId: string;
  importId: string;
  originalFilename: string;
  defaultFilename: string;
};

export default function ProjectShell({ onLogout }: { onLogout: () => void }) {
  const { t } = useLanguage();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectCatalogLoaded, setProjectCatalogLoaded] = useState(false);
  const [project, setProject] = useState<Project | null>(null);
  const [properties, setProperties] = useState<Property[]>([]);
  const [graph, setGraph] = useState<Graph>({ nodes: [], edges: [] });
  const [entityGraph, setEntityGraph] = useState<Graph>({
    nodes: [],
    edges: [],
  });
  const [tab, setTab] = useState<"property" | "entity" | "query">("property");
  const [selected, setSelected] = useState<Property | null>(null);
  const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null);
  const [selectedRelation, setSelectedRelation] = useState<GraphRelationDetail | null>(null);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<{
    properties: any[];
    entities: any[];
  } | null>(null);
  const [question, setQuestion] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatHistoryProjectId, setChatHistoryProjectId] = useState<string | null>(null);
  const [mcpOpen, setMcpOpen] = useState(false);
  const [mcpEndpoint, setMcpEndpoint] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [importTarget, setImportTarget] = useState<PropertyImportTarget | null>(null);
  const [renameTarget, setRenameTarget] = useState<PropertyRenameTarget | null>(null);
  const [regroupOpen, setRegroupOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [processingErrorOpen, setProcessingErrorOpen] = useState(false);
  const [processingStatus, setProcessingStatus] =
    useState<ProcessingStatus | null>(null);
  const processingViewRef = useRef<string | null>(null);
  const replacementInput = useRef<HTMLInputElement>(null);

  async function refreshProjects() {
    const data = await request<Project[]>("/projects");
    setProjects(data);
    setProject((current) => {
      if (!current) return data[0] || null;
      return data.find((item) => item.id === current.id) || current;
    });
    setProjectCatalogLoaded(true);
  }
  async function refreshProject(snapshotView?: "candidate") {
    if (!project) return;
    const graphQuery = snapshotView ? "?snapshot=candidate" : "";
    const [props, pg, eg] = await Promise.all([
      request<Property[]>(`/projects/${project.id}/properties`),
      request<Graph>(`/projects/${project.id}/graphs/property${graphQuery}`),
      request<Graph>(`/projects/${project.id}/graphs/entity${graphQuery}`),
    ]);
    setProperties(props);
    setGraph((current) => (current.snapshot_id === pg.snapshot_id ? current : pg));
    setEntityGraph((current) => (current.snapshot_id === eg.snapshot_id ? current : eg));
  }
  useEffect(() => {
    refreshProjects().catch((err) => setError(err.message));
  }, []);
  useEffect(() => {
    refreshProject().catch((err) => setError(err.message));
  }, [project?.id]);
  useEffect(() => {
    setChatMessages([]);
    setChatHistoryProjectId(null);
  }, [project?.id]);
  useEffect(() => {
    if (!project || tab !== "query" || chatHistoryProjectId === project.id) return;
    let disposed = false;
    const projectId = project.id;
    request<{ messages: ChatMessage[] }>(`/projects/${projectId}/ai-query/history`)
      .then(({ messages }) => {
        if (disposed) return;
        setChatMessages(messages);
        setChatHistoryProjectId(projectId);
      })
      .catch((err) => {
        if (disposed) return;
        setChatHistoryProjectId(projectId);
        setError(err instanceof Error ? err.message : t("Unable to load AI Query history"));
      });
    return () => {
      disposed = true;
    };
  }, [project?.id, tab, chatHistoryProjectId]);
  useEffect(() => {
    if (tab !== "entity") setSelectedEntity(null);
    if (tab !== "property") setSelected(null);
    setSelectedRelation(null);
  }, [tab]);
  useEffect(() => {
    if (!project) {
      setProcessingStatus(null);
      return;
    }
    let disposed = false;
    const refresh = async () => {
      try {
        const status = await request<ProcessingStatus>(
          `/projects/${project.id}/processing`,
        );
        if (!disposed) {
          setProcessingStatus(status);
          const candidateView = status.locked && status.candidate_snapshot;
          const refreshKey = processingRefreshKey(status);
          if (processingViewRef.current !== refreshKey) {
            processingViewRef.current = refreshKey;
            await refreshProject(candidateView ? "candidate" : undefined);
            if (shouldRefreshProjectCatalog(status)) {
              await refreshProjects();
            }
          }
        }
      } catch {
        /* capability-restricted users still retain the local status indicator */
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 800);
    return () => {
      disposed = true;
      processingViewRef.current = null;
      window.clearInterval(timer);
    };
  }, [project?.id]);
  const processing = useMemo(
    () =>
      Boolean(
        project?.processing ||
        processingStatus?.locked ||
        properties.some(
          (item) => item.status === "queued" || item.status === "removing",
        ),
      ),
    [project, processingStatus, properties],
  );
  const processingElapsed = processingElapsedLabel(
    processingStatus?.stage_started_at,
  );
  const selectedPropertyEntities = useMemo(() => {
    if (!selected) return [];
    return entitiesOwnedByProperty(selected.id, entityGraph.nodes as Entity[]);
  }, [entityGraph.nodes, selected?.id]);
  const selectedPropertyRelations = useMemo(
    () => selected ? relationsForNode(graph, selected.id, "property") : [],
    [graph.edges, graph.nodes, selected?.id],
  );
  const selectedEntityRelations = useMemo(
    () => selectedEntity ? relationsForNode(entityGraph, selectedEntity.id, "entity") : [],
    [entityGraph.edges, entityGraph.nodes, selectedEntity?.id],
  );

  async function createProject(providedName?: string) {
    if (!canLeaveProject(processing)) {
      setError(t("Project navigation is locked while the candidate build is processing."));
      return false;
    }
    const name = providedName ?? window.prompt(t("Project name"));
    if (!name?.trim()) return false;
    try {
      const created = await request<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ name: name.trim() }),
      });
      setProjects((current) => [...current, created]);
      setProject(created);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to create project"));
      return false;
    }
  }
  async function signOut() {
    await logout();
    onLogout();
  }
  async function runSearch(event: FormEvent) {
    event.preventDefault();
    if (!project || !search.trim()) return;
    setBusy(true);
    try {
      setResults(
        await request(`/projects/${project.id}/search`, {
          method: "POST",
          body: JSON.stringify({ query: search }),
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Search failed"));
    } finally {
      setBusy(false);
    }
  }
  async function runQuery(event: FormEvent) {
    event.preventDefault();
    const submittedQuestion = question.trim();
    if (!project || !submittedQuestion) return;
    const history = chatMessages
      .filter((message) => !message.streaming && message.content.trim())
      .map(({ role, content }) => ({ role, content }));
    setBusy(true);
    setChatMessages((current) => [
      ...current,
      { role: "user", content: submittedQuestion },
      { role: "assistant", content: "", streaming: true },
    ]);
    setQuestion("");
    try {
      await streamRequest<AIQueryStreamEvent>(`/projects/${project.id}/ai-query/stream`, {
          method: "POST",
          body: JSON.stringify({ query: submittedQuestion, history }),
        }, (streamEvent) => {
          setChatMessages((current) =>
            applyAIQueryEvent(current, streamEvent),
          );
          if (streamEvent.type === "error") setError(streamEvent.message);
        });
    } catch (err) {
      const message = err instanceof Error ? err.message : t("AI Query failed");
      setChatMessages((current) =>
        applyAIQueryEvent(current, { type: "error", message }),
      );
      setError(message);
    } finally {
      setBusy(false);
    }
  }
  async function clearChatHistory() {
    if (!project) return;
    setBusy(true);
    try {
      await request<void>(`/projects/${project.id}/ai-query/history`, {
        method: "DELETE",
      });
      setChatMessages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to clear AI Query history"));
    } finally {
      setBusy(false);
    }
  }
  async function submitUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!project) return;
    const form = new FormData(event.currentTarget);
    const uploadedFile = form.get("file");
    const currentFilename = uploadedFile instanceof File ? uploadedFile.name : "property";
    setBusy(true);
    try {
      const result = await request<PropertyUploadResult>(`/projects/${project.id}/properties`, {
        method: "POST",
        body: form,
      });
      setUploadOpen(false);
      setImportTarget({
        projectId: project.id,
        importId: result.import_id,
        originalFilename: result.original_filename || currentFilename,
        defaultFilename: result.suggested_filename || currentFilename,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Upload failed"));
    } finally {
      setBusy(false);
    }
  }
  async function cancelPropertyImport() {
    if (!importTarget) return;
    setBusy(true);
    try {
      await request(
        `/projects/${importTarget.projectId}/property-imports/${importTarget.importId}`,
        { method: "DELETE" },
      );
      setImportTarget(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to cancel property import"));
    } finally {
      setBusy(false);
    }
  }
  async function confirmPropertyImport(filename: string) {
    if (!importTarget) return;
    setBusy(true);
    try {
      await request<PropertyImportConfirmResult>(
        `/projects/${importTarget.projectId}/property-imports/${importTarget.importId}/confirm`,
        { method: "POST", body: JSON.stringify({ filename }) },
      );
      setImportTarget(null);
      await refreshProject();
      await refreshProjects();
      await refreshProcessingState();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Property import failed"));
    } finally {
      setBusy(false);
    }
  }
  async function toggleMcp() {
    if (!project) return;
    try {
      const result = await request<{ endpoint?: string }>(
        `/projects/${project.id}/mcp/${mcpOpen ? "close" : "open"}`,
        { method: "POST" },
      );
      setMcpOpen(!mcpOpen);
      setMcpEndpoint(result.endpoint || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("MCP action failed"));
    }
  }
  async function switchProject(nextProject: Project | null) {
    if (importTarget) {
      setError(t("Confirm or cancel the pending property import before switching projects."));
      return;
    }
    if (!canLeaveProject(processing)) {
      setError(t("Project switching is locked while the candidate build is processing."));
      return;
    }
    try {
      await closeMcpBeforeSwitch(project?.id, mcpOpen, request);
      setMcpOpen(false);
      setMcpEndpoint("");
      setSelected(null);
      setRenameTarget(null);
      setRegroupOpen(false);
      setProject(nextProject);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to switch project"));
    }
  }
  async function cancelJob() {
    if (!project) return;
    try {
      await cancelProcessingRequest(project.id, request);
      await refreshProcessingState();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("Unable to cancel processing"),
      );
    }
  }
  async function retryJob(propertyId: string) {
    if (!project) return;
    try {
      await retryProcessingRequest(project.id, propertyId, request);
      await refreshProject();
      await refreshProcessingState();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("Unable to retry processing"),
      );
    }
  }
  async function refreshProcessingState() {
    if (!project) return;
    try {
      setProcessingStatus(
        await request<ProcessingStatus>(`/projects/${project.id}/processing`),
      );
    } catch {
      /* handled by the polling loop */
    }
  }
  async function renameProject() {
    if (!project) return;
    const name = window.prompt(t("Project name"), project.name);
    if (!name?.trim()) return;
    try {
      const updated = await request<Project>(`/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      });
      setProject(updated);
      await refreshProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Project rename failed"));
    }
  }
  async function deleteProject() {
    if (importTarget) {
      setError(t("Confirm or cancel the pending property import before closing this project."));
      return;
    }
    if (!canLeaveProject(processing)) {
      setError(t("Project closing is locked while the candidate build is processing."));
      return;
    }
    if (
      !project ||
      !window.confirm(
        t("Delete {name}? This permanently removes its files and graph.", { name: project.name }),
      )
    )
      return;
    try {
      await request(`/projects/${project.id}`, { method: "DELETE" });
      setProject(null);
      setSelected(null);
      setRenameTarget(null);
      setMcpOpen(false);
      setMcpEndpoint("");
      await refreshProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Project delete failed"));
    }
  }
  function openPropertyRename() {
    const projectId = selected?.project_id || project?.id;
    if (!selected || !projectId) return;
    setRenameTarget({
      projectId,
      propertyId: selected.id,
      currentFilename: selected.filename,
      defaultFilename: selected.filename,
    });
  }
  async function renameProperty(filename: string) {
    if (!renameTarget) return;
    setBusy(true);
    try {
      const updated = await request<Property>(
        `/projects/${renameTarget.projectId}/properties/${renameTarget.propertyId}`,
        { method: "PATCH", body: JSON.stringify({ filename }) },
      );
      setSelected((current) => current?.id === updated.id ? updated : current);
      setRenameTarget(null);
      await refreshProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Property rename failed"));
    } finally {
      setBusy(false);
    }
  }
  async function regroupProperties(revisionPrompt: string) {
    if (!project) return;
    setBusy(true);
    try {
      await request(
        `/projects/${project.id}/properties/regroup`,
        {
          method: "POST",
          body: JSON.stringify({ revision_prompt: revisionPrompt }),
        },
      );
      setSelected(null);
      setRegroupOpen(false);
      await Promise.all([refreshProject(), refreshProjects()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Property re-grouping failed"));
    } finally {
      setBusy(false);
    }
  }
  async function removeProperty() {
    if (
      !selected ||
      !window.confirm(t("Permanently remove {filename}?", { filename: selected.filename }))
    )
      return;
    try {
      await request(
        `/projects/${selected.project_id}/properties/${selected.id}`,
        { method: "DELETE" },
      );
      setSelected(null);
      await refreshProject();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Property removal failed"));
    }
  }
  function openReplacement() {
    replacementInput.current?.click();
  }
  async function replaceProperty(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !selected) return;
    const form = new FormData();
    form.append("file", file);
    setBusy(true);
    try {
      await request(
        `/projects/${selected.project_id}/properties/${selected.id}/content`,
        { method: "PUT", body: form },
      );
      await refreshProject();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("Property replacement failed"),
      );
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }
  function selectProperty(property: Property | null) {
    setSelectedEntity(null);
    setSelectedRelation(null);
    setSelected(property);
  }

  function selectEntity(node: Entity) {
    const sourceProperties = (node.source_property_ids || [])
      .flatMap((sourceId) => {
        const source = graph.nodes.find((property) => property.id === sourceId) ||
          properties.find((property) => property.id === sourceId);
        if (!source) return [];
        return [{
          id: source.id,
          filename: source.filename,
          property_type: source.property_type,
          definition: typeof source.definition === "string" ? source.definition : undefined,
        }];
      });
    setSelected(null);
    setSelectedRelation(null);
    setSelectedEntity({ ...node, source_properties: sourceProperties });
  }

  function selectRelation(relation: GraphRelationDetail) {
    setSelected(null);
    setSelectedEntity(null);
    setSelectedRelation(relation);
  }

  function selectQueryCitation(citation: ChatCitation) {
    const resolved = resolveAIQueryCitation(
      citation,
      properties,
      entityGraph.nodes as Entity[],
    );
    if (!resolved) return;
    if (resolved.kind === "property") {
      selectProperty(resolved.value);
      return;
    }
    selectEntity(resolved.value);
  }

  const failedProperty = properties.find((item) => item.status === "failed");
  const showProjectCreation = projectCatalogLoaded && !project && projects.length === 0;
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark">
          <BrainCircuit size={18} />
          <span>DOCSEEK</span>
        </div>
        <div className="project-select">
          <FolderOpen size={16} />
          <select
            value={project?.id || ""}
            disabled={processing}
            title={processing ? t("Project navigation is locked while processing") : t("Select project")}
            onChange={(event) =>
              switchProject(
                projects.find((item) => item.id === event.target.value) || null,
              )
            }
          >
            <option value="">{t("Select project")}</option>
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          <ChevronDown size={14} />
        </div>
        <form className="global-search" onSubmit={runSearch}>
          <Search size={16} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("Search properties and entities")}
          />
          <kbd>⌘ K</kbd>
        </form>
        <div className="top-actions">
          <button
            type="button"
            className="text-button top-create-button"
            aria-label={t("Create project")}
            title={t("Create project")}
            disabled={processing}
            onClick={() => void createProject()}
          >
            <FolderPlus size={15} /> {t("New project")}
          </button>
          <button
            type="button"
            className={`mcp-button ${mcpOpen ? "active" : ""}`}
            onClick={toggleMcp}
          >
            <Network size={15} /> {mcpOpen ? t("MCP open") : t("MCP closed")}
          </button>
          <button
            type="button"
            className="icon-button"
            title={t("Settings")}
            aria-label={t("Settings")}
            onClick={() => setSettingsOpen(true)}
          >
            <Settings2 size={17} />
          </button>
          <LanguageSwitcher />
          <AccountMenu onSignOut={signOut} />
        </div>
      </header>
      {mcpOpen && (
        <div className="mcp-warning">
          <strong>{t("MCP endpoint is publicly callable.")}</strong>
          <span>
            {t("It inherits this user\u2019s current capabilities until closed.")}
          </span>
          {mcpEndpoint && <code>{mcpEndpoint}</code>}
        </div>
      )}
      {processingStatus &&
        (processingStatus.locked ||
          processingStatus.status === "failed" ||
          processingStatus.status === "cancelled") && (
          <div
            className={`processing-banner ${processingStatus.status === "failed" ? "failed" : ""}`}
          >
            <div>
              <strong>
                {processingStageLabel(processingStatus.stage || processingStatus.status)}
              </strong>
              <span>
                {processingStatus.stage_detail ||
                  (processingStatus.locked
                    ? t("Project actions are locked until this stage completes.")
                    : t("Build finished."))}
                {processingStatus.locked && processingElapsed
                  ? ` · ${processingElapsed}`
                  : ""}
                {processingStatus.error ? ` · ${processingStatus.error}` : ""}
              </span>
            </div>
            <div className="processing-actions">
              {processingStatus.status === "failed" && (
                <button
                  type="button"
                  className="text-button"
                  aria-label={t("Show error detail")}
                  onClick={() => setProcessingErrorOpen(true)}
                >
                  <Bug size={14} /> {t("Show error detail")}
                </button>
              )}
              {processingStatus.locked && (
                <button
                  type="button"
                  className="text-button"
                  onClick={cancelJob}
                >
                  {t("Cancel")}
                </button>
              )}
              {failedProperty && (
                <button
                  type="button"
                  className="text-button"
                  onClick={() => retryJob(failedProperty.id)}
                >
                  {t("Retry")}
                </button>
              )}
            </div>
          </div>
        )}
      <div className={`workspace ${sidebarCollapsed ? "workspace-sidebar-collapsed" : ""}`}>
        <aside className={`property-sidebar ${sidebarCollapsed ? "collapsed" : ""}`}>
          <div className="sidebar-heading">
            <div>
              <span className="eyebrow">{t("PROJECT PROPERTIES")}</span>
              <h2>{project?.name || t("No project selected")}</h2>
            </div>
            <button
              type="button"
              className="icon-button"
              title={sidebarCollapsed ? t("Expand panel") : t("Collapse panel")}
              aria-label={sidebarCollapsed ? t("Expand panel") : t("Collapse panel")}
              aria-expanded={!sidebarCollapsed}
              onClick={() => setSidebarCollapsed((current) => !current)}
            >
              {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>
          </div>
          <div className="tree-actions">
            <button
              className="primary-button compact"
              disabled={!project || processing}
              onClick={() => setUploadOpen(true)}
            >
              <FilePlus2 size={15} /> {t("Add property")}
            </button>
            {project && (
              <>
                <button
                  type="button"
                  className="icon-button"
                  title={t("Re-group properties")}
                  aria-label={t("Re-group properties")}
                  disabled={processing || busy || properties.length === 0}
                  onClick={() => setRegroupOpen(true)}
                >
                  <FolderTree size={16} />
                </button>
                <button
                  className="icon-button"
                  title={t("Rename project")}
                  onClick={renameProject}
                >
                  <Pencil size={16} />
                </button>
                <button
                  className="icon-button danger"
                  title={t("Delete project")}
                  onClick={deleteProject}
                  disabled={processing}
                >
                  <Trash2 size={16} />
                </button>
              </>
            )}
          </div>
          <div className="property-tree">
            {properties.length ? (
              <PropertyTree properties={properties} onSelect={selectProperty} />
            ) : (
              <div className="tree-empty">
                {t("No properties yet.")}
                <br />
                {t("Add a file to begin.")}
              </div>
            )}
          </div>
          <div className="sidebar-footer">
            <span className={`connection-dot ${processing ? "busy" : ""}`} />{" "}
            {processing
              ? processingStageLabel(processingStatus?.stage || "queued")
              : t("Active snapshot ready")}
          </div>
        </aside>
        <main className="center-workspace">
          {showProjectCreation ? <ProjectEmptyState busy={busy} onCreate={createProject} /> : <><div className="tab-row">
            <div className="tabs">
              <button
                type="button"
                className={tab === "entity" ? "active" : ""}
                onClick={() => setTab("entity")}
              >
                <Network size={15} /> {t("Entity Graph")}
              </button>
              <button
                type="button"
                className={tab === "property" ? "active" : ""}
                onClick={() => setTab("property")}
              >
                <FolderOpen size={15} /> {t("Property Graph")}
              </button>
              <button
                type="button"
                className={tab === "query" ? "active" : ""}
                onClick={() => setTab("query")}
              >
                <MessageSquareText size={15} /> {t("AI Query")}
              </button>
            </div>
            <div className="tab-status">
              {processing && (
                <>
                  <span className="spinner" /> {processingStageLabel(processingStatus?.stage || "queued")}
                </>
              )}
            </div>
          </div>
          {error && (
            <div className="error-banner">
              {error}
              <button type="button" onClick={() => setError("")}>
                <X size={14} />
              </button>
            </div>
          )}
          {tab === "property" && (
            <GraphCanvas
              graph={graph}
              kind="property"
              onRelationSelect={selectRelation}
              onSelect={(node) => {
                selectProperty(
                  properties.find((item) => item.id === node.id) || null,
                );
              }}
            />
          )}
          {tab === "entity" && (
            <GraphCanvas
              graph={entityGraph}
              kind="entity"
              onSelect={selectEntity}
              onRelationSelect={selectRelation}
            />
          )}
          {tab === "query" && (
            <Suspense fallback={<div className="chat-loading"><span className="spinner" /></div>}>
              <AIQueryChat question={question} messages={chatMessages} busy={busy} onQuestionChange={setQuestion} onSubmit={runQuery} onClear={clearChatHistory} onCitationSelect={selectQueryCitation} />
            </Suspense>
          )}</>}
        </main>
      </div>
      <PropertyOverlay
        property={selected}
        entities={selectedPropertyEntities}
        relations={selectedPropertyRelations}
        locked={processing}
        onClose={() => setSelected(null)}
        onRename={openPropertyRename}
        onReplace={openReplacement}
        onRemove={removeProperty}
        onEntitySelect={selectEntity}
        onRelationSelect={selectRelation}
      />
      <PropertyRenameDialog
        open={Boolean(renameTarget)}
        currentFilename={renameTarget?.currentFilename || ""}
        defaultFilename={renameTarget?.defaultFilename || ""}
        busy={busy || processing}
        onClose={() => setRenameTarget(null)}
        onSubmit={renameProperty}
      />
      <ImportPropertyDialog
        open={Boolean(importTarget)}
        originalFilename={importTarget?.originalFilename || ""}
        defaultFilename={importTarget?.defaultFilename || ""}
        busy={busy}
        onCancel={cancelPropertyImport}
        onConfirm={confirmPropertyImport}
      />
      <RegroupPropertiesDialog
        open={regroupOpen}
        busy={busy || processing}
        onClose={() => setRegroupOpen(false)}
        onSubmit={regroupProperties}
      />
      <EntityOverlay
        entity={selectedEntity}
        relations={selectedEntityRelations}
        onRelationSelect={selectRelation}
        onClose={() => setSelectedEntity(null)}
      />
      <RelationOverlay
        relation={selectedRelation}
        onClose={() => setSelectedRelation(null)}
      />
      <ProcessingErrorDialog
        open={processingErrorOpen && processingStatus?.status === "failed"}
        summary={processingStatus?.error}
        detail={processingStatus?.error_detail}
        onClose={() => setProcessingErrorOpen(false)}
      />
      <input
        ref={replacementInput}
        type="file"
        hidden
        onChange={replaceProperty}
      />
      {results && (
        <div className="search-drawer">
          <div className="overlay-header">
            <div>
              <span className="eyebrow">{t("SEARCH RESULTS")}</span>
              <h2>{search}</h2>
            </div>
            <button
              type="button"
              className="icon-button"
              onClick={() => setResults(null)}
            >
              <X size={16} />
            </button>
          </div>
          <section>
            <h3>
              {t("Properties")} <span>{results.properties.length}</span>
            </h3>
            {results.properties.map((item) => (
              <button
                type="button"
                className="result-row"
                key={item.id}
                onClick={() => {
                  selectProperty(
                    properties.find((property) => property.id === item.id) ||
                      null,
                  );
                  setResults(null);
                }}
              >
                {item.filename}
                <small>{item.score}</small>
              </button>
            ))}
          </section>
          <section>
            <h3>
              {t("Entities")} <span>{results.entities.length}</span>
            </h3>
            {results.entities.map((item) => (
              <button
                type="button"
                className="result-row"
                key={item.id}
                onClick={() => {
                  setTab("entity");
                  setResults(null);
                }}
              >
                {item.name}
                <small>{item.score}</small>
              </button>
            ))}
          </section>
        </div>
      )}
      {uploadOpen && (
        <div className="modal-backdrop">
          <form className="upload-modal" onSubmit={submitUpload}>
            <div className="overlay-header">
              <div>
                <span className="eyebrow">{t("INGEST PROPERTY")}</span>
                <h2>{t("Add to {name}", { name: project?.name || "" })}</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setUploadOpen(false)}
              >
                <X size={16} />
              </button>
            </div>
            <label className="dropzone">
              <Upload size={24} />
              <strong>{t("Choose a file")}</strong>
              <span>{t("Text, code, documents, or images")}</span>
              <input type="file" name="file" required />
            </label>
            <label>
              {t("Optional context")}
              <textarea
                name="comment"
                rows={3}
                placeholder={t("Add document details or grouping context")}
              />
            </label>
            <button className="primary-button" disabled={busy}>
              <Upload size={15} /> {t("Prepare import")}
            </button>
          </form>
        </div>
      )}
      {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
