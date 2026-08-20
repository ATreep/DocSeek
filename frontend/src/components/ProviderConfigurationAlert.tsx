import { Settings2, TriangleAlert, X } from "lucide-react";
import FloatingWindow from "./FloatingWindow";
import { useDocSeekTranslation } from "../i18n";

export type MissingProviderRoute = {
  key: string;
  label: string;
  provider_type: "llm" | "embedding";
};

type ProviderConfigurationAlertProps = {
  open: boolean;
  missingRoutes: MissingProviderRoute[];
  canConfigure: boolean;
  onClose: () => void;
  onConfigure: () => void;
};

const ROUTE_DISPLAY_NAMES: Record<string, string> = {
  dg_agent_route: "Definition Generation Agent",
  ga_agent_route: "Group Arrangement Agent",
  entity_agent_route: "Entity Extraction Agent",
  shared_embedding_route: "Shared Embedding Model",
};

export default function ProviderConfigurationAlert({
  open,
  missingRoutes,
  canConfigure,
  onClose,
  onConfigure,
}: ProviderConfigurationAlertProps) {
  const { t } = useDocSeekTranslation();
  return (
    <FloatingWindow
      open={open}
      className="modal-backdrop"
      role="presentation"
    >
      <section
        className="upload-modal provider-alert-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t("Model providers required")}
      >
        <header className="provider-alert-header">
          <span className="provider-alert-icon" aria-hidden="true">
            <TriangleAlert size={20} />
          </span>
          <div>
            <span className="eyebrow">{t("IMPORT SETUP REQUIRED")}</span>
            <h2>{t("Configure model providers")}</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label={t("Close model provider alert")}
            title={t("Close")}
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </header>
        <p className="provider-alert-copy">
          {t("Add Property needs a configured provider for every import agent.")}
        </p>
        <ul className="provider-alert-list" aria-label={t("Missing model providers")}>
          {missingRoutes.map((route) => (
            <li key={route.key}>
              <span>{t(ROUTE_DISPLAY_NAMES[route.key] || route.label)}</span>
              <small>{t(route.provider_type === "llm" ? "LLM" : "Embedding")}</small>
            </li>
          ))}
        </ul>
        {!canConfigure && (
          <p className="provider-alert-permission">
            {t("Ask an administrator to assign these provider routes.")}
          </p>
        )}
        <footer className="modal-actions provider-alert-actions">
          <button type="button" className="secondary-button" onClick={onClose}>
            {t("Cancel")}
          </button>
          <button
            type="button"
            className="primary-button"
            aria-label={t("Configure providers")}
            disabled={!canConfigure}
            onClick={onConfigure}
          >
            <Settings2 size={15} /> {t("Configure providers")}
          </button>
        </footer>
      </section>
    </FloatingWindow>
  );
}
