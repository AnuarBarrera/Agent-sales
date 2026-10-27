import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../../lib/api";
import { PolicyReviewPanel } from "../leadFit/PolicyReviewPanel";

type Channel = "email" | "phone" | "whatsapp";
interface Signal {
  id: string; kind: "fit" | "opportunity"; direction: "positive" | "negative";
  description: string; evidence_hint: string;
}
interface Content {
  contract_version: "4.0"; kind: "commercial_context"; business_name: string | null;
  offer: { summary: string | null; problem_solved: string | null; differentiators: string[]; price_context: string | null } | null;
  icp: { industries: string[]; geographies: string[]; buyer_roles: string[]; desired_traits: string[]; exclusions: string[] } | null;
  signals: Signal[];
  communication: { tone: string | null; call_to_action: string | null; allowed_channels: Channel[]; forbidden_claims: string[]; contact_restrictions: string[]; pricing_policy: "omit" } | null;
}
interface CommercialPlaybook {
  id: string; version: number; status: string; content_contract_version: string;
  structured_content: Content; content_digest: string | null;
  commercial_readiness?: { ready_for_approval: boolean; missing_fields: string[]; blockers: string[] } | null;
  source_payload?: { pasted_text?: string | null; source_document_manifest?: { id: string; filename: string }[] };
}
interface Profile { business_name?: string; offer_summary?: string | null; differentiators?: string | null; sales_constraints?: string | null; price_context?: string | null }
interface DocumentChoice { id: string; original_filename: string; eligibility_status: string }

function editable(content?: Partial<Content> | null): Content {
  return {
    contract_version: "4.0", kind: "commercial_context", business_name: content?.business_name ?? null,
    offer: content?.offer ?? { summary: null, problem_solved: null, differentiators: [], price_context: null },
    icp: content?.icp ?? { industries: [], geographies: [], buyer_roles: [], desired_traits: [], exclusions: [] },
    signals: content?.signals ?? [],
    communication: content?.communication ?? { tone: null, call_to_action: null, allowed_channels: [], forbidden_claims: [], contact_restrictions: [], pricing_policy: "omit" },
  };
}
const text = (value: string): string | null => value.trim() || null;
const lines = (values: string[]) => values.map((value) => value.trim()).filter(Boolean);
function clean(content: Content): Content {
  return {
    ...content, business_name: text(content.business_name ?? ""),
    offer: content.offer && { ...content.offer, summary: text(content.offer.summary ?? ""), problem_solved: text(content.offer.problem_solved ?? ""), differentiators: lines(content.offer.differentiators), price_context: text(content.offer.price_context ?? "") },
    icp: content.icp && Object.fromEntries(Object.entries(content.icp).map(([key, values]) => [key, lines(values)])) as Content["icp"],
    communication: content.communication && { ...content.communication, allowed_channels: content.communication.allowed_channels.filter((channel) => channel === "email" || channel === "phone"), tone: text(content.communication.tone ?? ""), call_to_action: text(content.communication.call_to_action ?? ""), forbidden_claims: lines(content.communication.forbidden_claims), contact_restrictions: lines(content.communication.contact_restrictions) },
  };
}

export function CommercialContextPanel({ allowAssisted = true, demoMode = false }: { allowAssisted?: boolean; demoMode?: boolean }) {
  const [versions, setVersions] = useState<CommercialPlaybook[]>([]);
  const [selected, setSelected] = useState<CommercialPlaybook | null>(null);
  const [content, setContent] = useState<Content>(() => editable());
  const [documents, setDocuments] = useState<DocumentChoice[]>([]);
  const [documentId, setDocumentId] = useState("");
  const [pastedText, setPastedText] = useState("");
  const [dirty, setDirty] = useState(true);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirmedSnapshot, setConfirmedSnapshot] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [needsProfileRefresh, setNeedsProfileRefresh] = useState(false);
  const requestKey = useRef(globalThis.crypto.randomUUID());

  function choose(playbook: CommercialPlaybook | null) {
    setSelected(playbook); setNeedsProfileRefresh(false);
    if (playbook) setContent(editable(playbook.structured_content));
    setDirty(!playbook); setConfirmedSnapshot(""); setMessage(""); setError("");
    setPastedText(""); setDocumentId(""); requestKey.current = globalThis.crypto.randomUUID();
  }
  useEffect(() => {
    let active = true;
    Promise.all([
      apiFetch<CommercialPlaybook[]>("/sales-playbooks"),
      apiFetch<Profile>("/seller-profile").catch(() => null),
      demoMode ? Promise.resolve([]) : apiFetch<DocumentChoice[]>("/sales-playbooks/source-documents/selection").catch(() => []),
    ]).then(([all, profile, available]) => {
      if (!active) return;
      const contexts = all.filter((item) => item.content_contract_version === "4.0");
      setVersions(contexts); setDocuments(available ?? []);
      if (contexts[0]) choose(contexts[0]);
      else if (profile) {
        const initial = editable({ business_name: profile.business_name ?? null });
        initial.offer = { summary: profile.offer_summary ?? null, problem_solved: null, differentiators: profile.differentiators ? [profile.differentiators] : [], price_context: profile.price_context ?? null };
        initial.communication!.contact_restrictions = profile.sales_constraints ? [profile.sales_constraints] : [];
        setContent(initial);
      }
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "No se pudo cargar el contexto"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [demoMode]);

  function change(next: Content) {
    setContent(next); setDirty(true); setConfirmedSnapshot(""); setMessage("");
    requestKey.current = globalThis.crypto.randomUUID();
  }
  function accept(playbook: CommercialPlaybook) {
    if (playbook.content_contract_version !== "4.0" || playbook.structured_content?.kind !== "commercial_context") throw new Error("El servidor devolvió un contrato incompatible");
    setVersions((old) => [playbook, ...old.filter((item) => item.id !== playbook.id)]);
    choose(playbook);
  }
  async function save(assisted = false) {
    setBusy(true); setError(""); setMessage(""); setConfirmedSnapshot("");
    try {
      // A demo save captures the current seller profile in a new draft; it never
      // rewrites the provenance of an existing or approved version.
      const editing = !demoMode && selected?.status === "draft" && !assisted;
      const result = await apiFetch<CommercialPlaybook>(editing ? `/sales-playbooks/${selected.id}/commercial-context` : "/sales-playbooks/commercial-context/drafts", {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify(editing ? { expected_digest: selected.content_digest, content: clean(content) } : {
          content: clean(content), pasted_text: demoMode ? null : text(pastedText), source_document_ids: !demoMode && documentId ? [documentId] : [],
          idempotency_key: requestKey.current, assisted,
        }),
      });
      accept(result); setMessage("Borrador guardado. Revisa los cambios antes de aprobar.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "No se pudo guardar el borrador"); }
    finally { setBusy(false); }
  }
  const snapshotKey = selected?.content_digest ? `${selected.id}:${selected.content_digest}` : "";
  const canReview = selected?.status === "draft" && !dirty && !needsProfileRefresh && selected.commercial_readiness?.ready_for_approval === true && /^[a-f0-9]{64}$/.test(selected.content_digest ?? "");
  async function approve() {
    if (!canReview || !selected || confirmedSnapshot !== snapshotKey) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await apiFetch<CommercialPlaybook>(`/sales-playbooks/${selected.id}/approve`, {
        method: "POST", body: JSON.stringify({ confirmed: true, content_digest: selected.content_digest }),
      });
      if (result.status !== "approved") throw new Error("El servidor no confirmó la aprobación");
      accept(result); setMessage("Contexto aprobado. Habilita análisis y borradores, no envíos.");
    } catch (reason) {
      if (demoMode && reason instanceof Error && reason.message.includes("Seller profile has changed since commercial context capture")) {
        setNeedsProfileRefresh(true);
        setError("El perfil de tu negocio cambió después de guardar estas condiciones. Actualiza el vínculo y vuelve a revisar; conservaremos lo que escribiste.");
      } else setError(reason instanceof Error ? reason.message : "No se pudo aprobar; vuelve a revisar");
    }
    finally { setConfirmedSnapshot(""); setBusy(false); }
  }
  const readOnly = loading || busy || (selected !== null && selected.status !== "draft");
  const field = (label: string, value: string | null, onChange: (value: string) => void, multiline = false) => (
    <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">{label}
      {multiline ? <textarea className="input min-h-20" value={value ?? ""} disabled={readOnly} onChange={(event) => onChange(event.target.value)} /> : <input className="input" value={value ?? ""} disabled={readOnly} onChange={(event) => onChange(event.target.value)} />}
    </label>
  );
  const listField = (label: string, values: string[], onChange: (values: string[]) => void) => field(label, values.join("\n"), (value) => onChange(value.split("\n")), true);

  return <div className="mx-auto max-w-5xl space-y-6">
    <header><h1 className="page-title">Contexto comercial</h1><p className="page-subtitle">{demoMode ? "Revisa tu oferta, a quién buscar y cómo iniciar la conversación. Tú apruebas las condiciones; no se envían mensajes." : "Define a quién ayudar, qué señales buscar y cómo iniciar una conversación. La IA propone hipótesis revisables, no evidencia verificada ni intención de compra; tú editas y apruebas."}</p></header>
    {error && <p role="alert" className="rounded-xl bg-red-50 p-4 text-red-800">{error}</p>}
    {message && <p role="status" className="rounded-xl bg-teal-50 p-4 text-teal-800">{message}</p>}
    {loading && <p role="status">Cargando contexto…</p>}
    {selected?.status === "approved" && <PolicyReviewPanel compact={demoMode} key={selected.id} playbookId={selected.id} opportunitySourcePointers={selected.structured_content.signals.flatMap((signal, index) => signal.kind === "opportunity" && signal.direction === "positive" ? [`commercial_context.signals[${index}]`] : [])} />}
    {!demoMode && <div className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-sm">Versión del contexto<select className="input" value={selected?.id ?? "new"} disabled={busy || loading} onChange={(event) => choose(versions.find((item) => item.id === event.target.value) ?? null)}><option value="new">Nueva versión</option>{versions.map((item) => <option key={item.id} value={item.id}>Versión {item.version} · {item.status === "approved" ? "Aprobada" : item.status === "draft" ? "Borrador" : item.status}</option>)}</select></label>
      <button className="btn-secondary" disabled={busy || loading} onClick={() => choose(null)}>Nueva versión editable</button>
    </div>}
    {demoMode && selected?.status === "approved" && <button className="btn-secondary" disabled={busy} onClick={() => choose(null)}>Editar condiciones</button>}
    <section className="card space-y-4" aria-labelledby="commercial-offer"><h2 id="commercial-offer" className="section-title">Oferta del negocio</h2>
      {field("Nombre del negocio", content.business_name, (value) => change({ ...content, business_name: value }))}
      {field("Qué vendes", content.offer!.summary, (value) => change({ ...content, offer: { ...content.offer!, summary: value } }), true)}
      {field("Problema que resuelves", content.offer!.problem_solved, (value) => change({ ...content, offer: { ...content.offer!, problem_solved: value } }), true)}
      {listField("Diferenciadores (uno por línea)", content.offer!.differentiators, (values) => change({ ...content, offer: { ...content.offer!, differentiators: values } }))}
      {field("Contexto de precio (no se incluirá en mensajes)", content.offer!.price_context, (value) => change({ ...content, offer: { ...content.offer!, price_context: value } }))}
    </section>
    <section className="card space-y-4" aria-labelledby="commercial-icp"><h2 id="commercial-icp" className="section-title">Cliente ideal</h2><p className="text-sm text-slate-500">Escribe un elemento por línea. Sector y zona describen encaje, no necesidad ni intención de compra. Lo que no sabes puede quedar pendiente.</p>
      {([['industries', 'Sectores'], ['geographies', 'Zonas atendidas'], ['buyer_roles', 'Roles de compra'], ['desired_traits', 'Características deseadas'], ['exclusions', 'A quién no atiendes']] as const).map(([key, label]) => <div key={key}>{listField(label, content.icp![key], (values) => change({ ...content, icp: { ...content.icp!, [key]: values } }))}</div>)}
    </section>
    <section className="card space-y-4" aria-labelledby="commercial-signals"><h2 id="commercial-signals" className="section-title">Señales de encaje y oportunidades</h2><p className="text-sm text-slate-500">Las oportunidades son opcionales para prospectar. Puedes dejarlas como preferencia o elegir Indispensable en las condiciones de encaje. Una oportunidad no demuestra intención de compra; revisa cada hipótesis y cómo podría comprobarse.</p>
      <p className="text-sm text-slate-500">No encontrar un dato no demuestra su ausencia. Tener un sitio web funcional no demuestra necesidad de reemplazarlo. Define tus propias condiciones y exclusiones; no se aplican reglas fijas por giro.</p>
      {content.signals.map((signal, index) => {
        const update = (patch: Partial<Signal>) => change({ ...content, signals: content.signals.map((item, i) => i === index ? { ...item, ...patch } : item) });
        return <fieldset key={index} className="space-y-3 border-l-2 border-teal-200 pl-4"><legend className="text-sm font-semibold">Señal {index + 1}</legend>
          {field(`Identificador de señal ${index + 1}`, signal.id, (value) => update({ id: value }))}
          <label className="flex flex-col gap-1 text-sm">Tipo de señal {index + 1}<select className="input" disabled={readOnly} value={signal.kind} onChange={(event) => update({ kind: event.target.value as Signal["kind"] })}><option value="fit">Encaje</option><option value="opportunity">Oportunidad</option></select></label>
          <label className="flex flex-col gap-1 text-sm">Dirección de señal {index + 1}<select className="input" disabled={readOnly} value={signal.direction} onChange={(event) => update({ direction: event.target.value as Signal["direction"] })}><option value="positive">Positiva</option><option value="negative">Negativa</option></select></label>
          {field(`Descripción de señal ${index + 1}`, signal.description, (value) => update({ description: value }), true)}
          {field(`Cómo comprobar señal ${index + 1}`, signal.evidence_hint, (value) => update({ evidence_hint: value }), true)}
          <button className="btn-secondary" disabled={readOnly} onClick={() => change({ ...content, signals: content.signals.filter((_, i) => i !== index) })}>Quitar señal {index + 1}</button>
        </fieldset>;
      })}
      <button className="btn-secondary" disabled={readOnly || content.signals.length >= 10} onClick={() => change({ ...content, signals: [...content.signals, { id: "", kind: "fit", direction: "positive", description: "", evidence_hint: "" }] })}>Agregar señal</button>
      <button className="btn-secondary" disabled={readOnly || content.signals.length >= 10} onClick={() => change({ ...content, signals: [...content.signals, { id: "", kind: "opportunity", direction: "positive", description: "", evidence_hint: "" }] })}>Agregar oportunidad observable</button>
    </section>
    <section className="card space-y-4" aria-labelledby="commercial-language"><h2 id="commercial-language" className="section-title">Cómo hablarle</h2>
      {field("Tono", content.communication!.tone, (value) => change({ ...content, communication: { ...content.communication!, tone: value } }))}
      {field("Llamada a la acción", content.communication!.call_to_action, (value) => change({ ...content, communication: { ...content.communication!, call_to_action: value } }))}
      {content.communication!.allowed_channels.includes("whatsapp") && <p className="text-sm text-amber-800">Esta versión conserva WhatsApp como canal anterior. Los nuevos borradores sólo admiten email y llamada; al guardar una versión editable se retirará ese canal y deberás aprobar los canales elegidos.</p>}
      <fieldset><legend className="mb-2 text-sm font-medium">Canales autorizados para borradores</legend><div className="flex gap-4">{(["email", "phone"] as Channel[]).map((channel) => <label key={channel} className="flex items-center gap-2"><input type="checkbox" disabled={readOnly} checked={content.communication!.allowed_channels.includes(channel)} onChange={(event) => change({ ...content, communication: { ...content.communication!, allowed_channels: event.target.checked ? [...content.communication!.allowed_channels, channel] : content.communication!.allowed_channels.filter((item) => item !== channel) } })} />{channel === "email" ? "Email" : "Pitch de llamada"}</label>)}</div></fieldset>
      {listField("Afirmaciones prohibidas (una por línea)", content.communication!.forbidden_claims, (values) => change({ ...content, communication: { ...content.communication!, forbidden_claims: values } }))}
      {listField("Restricciones de contacto (una por línea)", content.communication!.contact_restrictions, (values) => change({ ...content, communication: { ...content.communication!, contact_restrictions: values } }))}
      <p className="text-sm text-slate-500">Los borradores omiten precios y siempre requieren revisión humana.</p>
      <p className="text-sm text-slate-500">El contacto requiere un correo o teléfono público atribuible: puede provenir de Maps o una ficha comercial, sin sitio propio. El teléfono permite preparar un pitch de llamada, no realizarla automáticamente.</p>
    </section>
    {!demoMode && <section className="card space-y-4"><h2 className="section-title">Fuentes opcionales para una nueva versión</h2>
      {selected?.source_payload?.pasted_text && <label className="flex flex-col gap-1 text-sm">Texto capturado en esta versión<textarea className="input min-h-24" readOnly value={selected.source_payload.pasted_text} /></label>}
      {selected?.source_payload?.source_document_manifest?.map((source) => <p key={source.id} className="text-sm">Documento vinculado: {source.filename}</p>)}
      <label className="flex flex-col gap-1 text-sm">Documento ya normalizado<select className="input" disabled={readOnly || !!selected} value={documentId} onChange={(event) => { setDocumentId(event.target.value); setPastedText(""); setConfirmedSnapshot(""); requestKey.current = globalThis.crypto.randomUUID(); }}><option value="">Sin documento</option>{documents.filter((item) => item.eligibility_status === "eligible").map((item) => <option key={item.id} value={item.id}>{item.original_filename}</option>)}</select></label>
      <label className="flex flex-col gap-1 text-sm">Texto de referencia<textarea className="input min-h-24" disabled={readOnly || !!selected} value={pastedText} onChange={(event) => { setPastedText(event.target.value); setDocumentId(""); setConfirmedSnapshot(""); requestKey.current = globalThis.crypto.randomUUID(); }} /></label>
      <p className="text-sm text-slate-500">Las fuentes se vinculan al crear la versión. Para cambiarlas, crea una nueva versión editable.</p>
    </section>}
    <section className="card space-y-4" aria-labelledby="commercial-review"><h2 id="commercial-review" className="section-title">Revisión y aprobación</h2>
      {dirty ? <p>Guarda el borrador para comprobar los campos pendientes.</p> : selected?.commercial_readiness && <div><p>{selected.commercial_readiness.ready_for_approval ? "Contenido completo para revisión." : "Completa los datos pendientes:"}</p><ul className="mt-2 list-disc pl-5 text-sm">{[...selected.commercial_readiness.missing_fields, ...selected.commercial_readiness.blockers].map((item) => <li key={item}>{item}</li>)}</ul></div>}
      <div className="flex flex-wrap gap-3"><button className="btn-primary" disabled={readOnly} onClick={() => void save(false)}>Guardar borrador</button>{allowAssisted && !demoMode && <button className="btn-secondary" disabled={readOnly || !!selected} onClick={() => void save(true)}>Sugerir con IA</button>}</div>
      {needsProfileRefresh && <div className="rounded-xl border border-amber-300 bg-amber-50 p-4"><p>Se creará un borrador con el perfil actual y tus condiciones. Guardar no aprueba ni inicia búsquedas.</p><button type="button" className="btn-secondary mt-3" disabled={busy} onClick={() => void save(false)}>Actualizar vínculo con mi negocio</button></div>}
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" disabled={!canReview || busy} checked={!!snapshotKey && confirmedSnapshot === snapshotKey} onChange={(event) => setConfirmedSnapshot(event.target.checked ? snapshotKey : "")} />Revisé este contenido y sus fuentes. Autorizo análisis y borradores, no envíos.</label>
      <button className="btn-primary" disabled={!canReview || busy || confirmedSnapshot !== snapshotKey} onClick={() => void approve()}>Aprobar contexto</button>
    </section>
  </div>;
}
