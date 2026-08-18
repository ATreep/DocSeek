import {
  FormEvent,
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useDocSeekTranslation } from "../i18n";
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
import { ConfirmedPropertyImport, Project, Property, PropertyImportBatchConfirmResult, PropertyImportBatchItem, PropertyImportBatchResult, type PropertyImportBatchStreamEvent, type RegroupConfirmationItem, type RegroupProposal, request, streamRequest, logout } from "../api";
import GraphCanvas from "./GraphCanvas";
import PropertyOverlay from "./PropertyOverlay";
import PropertyRenameDialog from "./PropertyRenameDialog";
import RegroupPropertiesDialog from "./RegroupPropertiesDialog";
import RegroupConfirmationDialog from "./RegroupConfirmationDialog";
import ImportPropertyDialog from "./ImportPropertyDialog";
import ProviderConfigurationAlert, {
  type MissingProviderRoute,
} from "./ProviderConfigurationAlert";
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
import {
  applyAIQueryEvent,
  type AIQueryStreamEvent,
} from "../ai-query-stream";
import { resolveAIQueryCitation } from "../ai-query-citation";
import {
  processingElapsedLabel,
  processingRefreshKey,
  processingStageDetail,
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
import type { GraphDisplaySelection } from "../graph-display-filter";

const AIQueryChat = lazy(() => import("./AIQueryChat"));

function droppedPropertyFiles(dataTransfer: DataTransfer): File[] {
  const items = Array.from(dataTransfer.items || []);
  if (!items.length) return Array.from(dataTransfer.files || []);

  return items.flatMap((item) => {
    if (item.kind !== "file") return [];
    const entry = item.webkitGetAsEntry?.();
    if (entry?.isDirectory) return [];
    const file = item.getAsFile();
    return file ? [file] : [];
  });
}

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
  llm_response?: string | null;
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
  batchId: string;
  items: PropertyImportBatchItem[];
};
type ImportPreparationProgress = {
  phase: "uploading" | "generating" | "naming";
  index: number;
  total: number;
  filename: string;
};
type ImportProviderReadiness = {
  ready: boolean;
  missing_routes: MissingProviderRoute[];
  can_configure: boolean;
};

export default function ProjectShell({ onLogout }: { onLogout: () => void }) {
  const { t } = useDocSeekTranslation();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectCatalogLoaded, setProjectCatalogLoaded] = useState(false);
  const [project, setProject] = useState<Project | null>(null);
  const [properties, setProperties] = useState<Property[]>([]);
  const [graph, setGraph] = useState<Graph>({ nodes: [], edges: [] });
  const [entityGraph, setEntityGraph] = useState<Graph>({
    nodes: [],
    edges: [],
  });
  const [propertyGraphDisplaySelection, setPropertyGraphDisplaySelection] =
    useState<GraphDisplaySelection>({ groupPaths: [], propertyIds: [] });
  const [entityGraphDisplaySelection, setEntityGraphDisplaySelection] =
    useState<GraphDisplaySelection>({ groupPaths: [], propertyIds: [] });
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
  const [settingsFocusRoute, setSettingsFocusRoute] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadDragActive, setUploadDragActive] = useState(false);
  const [providerReadiness, setProviderReadiness] =
    useState<ImportProviderReadiness | null>(null);
  const [checkingProviders, setCheckingProviders] = useState(false);
  const [importTarget, setImportTarget] = useState<PropertyImportTarget | null>(null);
  const [regroupProposal, setRegroupProposal] = useState<RegroupProposal | null>(null);
  const [importPreparation, setImportPreparation] =
    useState<ImportPreparationProgress | null>(null);
  const [renameTarget, setRenameTarget] = useState<PropertyRenameTarget | null>(null);
  const [regroupOpen, setRegroupOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [processingErrorOpen, setProcessingErrorOpen] = useState(false);
  const [processingStatus, setProcessingStatus] =
    useState<ProcessingStatus | null>(null);
  const processingViewRef = useRef<string | null>(null);
  const uploadInput = useRef<HTMLInputElement>(null);

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
    setPropertyGraphDisplaySelection({ groupPaths: [], propertyIds: [] });
    setEntityGraphDisplaySelection({ groupPaths: [], propertyIds: [] });
  }, [project?.id]);
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
  const processingDetail = processingStatus?.stage_detail
    ? processingStageDetail(processingStatus.stage, processingStatus.stage_detail)
    : null;
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
  async function beginPropertyImport() {
    setCheckingProviders(true);
    setError("");
    try {
      const readiness = await request<ImportProviderReadiness>(
        "/system/import-provider-readiness",
      );
      if (!readiness.ready) {
        setProviderReadiness(readiness);
        return;
      }
      setUploadFiles([]);
      setUploadDragActive(false);
      setUploadOpen(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : t("Unable to check model provider configuration"),
      );
    } finally {
      setCheckingProviders(false);
    }
  }

  function openProviderSettings() {
    setSettingsFocusRoute(providerReadiness?.missing_routes[0]?.key || null);
    setProviderReadiness(null);
    setSettingsOpen(true);
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
    const selectedFiles = uploadFiles;
    if (!selectedFiles.length) return;
    const formValues = new FormData(event.currentTarget);
    const form = new FormData();
    selectedFiles.forEach((file) => form.append("files", file));
    form.append("comment", String(formValues.get("comment") || ""));
    setBusy(true);
    setImportPreparation({
      phase: "uploading",
      index: 0,
      total: selectedFiles.length,
      filename: "",
    });
    try {
      const completedResults: PropertyImportBatchResult[] = [];
      let streamError = "";
      await streamRequest<PropertyImportBatchStreamEvent>(
        `/projects/${project.id}/property-import-batches/stream`,
        { method: "POST", body: form },
        (streamEvent) => {
          if (streamEvent.type === "batch_started") {
            setImportPreparation((current) => ({
              phase: current?.phase || "uploading",
              index: current?.index || 0,
              filename: current?.filename || "",
              total: streamEvent.total,
            }));
          } else if (streamEvent.type === "file_started") {
            setImportPreparation({
              phase: "generating",
              index: streamEvent.index,
              total: streamEvent.total,
              filename: streamEvent.filename,
            });
          } else if (streamEvent.type === "filename_generation_started") {
            setImportPreparation({
              phase: "naming",
              index: streamEvent.total,
              total: streamEvent.total,
              filename: "",
            });
          } else if (streamEvent.type === "batch_completed") {
            completedResults.push({
              batch_id: streamEvent.batch_id,
              status: streamEvent.status,
              items: streamEvent.items,
            });
          } else if (streamEvent.type === "error") {
            streamError = streamEvent.message;
          }
        },
      );
      if (streamError) throw new Error(streamError);
      const result = completedResults[0];
      if (!result) throw new Error(t("Property preparation ended without a result"));
      setUploadOpen(false);
      setUploadFiles([]);
      setImportTarget({
        projectId: project.id,
        batchId: result.batch_id,
        items: result.items,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Upload failed"));
    } finally {
      setImportPreparation(null);
      setBusy(false);
    }
  }
  async function cancelPropertyImport() {
    if (!importTarget) return;
    setBusy(true);
    try {
      await request(
        `/projects/${importTarget.projectId}/property-import-batches/${importTarget.batchId}`,
        { method: "DELETE" },
      );
      setImportTarget(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to cancel property import"));
    } finally {
      setBusy(false);
    }
  }
  async function confirmPropertyImport(items: ConfirmedPropertyImport[]) {
    if (!importTarget) return;
    setBusy(true);
    try {
      await request<PropertyImportBatchConfirmResult>(
        `/projects/${importTarget.projectId}/property-import-batches/${importTarget.batchId}/confirm`,
        { method: "POST", body: JSON.stringify({ items }) },
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
        `Delete ${project.name}? This permanently removes its files and graph.`,
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
      const proposal = await request<RegroupProposal>(
        `/projects/${project.id}/properties/regroup`,
        {
          method: "POST",
          body: JSON.stringify({ revision_prompt: revisionPrompt }),
        },
      );
      setRegroupOpen(false);
      setRegroupProposal(proposal);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Property re-grouping failed"));
    } finally {
      setBusy(false);
    }
  }
  async function confirmRegroupProperties(items: RegroupConfirmationItem[]) {
    if (!project || !regroupProposal) return;
    setBusy(true);
    try {
      await request(
        `/projects/${project.id}/properties/regroup/confirm`,
        {
          method: "POST",
          body: JSON.stringify({ catalog_signature: regroupProposal.catalog_signature, items }),
        },
      );
      setSelected(null);
      setRegroupProposal(null);
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
      !window.confirm(`Permanently remove ${selected.filename}?`)
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
            title={t(processing ? "Project navigation is locked while processing" : "Select project")}
            onChange={(event) =>
              switchProject(
                projects.find((item) => item.id === event.target.value) || null,
              )
            }
          >
            <option value="">{t('Select project')}</option>
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          <ChevronDown size={14} />
          <button
            type="button"
            className="project-create-button"
            aria-label={t('Create project')}
            title={t('Create project')}
            disabled={processing}
            onClick={() => void createProject()}
          >
            <FolderPlus size={15} />
          </button>
        </div>
        <form className="global-search" onSubmit={runSearch}>
          <button
            type="submit"
            className="global-search-submit"
            aria-label={t('Search project')}
            title={t('Search project')}
          >
            <Search size={16} />
          </button>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t('Search properties and entities')}
          />
        </form>
        <div className="top-actions">
          <button
            type="button"
            className={`mcp-button ${mcpOpen ? "active" : ""}`}
            onClick={toggleMcp}
          >
            <Network size={15} /> MCP {t(mcpOpen ? "open" : "closed")}
          </button>
          <button
            type="button"
            className="icon-button"
            title={t('Settings')}
            aria-label={t('Settings')}
            onClick={() => {
              setSettingsFocusRoute(null);
              setSettingsOpen(true);
            }}
          >
            <Settings2 size={17} />
          </button>
          <LanguageSwitcher />
          <AccountMenu onSignOut={signOut} />
        </div>
      </header>
      {mcpOpen && (
        <div className="mcp-warning">
          <strong>{t('MCP endpoint is publicly callable.')}</strong>
          <span>
            {t("It inherits this user's current capabilities until closed.")}
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
                {t(processingStageLabel(processingStatus.stage || processingStatus.status))}
              </strong>
              <span>
                {processingDetail ? (processingDetail.values
                  ? t(processingDetail.key, processingDetail.values)
                  : t(processingDetail.key)) :
                  (processingStatus.locked
                    ? t('Project actions are locked until this stage completes.')
                    : t('Build finished.'))}
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
                  aria-label={t('Show error detail')}
                  onClick={() => setProcessingErrorOpen(true)}
                >
                  <Bug size={14} /> {t('Show error detail')}
                </button>
              )}
              {processingStatus.locked && (
                <button
                  type="button"
                  className="text-button"
                  onClick={cancelJob}
                >
                  {t('Cancel')}
                </button>
              )}
              {failedProperty && (
                <button
                  type="button"
                  className="text-button"
                  onClick={() => retryJob(failedProperty.id)}
                >
                  {t('Retry')}
                </button>
              )}
            </div>
          </div>
        )}
      <div className={`workspace ${sidebarCollapsed ? "workspace-sidebar-collapsed" : ""}`}>
        <aside className={`property-sidebar ${sidebarCollapsed ? "collapsed" : ""}`}>
          <div className="sidebar-heading">
            <div className="project-title-row">
              <div className="project-title-copy">
                <span className="eyebrow">{t('PROJECT PROPERTIES')}</span>
                <h2>{project?.name || t('No project selected')}</h2>
              </div>
              {project && (
                <div className="project-title-actions">
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={t('Rename project')}
                    title={t('Rename project')}
                    onClick={renameProject}
                  >
                    <Pencil size={15} />
                  </button>
                  <button
                    type="button"
                    className="icon-button danger"
                    aria-label={t('Delete project')}
                    title={t('Delete project')}
                    onClick={deleteProject}
                    disabled={processing}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              )}
            </div>
            <button
              type="button"
              className="icon-button"
              title={t(sidebarCollapsed ? "Expand panel" : "Collapse panel")}
              aria-label={t(sidebarCollapsed ? "Expand panel" : "Collapse panel")}
              aria-expanded={!sidebarCollapsed}
              onClick={() => setSidebarCollapsed((current) => !current)}
            >
              {sidebarCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>
          </div>
          <div className="tree-actions">
            <button
              className="primary-button compact"
              disabled={!project || processing || checkingProviders}
              aria-busy={checkingProviders}
              onClick={() => void beginPropertyImport()}
            >
              <FilePlus2 size={15} /> {t('Add property')}
            </button>
            {project && (
              <button
                type="button"
                className="regroup-properties-button"
                title={t('Revise Project Tree')}
                aria-label={t('Revise Project Tree')}
                disabled={processing || busy || properties.length === 0}
                onClick={() => setRegroupOpen(true)}
              >
                <FolderTree size={15} />
                <span>{t('Revise Project Tree')}</span>
              </button>
            )}
          </div>
          <div className="property-tree">
            {properties.length ? (
              <PropertyTree properties={properties} onSelect={selectProperty} />
            ) : (
              <div className="tree-empty">
                {t('No properties yet.')}
                <br />
                {t('Add a file to begin.')}
              </div>
            )}
          </div>
          <div className="sidebar-footer">
            <span className={`connection-dot ${processing ? "busy" : ""}`} />{" "}
            {processing
              ? t(processingStageLabel(processingStatus?.stage || "queued"))
              : t('Active snapshot ready')}
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
                <Network size={15} /> {t('Entity Graph')}
              </button>
              <button
                type="button"
                className={tab === "property" ? "active" : ""}
                onClick={() => setTab("property")}
              >
                <FolderOpen size={15} /> {t('Property Graph')}
              </button>
              <button
                type="button"
                className={tab === "query" ? "active" : ""}
                onClick={() => setTab("query")}
              >
                <MessageSquareText size={15} /> {t('AI Query')}
              </button>
            </div>
            <div className="tab-status">
              {processing && (
                <>
                  <span className="spinner" /> {t(processingStageLabel(processingStatus?.stage || "queued"))}
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
              properties={properties}
              displaySelection={propertyGraphDisplaySelection}
              onDisplaySelectionChange={setPropertyGraphDisplaySelection}
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
              properties={properties}
              displaySelection={entityGraphDisplaySelection}
              onDisplaySelectionChange={setEntityGraphDisplaySelection}
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
        items={importTarget?.items || []}
        busy={busy}
        onCancel={cancelPropertyImport}
        onConfirm={confirmPropertyImport}
      />
      <RegroupPropertiesDialog
        open={regroupOpen}
        projectName={project?.name || ''}
        busy={busy || processing}
        onClose={() => setRegroupOpen(false)}
        onSubmit={regroupProperties}
      />
      <RegroupConfirmationDialog
        open={Boolean(regroupProposal)}
        proposal={regroupProposal}
        busy={busy || processing}
        onClose={() => setRegroupProposal(null)}
        onConfirm={confirmRegroupProperties}
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
        llmResponse={processingStatus?.llm_response}
        onClose={() => setProcessingErrorOpen(false)}
      />
      {results && (
        <div className="search-drawer">
          <div className="overlay-header">
            <div>
              <span className="eyebrow">{t('SEARCH RESULTS')}</span>
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
              {t('Properties')} <span>{results.properties.length}</span>
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
                <small title={item.retrieval_path?.join(" -> ")}>
                  {item.retrieval_reason ? t(item.retrieval_reason) : t('Direct match')}
                </small>
              </button>
            ))}
          </section>
          <section>
            <h3>
              {t('Entities')} <span>{results.entities.length}</span>
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
                <small title={item.retrieval_path?.join(" -> ")}>
                  {item.retrieval_reason ? t(item.retrieval_reason) : t('Direct match')}
                </small>
              </button>
            ))}
          </section>
        </div>
      )}
      {uploadOpen && (
        <div className="modal-backdrop">
          <form className="upload-modal property-upload-modal" onSubmit={submitUpload}>
            <div className="overlay-header">
              <div>
                <span className="eyebrow">{t('INGEST PROPERTY')}</span>
                <h2>{t('Add to')} {project?.name}</h2>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label={t('Close property upload')}
                disabled={busy}
                onClick={() => {
                  setUploadOpen(false);
                  setUploadFiles([]);
                  setUploadDragActive(false);
                }}
              >
                <X size={16} />
              </button>
            </div>
            <input
              ref={uploadInput}
              className="visually-hidden"
              type="file"
              aria-label={t('Property files')}
              multiple
              disabled={busy}
              onChange={(event) => setUploadFiles(Array.from(event.target.files || []))}
            />
            <button
              type="button"
              className={`dropzone ${uploadDragActive ? "drag-active" : ""}`}
              aria-label={t('Choose property files')}
              disabled={busy}
              onClick={() => uploadInput.current?.click()}
              onDragEnter={(event) => {
                event.preventDefault();
                setUploadDragActive(true);
              }}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "copy";
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                  setUploadDragActive(false);
                }
              }}
              onDrop={(event) => {
                event.preventDefault();
                setUploadDragActive(false);
                setUploadFiles(droppedPropertyFiles(event.dataTransfer));
              }}
            >
              <Upload size={24} />
              <strong>{t('Choose files')}</strong>
              <span>{t('Text, documents, or images')}</span>
              <span className="dropzone-count">
                {uploadFiles.length
                  ? `${uploadFiles.length} ${t(uploadFiles.length === 1 ? "file" : "files")} ${t('selected')}`
                  : t('No files selected')}
              </span>
            </button>
            {uploadFiles.length > 0 ? (
              <section className="upload-file-selection">
                <div className="upload-file-selection-header">
                  <span>{t('Selected files')}</span>
                  <strong>{uploadFiles.length}</strong>
                </div>
                <ul aria-label={t('Selected files')}>
                  {uploadFiles.map((file, index) => (
                    <li
                      key={`${file.name}-${file.size}-${file.lastModified}-${index}`}
                      title={file.name}
                    >
                      <FilePlus2 size={14} aria-hidden="true" />
                      <span>{file.name}</span>
                      <button
                        type="button"
                        className="upload-file-remove"
                        aria-label={`${t('Remove')} ${file.name}`}
                        title={`${t('Remove')} ${file.name}`}
                        disabled={busy}
                        onClick={() => {
                          setUploadFiles((current) =>
                            current.filter((_, currentIndex) => currentIndex !== index),
                          );
                          if (uploadInput.current) uploadInput.current.value = "";
                        }}
                      >
                        <X size={13} aria-hidden="true" />
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            <label>
              {t('Optional context')}
              <textarea
                name="comment"
                rows={3}
                disabled={busy}
                placeholder={t('Add document details or grouping context')}
              />
            </label>
            {importPreparation && (
              <section
                className="import-preparation"
                role="status"
                aria-live="polite"
              >
                <div className="import-preparation-header">
                  <div>
                    <span className="eyebrow">{t(importPreparation.phase === "naming" ? "Group Arrangement Agent" : "Definition Generation Agent")}</span>
                    <strong>
                      {importPreparation.phase === "uploading"
                        ? `${t('Uploading')} ${importPreparation.total} ${t(importPreparation.total === 1 ? "property" : "properties")}`
                        : importPreparation.phase === "naming"
                          ? `${t('Generating')} ${t(importPreparation.total === 1 ? "a suggested filename" : "suggested filenames")}`
                          : `${t('Preparing')} ${importPreparation.index} ${t('of')} ${importPreparation.total} ${t(importPreparation.total === 1 ? "property" : "properties")}`}
                    </strong>
                  </div>
                  <span className="spinner" aria-hidden="true" />
                </div>
                {importPreparation.filename && (
                  <div className="import-preparation-file">
                    <FilePlus2 size={16} aria-hidden="true" />
                    <span>
                      <strong>{importPreparation.filename}</strong>
                      <small>{t('Generating a concise property definition')}</small>
                    </span>
                  </div>
                )}
                <progress
                  aria-label={t('Definition Generation Agent preparation progress')}
                  max={Math.max(importPreparation.total, 1)}
                  value={importPreparation.index}
                />
              </section>
            )}
            <button type="submit" className="primary-button" disabled={busy || uploadFiles.length === 0}>
              {busy ? <span className="spinner" aria-hidden="true" /> : <Upload size={15} />}
              {t(busy ? "Preparing properties" : "Prepare import")}
            </button>
          </form>
        </div>
      )}
      <ProviderConfigurationAlert
        open={Boolean(providerReadiness)}
        missingRoutes={providerReadiness?.missing_routes || []}
        canConfigure={providerReadiness?.can_configure ?? false}
        onClose={() => setProviderReadiness(null)}
        onConfigure={openProviderSettings}
      />
      {settingsOpen && (
        <SettingsPanel
          focusRouteKey={settingsFocusRoute}
          onClose={() => {
            setSettingsOpen(false);
            setSettingsFocusRoute(null);
          }}
        />
      )}
    </div>
  );
}
