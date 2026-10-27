// Ficha de prospección basada en evidencia (T08/T03 prospection brief).
// Presenta condiciones revisadas, cobertura, señales, exclusiones, preguntas
// pendientes y fuentes. No crea scores, no infiere intención de compra y no
// habilita ninguna acción que no existiera antes.

import { useMemo } from "react";
import { buildProspectingBrief } from "./prospectingBrief";
import type { FitEvidenceSource } from "./api";
import type {
  FitClassification,
  FitDecision,
  FitEvaluationContext,
  FitEvaluationRead,
} from "./types";

interface FitResultPanelProps {
  leadId: string;
  authorityState: "loading" | "ready" | "unavailable";
  evaluation: FitEvaluationRead | null;
  context: FitEvaluationContext | null;
  sources?: Array<FitEvidenceSource & { safe_url: string | null }>;
  onResearch?: () => void;
}

const CLASSIFICATION_LABELS: Record<FitClassification, string> = {
  qualified: "Cumple condiciones revisadas",
  needs_more_research: "Datos por confirmar",
  not_qualified: "No cumple condiciones revisadas",
};

const ASSESSMENT_LABELS: Record<string, string> = {
  matches: "Respaldada",
  does_not_match: "No respaldada",
  not_established: "No establecida",
  conflicting: "En conflicto",
};

const GROUP_TITLES: Record<string, string> = {
  profile: "Criterios del perfil",
  signals: "Señales comerciales",
  exclusions: "Exclusiones",
  other: "Otras condiciones revisadas",
};

const GROUP_ORDER = ["profile", "signals", "exclusions", "other"] as const;

function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function formatCoverage(coverage: number | null): string {
  if (coverage === null) return "Cobertura de criterios: no disponible";
  return `Cobertura de criterios: ${coverage.toFixed(1).replace(/\.0$/, "")}%`;
}

export function FitResultPanel({
  leadId,
  authorityState,
  evaluation,
  context,
  sources = [],
  onResearch,
}: FitResultPanelProps) {
  const view = useMemo(
    () => buildProspectingBrief({ leadId, authorityState, context, evaluation, sources }),
    [leadId, authorityState, context, evaluation, sources],
  );
  const sourceById = useMemo(() => {
    const map = new Map<string, FitEvidenceSource & { safe_url: string | null }>();
    for (const source of sources) map.set(source.evidence_id, source);
    return map;
  }, [sources]);

  if (view.state === "loading") {
    return <div className="card p-4 text-slate-500">Consultando la autoridad de encaje…</div>;
  }
  if (view.state === "unavailable") {
    return (
      <div className="card p-4 text-slate-600">
        Ficha de prospección no disponible: el detalle requiere la política vigente que expone el servidor.
      </div>
    );
  }
  if (view.state === "pending") {
    return (
      <div className="card p-4 text-slate-600">
        La evaluación está en curso. Todavía no hay una conclusión comercial; un estado técnico no es un rechazo.
      </div>
    );
  }
  if (view.state === "failed") {
    return (
      <div className="card p-4 text-slate-700" role="alert">
        <p>
          La evaluación no pudo completarse{evaluation?.error_code ? ` (${evaluation.error_code})` : ""}. Es un fallo
          técnico y no un rechazo comercial.
        </p>
      </div>
    );
  }
  if (view.state === "stale") {
    return (
      <div className="card p-4 text-slate-700" role="status">
        <p>
          Esta ficha no está vigente: la política o las fuentes cambiaron desde que se evaluó. Actualiza la autoridad
          antes de usarla; aquí no se muestra ninguna conclusión vigente.
        </p>
      </div>
    );
  }

  const groups = GROUP_ORDER
    .map((group) => ({ group, criteria: view.criteria.filter((criterion) => criterion.group === group) }))
    .filter((entry) => entry.criteria.length > 0);

  return (
    <section className="card overflow-hidden p-0" aria-labelledby="fit-result-heading">
      <div className="border-b border-slate-100 bg-slate-50/80 p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="eyebrow">Ficha de prospección basada en evidencia</p>
            <h3 id="fit-result-heading" className="mt-1 text-lg font-bold text-slate-900">
              Condiciones revisadas y cobertura
            </h3>
            <p className="mt-1 text-sm text-slate-500">
              Reglas explicables del motor. La cobertura no es encaje, exactitud ni probabilidad de venta.
            </p>
          </div>
          <span className="status status-completed">Vigente</span>
        </div>
      </div>

      <div className="space-y-5 p-5">
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs uppercase tracking-wide text-gray-500">Resultado</p>
            <p className="mt-1 text-lg font-bold text-slate-900">
              {view.classification ? CLASSIFICATION_LABELS[view.classification] : "Sin clasificar"}
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs uppercase tracking-wide text-gray-500">Puntuación según reglas</p>
            <p className="mt-1 text-lg font-bold text-slate-900">
              {view.fitScore === null ? "No calculada" : `${view.fitScore}/100`}
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs uppercase tracking-wide text-gray-500">Cobertura de criterios</p>
            <p className="mt-1 text-lg font-bold text-slate-900">
              {view.coverage === null ? "No disponible" : `${view.coverage}%`}
            </p>
            <p className="mt-1 text-xs text-slate-500">{formatCoverage(view.coverage)}</p>
          </div>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs uppercase tracking-wide text-gray-500">Momento de compra</p>
          <p className="mt-1 text-sm text-slate-700">Momento de compra: no evaluado en esta versión</p>
          <p className="mt-1 text-xs text-slate-500">
            Una necesidad posible no demuestra intención. No hay en esta ficha una predicción de compra.
          </p>
        </div>

        {groups.map(({ group, criteria }) => (
          <div key={group} className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs uppercase tracking-wide text-gray-600">{GROUP_TITLES[group]}</p>
            <ul className="mt-2 divide-y divide-slate-100">
              {criteria.map((criterion) => (
                <li key={criterion.id} className="py-2 text-sm">
                  <div className="flex flex-wrap justify-between gap-2">
                    <span className="font-medium text-slate-800">{criterion.description}</span>
                    <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold text-slate-700">
                      {ASSESSMENT_LABELS[criterion.result] ?? criterion.result}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    Tipo: {criterion.kind === "required" ? "Indispensable" : criterion.kind === "exclusion" ? "Exclusión" : "Preferencia"}
                  </p>
                  <details className="mt-1">
                    <summary className="cursor-pointer text-xs text-slate-500">Detalles del juicio</summary>
                    <p className="mt-1 text-xs text-slate-600">Confianza del juicio: {Math.round(criterion.confidence * 100)}%</p>
                    <ul className="mt-1 space-y-1 text-xs text-slate-600">
                      {criterion.evidenceIds.length === 0 && <li>Sin evidencia vinculada registrada.</li>}
                      {criterion.evidenceIds.map((id) => {
                        const source = sourceById.get(id);
                        if (!source) return <li key={id}>Fuente no disponible: {id}</li>;
                        const href = safeExternalUrl(source.safe_url);
                        const label = source.source_title || source.source_type;
                        return (
                          <li key={id}>
                            {href ? (
                              <a href={href} target="_blank" rel="noopener noreferrer" className="underline">{label}</a>
                            ) : (
                              <span>{label}</span>
                            )}
                            {source.excerpt ? `: ${source.excerpt}` : ""}
                          </li>
                        );
                      })}
                    </ul>
                  </details>
                </li>
              ))}
            </ul>
          </div>
        ))}

        {view.questions.length > 0 && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
            <p className="text-xs uppercase tracking-wide text-amber-800">Preguntas pendientes</p>
            <ul className="mt-2 space-y-1 text-sm text-amber-800 list-disc pl-5">
              {view.questions.map((question) => <li key={question.criterionId}>{question.text}</li>)}
            </ul>
            <p className="mt-2 text-xs text-amber-700">
              Son preguntas de verificación, no búsquedas automáticas ni conclusiones sobre el prospecto.
            </p>
          </div>
        )}

        {sources.length > 0 && (
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs uppercase tracking-wide text-gray-600">Fuentes descargadas</p>
            <ul className="mt-2 space-y-1 text-sm text-slate-700">
              {sources.map((source) => {
                const href = safeExternalUrl(source.safe_url);
                const label = source.source_title || source.source_type;
                return (
                  <li key={source.evidence_id}>
                    {href ? (
                      <a href={href} target="_blank" rel="noopener noreferrer" className="underline">{label}</a>
                    ) : (
                      <span>{label}</span>
                    )}
                    {source.excerpt ? `: ${source.excerpt}` : ""}
                  </li>
                );
              })}
            </ul>
            <p className="mt-2 text-xs text-slate-500">
              El endpoint de fuentes puede devolver sólo parte de la ancestry: eso no prueba que un juicio sea falso.
            </p>
          </div>
        )}

        {evaluation?.result && (
          <details className="rounded-xl border border-slate-200 bg-white p-4">
            <summary className="cursor-pointer text-xs uppercase tracking-wide text-gray-600">Detalles técnicos</summary>
            <p className="mt-2 text-xs text-slate-600">Motor: {evaluation.engine_version} · Cobertura: {formatCoverage(view.coverage)}</p>
            {view.policyVersion !== null && (
              <p className="mt-1 text-xs text-slate-600">Política revisada: versión {view.policyVersion}</p>
            )}
            {view.policyApprovedAt && (
              <p className="mt-1 text-xs text-slate-600">Aprobada: {view.policyApprovedAt}</p>
            )}
            <ul className="mt-2 space-y-1 text-xs text-slate-600 list-disc pl-5">
              {(evaluation.result.reasons as string[] | undefined)?.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          </details>
        )}

        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-xs uppercase tracking-wide text-gray-500">Contacto</p>
          <p className="mt-1 text-sm text-slate-700">
            {evaluation?.result?.contact_ready === true ? "Canal autorizado disponible" : "Sin canal autorizado confirmado"}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            El readiness es independiente del encaje: un lead que cumple condiciones revisadas puede carecer de canal.
          </p>
        </div>

        {onResearch && (
          <div className="mt-4 flex gap-3">
            <button type="button" className="btn-primary" onClick={() => onResearch()}>Investigar más</button>
          </div>
        )}
      </div>
    </section>
  );
}

interface FitDecisionProps {
  decision: FitDecision;
}

export function FitDecisionBadge({ decision }: FitDecisionProps) {
  const label = CLASSIFICATION_LABELS[decision.classification] ?? "Sin clasificar";
  const tone =
    decision.classification === "qualified"
      ? "text-emerald-700 bg-emerald-50"
      : decision.classification === "not_qualified"
        ? "text-rose-700 bg-rose-50"
        : "text-amber-700 bg-amber-50";
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${tone}`}>
      {label}
    </span>
  );
}
