import { useCallback, useEffect, useState } from "react";
import { RefreshCw, ScrollText } from "lucide-react";
import { request } from "../api";
import { useDocSeekTranslation } from "../i18n";

type LLMInvocationLog = {
  id: string;
  request_time: string;
  response_time: string;
  duration_ms: number;
  model: string;
  route_key: string | null;
  profile_id: string | null;
  status: "success" | "error" | "cancelled";
  request_prompt: string;
  response_output: string;
};

const LATEST_LOG_LIMIT = 50;

const ROUTE_LABELS: Record<string, string> = {
  dg_agent_route: "Definition Generation Agent",
  ga_agent_route: "Group Arrangement Agent",
  pgb_agent_route: "Property Graph Building Agent",
  entity_agent_route: "Entity Extraction Agent",
  ai_query_route: "AI Query LLM",
  provider_validation: "Model provider validation",
};

export default function LLMInvocationLogs({ active }: { active: boolean }) {
  const { t, i18n } = useDocSeekTranslation();
  const [logs, setLogs] = useState<LLMInvocationLog[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadLogs = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const next = await request<LLMInvocationLog[]>(
        `/system/llm-invocations?limit=${LATEST_LOG_LIMIT}`,
      );
      setLogs(next.slice(0, LATEST_LOG_LIMIT));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("Unable to load LLM invocation logs"),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (active && logs === null && !loading) void loadLogs();
  }, [active, loadLogs, loading, logs]);

  function formatTime(value: string) {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? value
      : date.toLocaleString(i18n.language, { hourCycle: "h23" });
  }

  function routeLabel(routeKey: string | null) {
    return t(routeKey ? ROUTE_LABELS[routeKey] || "Unassigned LLM route" : "Unassigned LLM route");
  }

  function statusLabel(status: LLMInvocationLog["status"]) {
    if (status === "success") return t("Completed");
    if (status === "cancelled") return t("Interrupted");
    return t("Failed");
  }

  if (!active) return null;

  return (
    <div className="settings-content">
      <section className="settings-section llm-log-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">{t("LLM AUDIT")}</span>
            <h3>{t("LLM invocation logs")}</h3>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label={t("Refresh LLM invocation logs")}
            title={t("Refresh LLM invocation logs")}
            disabled={loading}
            onClick={() => void loadLogs()}
          >
            <RefreshCw size={15} />
          </button>
        </div>
        <p className="muted llm-log-description">
          {t("Each LLM request records its prompt, output, request time, and response time.")}
        </p>
        {error ? <div className="error-banner llm-log-error">{error}</div> : null}
        {loading && logs === null ? (
          <p className="muted">{t("Loading LLM invocation logs...")}</p>
        ) : null}
        {logs?.length === 0 ? (
          <div className="llm-log-empty">
            <ScrollText size={20} />
            <p>{t("No LLM invocations have been recorded yet.")}</p>
          </div>
        ) : null}
        {logs && logs.length > 0 ? (
          <div className="llm-log-list">
            {logs.map((log) => (
              <details className="llm-log-entry" key={log.id}>
                <summary>
                  <span>
                    <strong>{routeLabel(log.route_key)}</strong>
                    <small>
                      {formatTime(log.request_time)} · {log.model}
                    </small>
                  </span>
                  <span className={`llm-log-status ${log.status}`}>
                    {statusLabel(log.status)} · {log.duration_ms} {t("ms")}
                  </span>
                </summary>
                <div className="llm-log-detail">
                  <dl className="llm-log-timing">
                    <div>
                      <dt>{t("Request time")}</dt>
                      <dd>{formatTime(log.request_time)}</dd>
                    </div>
                    <div>
                      <dt>{t("Response time")}</dt>
                      <dd>{formatTime(log.response_time)}</dd>
                    </div>
                  </dl>
                  <section>
                    <h4>{t("Request prompt")}</h4>
                    <pre>{log.request_prompt}</pre>
                  </section>
                  <section>
                    <h4>{t("LLM response output")}</h4>
                    <pre>{log.response_output || t("No response output")}</pre>
                  </section>
                </div>
              </details>
            ))}
          </div>
        ) : null}
      </section>
    </div>
  );
}
