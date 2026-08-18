import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  KeyRound,
  Pencil,
  Plus,
  Save,
  ServerCog,
  Shield,
  Trash2,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import { request } from "../api";
import { useDocSeekTranslation } from "../i18n";
import CapabilityPicker from "./CapabilityPicker";
import FloatingWindow from "./FloatingWindow";

type Provider = {
  id: string;
  name: string;
  provider_type: "llm" | "embedding";
  model: string;
  base_url?: string | null;
  secret_configured: boolean;
};
type Profile = {
  username: string;
  groups: Array<{ name: string }>;
  roles: Array<{ name: string }>;
  capabilities: string[];
};
type RetrievalSettings = {
  ai_query: {
    property_limit: number;
    entity_limit: number;
    total_node_limit: number;
  };
  search: {
    property_limit: number;
    entity_limit: number;
  };
};
type SystemConfig = {
  routes: Record<string, string | null>;
  entity_schema: string;
  entity_prompt: string;
  neo4j: {
    property_database: string;
    entity_database: string;
    use_neo4j: boolean;
  };
  mcp: { enabled: boolean };
  retrieval: RetrievalSettings;
};
type SystemConfigResponse = Omit<SystemConfig, "retrieval"> & {
  retrieval?: {
    ai_query?: Partial<RetrievalSettings["ai_query"]>;
    search?: Partial<RetrievalSettings["search"]>;
  };
};
type AccessUser = { id: string; username: string; disabled: boolean };
type AccessGroup = { id: string; name: string };
type AccessRole = {
  id: string;
  name: string;
  immutable: boolean;
  capabilities: string[];
};

const DEFAULT_RETRIEVAL_SETTINGS: RetrievalSettings = {
  ai_query: {
    property_limit: 15,
    entity_limit: 15,
    total_node_limit: 30,
  },
  search: {
    property_limit: 30,
    entity_limit: 30,
  },
};

export function normalizeSystemConfig(
  config: SystemConfigResponse,
): SystemConfig {
  return {
    ...config,
    retrieval: {
      ai_query: {
        ...DEFAULT_RETRIEVAL_SETTINGS.ai_query,
        ...config.retrieval?.ai_query,
      },
      search: {
        ...DEFAULT_RETRIEVAL_SETTINGS.search,
        ...config.retrieval?.search,
      },
    },
  };
}

export default function SettingsPanel({
  onClose,
  open = true,
  focusRouteKey = null,
}: {
  onClose: () => void;
  open?: boolean;
  focusRouteKey?: string | null;
}) {
  const { t } = useDocSeekTranslation();
  const [dismissed, setDismissed] = useState(false);
  const [tab, setTab] = useState<"system" | "access" | "profile">("system");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerChecks, setProviderChecks] = useState<Record<string, string>>(
    {},
  );
  const [providerDialogOpen, setProviderDialogOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [users, setUsers] = useState<AccessUser[]>([]);
  const [groups, setGroups] = useState<AccessGroup[]>([]);
  const [roles, setRoles] = useState<AccessRole[]>([]);
  const [neo4jStatus, setNeo4jStatus] = useState<{
    ready: boolean;
    message: string;
    configured?: boolean;
    mode?: string;
  } | null>(null);
  const [storageStatus, setStorageStatus] = useState<{
    writable: boolean;
    data_dir: string;
  } | null>(null);
  const [providerForm, setProviderForm] = useState({
    name: "",
    provider_type: "llm",
    model: "",
    base_url: "",
    secret: "",
  });
  const [userForm, setUserForm] = useState({ username: "", password: "" });
  const [groupName, setGroupName] = useState("");
  const [memberForm, setMemberForm] = useState({ user_id: "", group_id: "" });
  const [roleGroupForm, setRoleGroupForm] = useState({
    group_id: "",
    role_id: "",
  });
  const [roleForm, setRoleForm] = useState({
    name: "",
    capabilities: [] as string[],
  });
  const [passwordForm, setPasswordForm] = useState({
    current_password: "",
    new_password: "",
  });
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const routeSectionRef = useRef<HTMLFormElement>(null);

  function requestClose() {
    setProviderDialogOpen(false);
    setDismissed(true);
    window.setTimeout(onClose, 160);
  }

  async function load() {
    const safe = async <T,>(path: string): Promise<T | null> => {
      try {
        return await request<T>(path);
      } catch {
        return null;
      }
    };
    const [
      nextProfile,
      nextConfig,
      nextProviders,
      nextNeo4j,
      nextStorage,
      nextUsers,
      nextGroups,
      nextRoles,
    ] = await Promise.all([
      safe<Profile>("/profile"),
      safe<SystemConfigResponse>("/system/config"),
      safe<Provider[]>("/system/providers"),
      safe<{ ready: boolean; message: string; configured?: boolean; mode?: string }>("/system/neo4j/check"),
      safe<{ writable: boolean; data_dir: string }>("/system/storage/check"),
      safe<AccessUser[]>("/admin/users"),
      safe<AccessGroup[]>("/admin/groups"),
      safe<AccessRole[]>("/admin/roles"),
    ]);
    setProfile(nextProfile);
    setConfig(nextConfig ? normalizeSystemConfig(nextConfig) : null);
    setProviders(nextProviders || []);
    setNeo4jStatus(nextNeo4j);
    setStorageStatus(nextStorage);
    setUsers(nextUsers || []);
    setGroups(nextGroups || []);
    setRoles(nextRoles || []);
  }
  useEffect(() => {
    load().catch((err) =>
      setError(err instanceof Error ? err.message : t("Unable to load settings")),
    );
  }, []);
  useEffect(() => {
    if (!config || !focusRouteKey || tab !== "system") return;
    const routeSelect = routeSectionRef.current?.querySelector<HTMLSelectElement>(
      `[data-route-key="${focusRouteKey}"]`,
    );
    if (!routeSelect) return;
    routeSelect.scrollIntoView?.({ behavior: "smooth", block: "center" });
    routeSelect.focus({ preventScroll: true });
  }, [config, focusRouteKey, tab]);

  const llmProviders = useMemo(
    () => providers.filter((item) => item.provider_type === "llm"),
    [providers],
  );
  const embeddingProviders = useMemo(
    () => providers.filter((item) => item.provider_type === "embedding"),
    [providers],
  );
  function setRoute(key: string, value: string) {
    setConfig((current) =>
      current
        ? { ...current, routes: { ...current.routes, [key]: value || null } }
        : current,
    );
  }
  async function saveConfig(event: FormEvent) {
    event.preventDefault();
    if (!config) return;
    try {
      await request("/system/config", {
        method: "PATCH",
        body: JSON.stringify({
          ...config.routes,
          mcp_enabled: config.mcp.enabled,
        }),
      });
      setNotice(t("System configuration saved"));
      await load();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("Unable to save configuration"),
      );
    }
  }
  function resetProviderForm() {
    setEditingProvider(null);
    setProviderForm({
      name: "",
      provider_type: "llm",
      model: "",
      base_url: "",
      secret: "",
    });
  }
  function closeProviderDialog() {
    setProviderDialogOpen(false);
    resetProviderForm();
  }
  function beginProviderAdd() {
    resetProviderForm();
    setError("");
    setNotice("");
    setProviderDialogOpen(true);
  }
  function beginProviderEdit(provider: Provider) {
    setEditingProvider(provider);
    setProviderForm({
      name: provider.name,
      provider_type: provider.provider_type,
      model: provider.model,
      base_url: provider.base_url || "",
      secret: "",
    });
    setError("");
    setNotice("");
    setProviderDialogOpen(true);
  }
  async function saveProvider(event: FormEvent) {
    event.preventDefault();
    try {
      const path = editingProvider
        ? `/system/providers/${editingProvider.id}`
        : "/system/providers";
      const body = editingProvider && !providerForm.secret
        ? Object.fromEntries(
            Object.entries(providerForm).filter(([key]) => key !== "secret"),
          )
        : providerForm;
      await request(path, {
        method: editingProvider ? "PATCH" : "POST",
        body: JSON.stringify(body),
      });
      setNotice(
        t(editingProvider ? "Provider profile updated" : "Provider profile created"),
      );
      closeProviderDialog();
      await load();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("Unable to save provider"),
      );
    }
  }
  async function removeProvider(provider: Provider) {
    if (!window.confirm(`Remove provider ${provider.name}?`)) return;
    try {
      await request(`/system/providers/${provider.id}`, { method: "DELETE" });
      setProviderChecks((current) => {
        const next = { ...current };
        delete next[provider.id];
        return next;
      });
      if (editingProvider?.id === provider.id) resetProviderForm();
      setNotice(t("Provider profile removed"));
      await load();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("Unable to remove provider"),
      );
    }
  }
  async function validateProvider(provider: Provider) {
    setProviderChecks((current) => ({ ...current, [provider.id]: t("Checking...") }));
    try {
      const result = await request<{ ready: boolean; dimensions?: number }>(
        `/system/providers/${provider.id}/validate`,
        { method: "POST" },
      );
      if (!result.ready) {
        setProviderChecks((current) => ({
          ...current,
          [provider.id]: t("Unavailable"),
        }));
        setError(`Provider ${provider.name} is unavailable`);
        return;
      }
      setProviderChecks((current) => ({
        ...current,
        [provider.id]: result.dimensions
          ? `Ready · ${result.dimensions}d`
          : t("Ready"),
      }));
    } catch (err) {
      setProviderChecks((current) => ({
        ...current,
        [provider.id]: t("Unavailable"),
      }));
      setError(
        err instanceof Error ? err.message : t("Provider validation failed"),
      );
    }
  }
  async function createUser(event: FormEvent) {
    event.preventDefault();
    try {
      await request("/admin/users", {
        method: "POST",
        body: JSON.stringify(userForm),
      });
      setUserForm({ username: "", password: "" });
      setNotice(t("User created"));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to create user"));
    }
  }
  async function createGroup(event: FormEvent) {
    event.preventDefault();
    try {
      await request("/admin/groups", {
        method: "POST",
        body: JSON.stringify({ name: groupName }),
      });
      setGroupName("");
      setNotice(t("Group created"));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to create group"));
    }
  }
  async function addMember(event: FormEvent) {
    event.preventDefault();
    try {
      await request(`/admin/groups/${memberForm.group_id}/members`, {
        method: "POST",
        body: JSON.stringify({ user_id: memberForm.user_id }),
      });
      setNotice(t("Member added"));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to add member"));
    }
  }
  async function addGroupRole(event: FormEvent) {
    event.preventDefault();
    try {
      await request(`/admin/groups/${roleGroupForm.group_id}/roles`, {
        method: "POST",
        body: JSON.stringify({ role_id: roleGroupForm.role_id }),
      });
      setNotice(t("Role assigned"));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to assign role"));
    }
  }
  async function createRole(event: FormEvent) {
    event.preventDefault();
    try {
      await request("/admin/roles", {
        method: "POST",
        body: JSON.stringify(roleForm),
      });
      setRoleForm({ name: "", capabilities: [] });
      setNotice(t("Role created"));
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to create role"));
    }
  }
  async function disableUser(user: AccessUser) {
    try {
      await request(`/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ disabled: !user.disabled }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Unable to update user"));
    }
  }
  async function changePassword(event: FormEvent) {
    event.preventDefault();
    try {
      await request("/profile/password", {
        method: "POST",
        body: JSON.stringify(passwordForm),
      });
      setPasswordForm({ current_password: "", new_password: "" });
      setNotice(t("Password changed"));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("Unable to change password"),
      );
    }
  }
  return (
    <>
      <FloatingWindow
        open={open && !dismissed}
        as="aside"
        className="settings-panel"
        role="dialog"
        aria-label={t('Settings')}
      >
      <header className="settings-header">
        <div>
          <span className="eyebrow">{t('DOCSEEK CONTROL PLANE')}</span>
          <h2>{t('Settings')}</h2>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label={t('Close settings')}
          title={t('Close settings')}
          onClick={requestClose}
        >
          <X size={17} />
        </button>
      </header>
      <nav className="settings-tabs">
        <button
          type="button"
          className={tab === "system" ? "active" : ""}
          onClick={() => setTab("system")}
        >
          <ServerCog size={15} /> {t('System')}
        </button>
        <button
          type="button"
          className={tab === "access" ? "active" : ""}
          onClick={() => setTab("access")}
        >
          <Shield size={15} /> {t('Access')}
        </button>
        <button
          type="button"
          className={tab === "profile" ? "active" : ""}
          onClick={() => setTab("profile")}
        >
          <UserRound size={15} /> {t('Profile')}
        </button>
      </nav>
      {error && (
        <div className="error-banner">
          {error}
          <button
            type="button"
            aria-label={t('Dismiss error')}
            onClick={() => setError("")}
          >
            <X size={14} />
          </button>
        </div>
      )}
      {notice && (
        <div className="notice-banner">
          {notice}
          <button
            type="button"
            aria-label={t('Dismiss notice')}
            onClick={() => setNotice("")}
          >
            <X size={14} />
          </button>
        </div>
      )}
      {tab === "system" && (
        <div className="settings-content">
          <section className="settings-section">
            <div className="section-heading">
              <div>
                <span className="eyebrow">{t('PROVIDERS')}</span>
                <h3>{t('Provider profiles')}</h3>
              </div>
              <div className="provider-section-actions">
                <span>{providers.length}</span>
                <button
                  type="button"
                  className="primary-button compact"
                  onClick={beginProviderAdd}
                >
                  <Plus size={14} /> {t('Add provider')}
                </button>
              </div>
            </div>
            <div className="settings-list">
              {providers.map((provider) => (
                <div className="settings-list-row" key={provider.id}>
                  <span>
                    {provider.name}
                    <small>
                      {provider.provider_type} · {provider.model}
                      {providerChecks[provider.id]
                        ? ` · ${providerChecks[provider.id]}`
                        : ""}
                    </small>
                  </span>
                  <span className="provider-actions">
                    <small>
                      {t(provider.secret_configured ? "Configured" : "No secret")}
                    </small>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={`${t('Validate provider')} ${provider.name}`}
                      title={`${t('Validate provider')} ${provider.name}`}
                      onClick={() => validateProvider(provider)}
                    >
                      <CheckCircle2 size={15} />
                    </button>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={`${t('Edit provider')} ${provider.name}`}
                      title={`${t('Edit provider')} ${provider.name}`}
                      onClick={() => beginProviderEdit(provider)}
                    >
                      <Pencil size={15} />
                    </button>
                    <button
                      type="button"
                      className="icon-button danger"
                      aria-label={`${t('Remove provider')} ${provider.name}`}
                      title={`${t('Remove provider')} ${provider.name}`}
                      onClick={() => void removeProvider(provider)}
                    >
                      <Trash2 size={15} />
                    </button>
                  </span>
                </div>
              ))}
              {!providers.length && (
                <p className="muted">{t('No provider profiles configured.')}</p>
              )}
            </div>
          </section>
          {config && (
            <form
              className="settings-section"
              ref={routeSectionRef}
              onSubmit={saveConfig}
            >
              <div className="section-heading">
                <div>
                  <span className="eyebrow">{t('MODEL ROUTES')}</span>
                  <h3>{t('System configuration')}</h3>
                </div>
                <button
                  className="icon-button"
                  type="submit"
                  aria-label={t('Save system configuration')}
                  title={t('Save system configuration')}
                >
                  <Save size={15} />
                </button>
              </div>
              <label>
                {t('Definition Generation Agent')}
                <select
                  data-route-key="dg_agent_route"
                  value={config.routes.dg_agent_route || ""}
                  onChange={(event) =>
                    setRoute("dg_agent_route", event.target.value)
                  }
                >
                  <option value="">{t('Select profile')}</option>
                  {llmProviders.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t('Group Arrangement Agent')}
                <select
                  data-route-key="ga_agent_route"
                  value={config.routes.ga_agent_route || ""}
                  onChange={(event) =>
                    setRoute("ga_agent_route", event.target.value)
                  }
                >
                  <option value="">{t('Select profile')}</option>
                  {llmProviders.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t('Property Graph Building Agent')}
                <select
                  data-route-key="pgb_agent_route"
                  value={config.routes.pgb_agent_route || ""}
                  onChange={(event) =>
                    setRoute("pgb_agent_route", event.target.value)
                  }
                >
                  <option value="">{t('Select profile')}</option>
                  {llmProviders.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t('Entity Extraction Agent')}
                <select
                  data-route-key="entity_agent_route"
                  value={config.routes.entity_agent_route || ""}
                  onChange={(event) =>
                    setRoute("entity_agent_route", event.target.value)
                  }
                >
                  <option value="">{t('Select profile')}</option>
                  {llmProviders.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t('AI Query LLM')}
                <select
                  data-route-key="ai_query_route"
                  value={config.routes.ai_query_route || ""}
                  onChange={(event) =>
                    setRoute("ai_query_route", event.target.value)
                  }
                >
                  <option value="">{t('Select profile')}</option>
                  {llmProviders.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t('Shared Embedding Model')}
                <select
                  data-route-key="shared_embedding_route"
                  value={config.routes.shared_embedding_route || ""}
                  onChange={(event) =>
                    setRoute("shared_embedding_route", event.target.value)
                  }
                >
                  <option value="">{t('Select profile')}</option>
                  {embeddingProviders.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="toggle-row">
                <input
                  type="checkbox"
                  checked={config.mcp.enabled}
                  onChange={(event) =>
                    setConfig({
                      ...config,
                      mcp: { enabled: event.target.checked },
                    })
                  }
                />{" "}
                {t('Enable project MCP endpoints')}
              </label>
              <div className="settings-checks">
                <span
                  className={
                    neo4jStatus?.ready
                      ? "check-ok"
                      : neo4jStatus?.configured
                        ? "check-warn"
                        : "check-info"
                  }
                >
                  {neo4jStatus?.message ? t(neo4jStatus.message) : t('Neo4j status unavailable')}
                </span>
                <span
                  className={
                    storageStatus?.writable ? "check-ok" : "check-warn"
                  }
                >
                  {storageStatus?.writable
                    ? t('Storage writable')
                    : t('Storage check unavailable')}
                </span>
              </div>
            </form>
          )}
        </div>
      )}
      {tab === "access" && (
        <div className="settings-content">
          <section className="settings-section">
            <div className="section-heading">
              <div>
                <span className="eyebrow">{t('USERS')}</span>
                <h3>{t('User accounts')}</h3>
              </div>
              <UsersRound size={16} />
            </div>
            <div className="settings-list">
              {users.map((item) => (
                <div className="settings-list-row" key={item.id}>
                  <span>
                    {item.username}
                    <small>{t(item.disabled ? "Disabled" : "Active")}</small>
                  </span>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => disableUser(item)}
                  >
                    {t(item.disabled ? "Enable" : "Disable")}
                  </button>
                </div>
              ))}
            </div>
            <form className="settings-form" onSubmit={createUser}>
              <label>
                {t('Username')}
                <input
                  value={userForm.username}
                  onChange={(event) =>
                    setUserForm({ ...userForm, username: event.target.value })
                  }
                  required
                />
              </label>
              <label>
                {t('Temporary password')}
                <input
                  type="password"
                  minLength={8}
                  value={userForm.password}
                  onChange={(event) =>
                    setUserForm({ ...userForm, password: event.target.value })
                  }
                  required
                />
              </label>
              <button className="primary-button compact" type="submit">
                <Plus size={14} /> {t('Add user')}
              </button>
            </form>
          </section>
          <section className="settings-section">
            <div className="section-heading">
              <div>
                <span className="eyebrow">{t('GROUPS')}</span>
                <h3>{t('Group membership')}</h3>
              </div>
              <UsersRound size={16} />
            </div>
            <div className="settings-list">
              {groups.map((item) => (
                <div className="settings-list-row" key={item.id}>
                  <span>{item.name}</span>
                </div>
              ))}
            </div>
            <form className="settings-form" onSubmit={createGroup}>
              <label>
                {t('Group name')}
                <input
                  value={groupName}
                  onChange={(event) => setGroupName(event.target.value)}
                  required
                />
              </label>
              <button className="primary-button compact" type="submit">
                <Plus size={14} /> {t('Add group')}
              </button>
            </form>
            <form
              className="settings-form assignment-form"
              onSubmit={addMember}
            >
              <label>
                {t('Member user')}
                <select
                  aria-label={t('Member user')}
                  value={memberForm.user_id}
                  onChange={(event) =>
                    setMemberForm({
                      ...memberForm,
                      user_id: event.target.value,
                    })
                  }
                  required
                >
                  <option value="">{t('Select user')}</option>
                  {users.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.username}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t('Member group')}
                <select
                  aria-label={t('Member group')}
                  value={memberForm.group_id}
                  onChange={(event) =>
                    setMemberForm({
                      ...memberForm,
                      group_id: event.target.value,
                    })
                  }
                  required
                >
                  <option value="">{t('Select group')}</option>
                  {groups.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <button className="primary-button compact" type="submit">
                <Plus size={14} /> {t('Add member')}
              </button>
            </form>
          </section>
          <section className="settings-section roles-section">
            <div className="section-heading">
              <div>
                <span className="eyebrow">{t('ROLES')}</span>
                <h3>{t('Capability grants')}</h3>
              </div>
              <Shield size={16} />
            </div>
            <div className="settings-list">
              {roles.map((item) => (
                <div className="settings-list-row" key={item.id}>
                  <span>
                    {item.name}
                    <small>
                      {item.immutable
                        ? t('Built-in')
                        : item.capabilities.join(", ") || t('No capabilities')}
                    </small>
                  </span>
                </div>
              ))}
            </div>
            <form className="settings-form role-editor" onSubmit={createRole}>
              <label>
                {t('Role name')}
                <input
                  value={roleForm.name}
                  onChange={(event) =>
                    setRoleForm({ ...roleForm, name: event.target.value })
                  }
                  required
                />
              </label>
              <CapabilityPicker
                value={roleForm.capabilities}
                onChange={(capabilities) =>
                  setRoleForm({ ...roleForm, capabilities })
                }
              />
              <button className="primary-button compact" type="submit">
                <Plus size={14} /> {t('Add role')}
              </button>
            </form>
            <form
              className="settings-form assignment-form"
              onSubmit={addGroupRole}
            >
              <label>
                {t('Role group')}
                <select
                  aria-label={t('Role group')}
                  value={roleGroupForm.group_id}
                  onChange={(event) =>
                    setRoleGroupForm({
                      ...roleGroupForm,
                      group_id: event.target.value,
                    })
                  }
                  required
                >
                  <option value="">{t('Select group')}</option>
                  {groups.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t('Role')}
                <select
                  aria-label={t('Role')}
                  value={roleGroupForm.role_id}
                  onChange={(event) =>
                    setRoleGroupForm({
                      ...roleGroupForm,
                      role_id: event.target.value,
                    })
                  }
                  required
                >
                  <option value="">{t('Select role')}</option>
                  {roles.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <button className="primary-button compact" type="submit">
                <Plus size={14} /> {t('Assign role')}
              </button>
            </form>
          </section>
        </div>
      )}
      {tab === "profile" && (
        <div className="settings-content">
          <section className="settings-section">
            <span className="eyebrow">{t('ACCOUNT')}</span>
            <h3>{profile?.username || t('Current user')}</h3>
            <p className="muted">
              {t('Groups')}: {" "}
              {profile?.groups.map((group) => group.name).join(", ") || t('None')}
            </p>
            <p className="muted">
              {t('Roles')}: {" "}
              {profile?.roles.map((role) => role.name).join(", ") || t('None')}
            </p>
            <div className="capability-cloud">
              {(profile?.capabilities || []).map((capability) => (
                <span key={capability}>{capability}</span>
              ))}
            </div>
          </section>
          <form
            className="settings-section settings-form"
            onSubmit={changePassword}
          >
            <div className="section-heading">
              <div>
                <span className="eyebrow">{t('SECURITY')}</span>
                <h3>{t('Change password')}</h3>
              </div>
              <KeyRound size={16} />
            </div>
            <label>
              {t('Current password')}
              <input
                type="password"
                value={passwordForm.current_password}
                onChange={(event) =>
                  setPasswordForm({
                    ...passwordForm,
                    current_password: event.target.value,
                  })
                }
                required
              />
            </label>
            <label>
              {t('New password')}
              <input
                type="password"
                minLength={8}
                value={passwordForm.new_password}
                onChange={(event) =>
                  setPasswordForm({
                    ...passwordForm,
                    new_password: event.target.value,
                  })
                }
                required
              />
            </label>
            <button className="primary-button compact" type="submit">
              <KeyRound size={14} /> {t('Change password')}
            </button>
          </form>
        </div>
      )}
      </FloatingWindow>
      <FloatingWindow
        open={providerDialogOpen && open && !dismissed}
        className="modal-backdrop provider-editor-backdrop"
        role="presentation"
      >
        <section
          className="upload-modal provider-editor-modal"
          role="dialog"
          aria-modal="true"
          aria-label={t(editingProvider ? "Edit model provider" : "Add model provider")}
        >
          <header className="provider-editor-header">
            <div>
              <span className="eyebrow">{t('MODEL PROVIDER')}</span>
              <h2>{t(editingProvider ? "Edit provider" : "Add provider")}</h2>
            </div>
            <button
              type="button"
              className="icon-button"
              aria-label={t('Close provider dialog')}
              title={t('Close provider dialog')}
              onClick={closeProviderDialog}
            >
              <X size={17} />
            </button>
          </header>
          {error && (
            <div className="error-banner provider-editor-error">
              {error}
              <button
                type="button"
                aria-label={t('Dismiss provider error')}
                onClick={() => setError("")}
              >
                <X size={14} />
              </button>
            </div>
          )}
          <form className="settings-form provider-editor-form" onSubmit={saveProvider}>
            <label>
              {t('Name')}
              <input
                autoFocus
                value={providerForm.name}
                onChange={(event) =>
                  setProviderForm((current) => ({
                    ...current,
                    name: event.target.value,
                  }))
                }
                required
              />
            </label>
            <label>
              {t('Type')}
              <select
                value={providerForm.provider_type}
                disabled={Boolean(editingProvider)}
                onChange={(event) =>
                  setProviderForm((current) => ({
                    ...current,
                    provider_type: event.target.value,
                  }))
                }
              >
                <option value="llm">{t('LLM')}</option>
                <option value="embedding">{t('Embedding')}</option>
              </select>
            </label>
            <label>
              {t('Model')}
              <input
                value={providerForm.model}
                onChange={(event) =>
                  setProviderForm((current) => ({
                    ...current,
                    model: event.target.value,
                  }))
                }
                required
              />
            </label>
            <label>
              {t('Base URL')}
              <input
                value={providerForm.base_url}
                onChange={(event) =>
                  setProviderForm((current) => ({
                    ...current,
                    base_url: event.target.value,
                  }))
                }
                placeholder={t('Optional provider endpoint')}
              />
            </label>
            <label>
              {t('Secret')}
              <input
                type="password"
                value={providerForm.secret}
                onChange={(event) =>
                  setProviderForm((current) => ({
                    ...current,
                    secret: event.target.value,
                  }))
                }
                placeholder={
                  editingProvider
                    ? t('Leave blank to keep the current secret')
                    : t('Stored in local protected config')
                }
              />
            </label>
            <div className="modal-actions provider-editor-actions">
              <button
                type="button"
                className="secondary-button"
                onClick={closeProviderDialog}
              >
                {t('Cancel')}
              </button>
              <button className="primary-button" type="submit">
                {editingProvider ? <Save size={14} /> : <Plus size={14} />}
                {t(editingProvider ? "Save provider" : "Add provider")}
              </button>
            </div>
          </form>
        </section>
      </FloatingWindow>
    </>
  );
}
