import { useEffect, useState } from "react";
import { apiFetch } from "../../lib/api";

interface SellerProfile {
  id?: string;
  workspace_id?: string;
  business_name: string;
  description: string | null;
  offer_summary: string | null;
  target_customer_summary: string | null;
  location_or_market: string | null;
  price_context: string | null;
  primary_goal: string | null;
  differentiators: string | null;
  sales_constraints: string | null;
}

type ProfileForm = Omit<SellerProfile, "id" | "workspace_id">;

const emptyForm: ProfileForm = {
  business_name: "",
  description: "",
  offer_summary: "",
  target_customer_summary: "",
  location_or_market: "",
  price_context: "",
  primary_goal: "",
  differentiators: "",
  sales_constraints: "",
};

export function SellerProfilePage() {
  const [form, setForm] = useState<ProfileForm>(emptyForm);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let active = true;
    apiFetch<SellerProfile>("/seller-profile")
      .then((profile) => {
        if (active) setForm(toForm(profile));
      })
      .catch((reason: unknown) => {
        if (active && !(reason instanceof Error && /not found/i.test(reason.message))) {
          setError(reason instanceof Error ? reason.message : "No se pudo cargar el perfil");
        }
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  function updateField(field: keyof ProfileForm, value: string) {
    setSaved(false);
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSaved(false);
    setIsSaving(true);
    try {
      await apiFetch<SellerProfile>("/seller-profile", {
        method: "PUT",
        body: JSON.stringify(form),
      });
      setSaved(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo guardar el perfil");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header>
        <p className="eyebrow">Configuración comercial</p>
        <h1 className="page-title">Mi negocio</h1>
        <p className="page-subtitle">
          Define la información que tu equipo necesita para entender la oferta y preparar mejores conversaciones.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="space-y-5">
        {error && <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">{error}</p>}
        {saved && <p className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700" role="status">Perfil guardado</p>}

        <section className="card space-y-5" aria-labelledby="offer-heading">
          <div>
            <h2 id="offer-heading" className="section-title">Qué vendes</h2>
            <p className="mt-1 text-sm text-slate-500">Una descripción clara ayuda a mantener el mismo mensaje en todo el equipo.</p>
          </div>
          <Field
            id="business-name"
            label="Nombre del negocio"
            value={form.business_name}
            onChange={(value) => updateField("business_name", value)}
            required
            disabled={isLoading || isSaving}
          />
          <TextField
            id="description"
            label="Descripción breve"
            value={form.description}
            onChange={(value) => updateField("description", value)}
            placeholder="Qué hace tu negocio y qué problema resuelve"
            disabled={isLoading || isSaving}
          />
          <TextField
            id="offer-summary"
            label="Oferta principal"
            value={form.offer_summary}
            onChange={(value) => updateField("offer_summary", value)}
            placeholder="Producto o servicio que quieres impulsar"
            disabled={isLoading || isSaving}
          />
        </section>

        <section className="card space-y-5" aria-labelledby="customer-heading">
          <div>
            <h2 id="customer-heading" className="section-title">A quién ayudas</h2>
            <p className="mt-1 text-sm text-slate-500">Estos datos sirven como contexto para la prospección y las conversaciones.</p>
          </div>
          <TextField
            id="target-customer"
            label="Cliente ideal"
            value={form.target_customer_summary}
            onChange={(value) => updateField("target_customer_summary", value)}
            placeholder="Tipo de negocio, tamaño, necesidad o perfil"
            disabled={isLoading || isSaving}
          />
          <div className="grid gap-5 md:grid-cols-2">
            <Field
              id="location-market"
              label="Zona o mercado"
              value={form.location_or_market}
              onChange={(value) => updateField("location_or_market", value)}
              placeholder="Ej. México, CDMX y alrededores"
              disabled={isLoading || isSaving}
            />
            <Field
              id="price-context"
              label="Rango o contexto de precio"
              value={form.price_context}
              onChange={(value) => updateField("price_context", value)}
              placeholder="Ej. Desde $2,500 MXN"
              disabled={isLoading || isSaving}
            />
          </div>
        </section>

        <section className="card space-y-5" aria-labelledby="sales-heading">
          <div>
            <h2 id="sales-heading" className="section-title">Cómo quieres vender</h2>
            <p className="mt-1 text-sm text-slate-500">Alinea al setter y al closer antes de automatizar más partes del proceso.</p>
          </div>
          <TextField
            id="primary-goal"
            label="Objetivo comercial"
            value={form.primary_goal}
            onChange={(value) => updateField("primary_goal", value)}
            placeholder="Qué resultado quieres conseguir con la prospección"
            disabled={isLoading || isSaving}
          />
          <TextField
            id="differentiators"
            label="Diferenciadores"
            value={form.differentiators}
            onChange={(value) => updateField("differentiators", value)}
            placeholder="Por qué deberían elegirte"
            disabled={isLoading || isSaving}
          />
          <TextField
            id="sales-constraints"
            label="Límites o consideraciones"
            value={form.sales_constraints}
            onChange={(value) => updateField("sales_constraints", value)}
            placeholder="Promesas, condiciones o temas que deben evitarse"
            disabled={isLoading || isSaving}
          />
        </section>

        <div className="flex justify-end">
          <button className="btn-primary min-w-40" type="submit" disabled={isLoading || isSaving || !form.business_name.trim()}>
            {isSaving ? "Guardando…" : "Guardar perfil"}
          </button>
        </div>
      </form>
    </div>
  );
}

function toForm(profile: SellerProfile): ProfileForm {
  return {
    business_name: profile.business_name || "",
    description: profile.description || "",
    offer_summary: profile.offer_summary || "",
    target_customer_summary: profile.target_customer_summary || "",
    location_or_market: profile.location_or_market || "",
    price_context: profile.price_context || "",
    primary_goal: profile.primary_goal || "",
    differentiators: profile.differentiators || "",
    sales_constraints: profile.sales_constraints || "",
  };
}

function Field({
  id,
  label,
  value,
  onChange,
  placeholder,
  required = false,
  disabled = false,
}: {
  id: string;
  label: string;
  value: string | null;
  onChange: (value: string) => void;
  placeholder?: string;
  required?: boolean;
  disabled?: boolean;
}) {
  return (
    <label htmlFor={id} className="block space-y-2">
      <span className="text-sm font-semibold text-slate-700">{label}{required && <span className="text-teal-600"> *</span>}</span>
      <input
        id={id}
        className="input w-full"
        value={value || ""}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        required={required}
        disabled={disabled}
      />
    </label>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  placeholder,
  disabled = false,
}: {
  id: string;
  label: string;
  value: string | null;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <label htmlFor={id} className="block space-y-2">
      <span className="text-sm font-semibold text-slate-700">{label}</span>
      <textarea
        id={id}
        className="input min-h-24 w-full resize-y"
        value={value || ""}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        disabled={disabled}
      />
    </label>
  );
}
