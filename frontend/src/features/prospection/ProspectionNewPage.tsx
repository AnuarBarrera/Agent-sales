import { useState, useCallback } from "react";
import { MapSelector } from "./MapSelector";
import { apiFetch } from "../../lib/api";

interface LocationData {
  center: [number, number];
  radiusKm: number;
  polygon?: GeoJSON.Polygon;
}

export function ProspectionNewPage() {
  const [giro, setGiro] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [locationData, setLocationData] = useState<LocationData | null>(null);
  const [locationLabel, setLocationLabel] = useState("Área seleccionada en el mapa");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setIsLoading(true);
    try {
      if (!locationData) throw new Error("Selecciona un área en el mapa");
      const data = await apiFetch<{ id: string }>("/prospection-runs", {
        method: "POST",
        body: JSON.stringify({
          giro: giro.trim(),
          location_label: locationLabel.trim() || "Área seleccionada en el mapa",
          center_latitude: locationData.center[1],
          center_longitude: locationData.center[0],
          radius_km: locationData.polygon ? null : locationData.radiusKm,
          polygon_geometry: locationData.polygon || null,
        }),
      });
      setRunId(data.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      setIsLoading(false);
    }
  }

  const handleLocationSelect = useCallback((location: { center: [number, number]; radiusKm: number; polygon?: GeoJSON.Polygon }) => {
    setLocationData(location);
  }, []);

  if (runId) {
    return (
      <div className="text-center py-12">
        <h2 className="text-2xl font-bold text-green-700 mb-4">¡Búsqueda creada!</h2>
        <p className="text-gray-600 mb-6">Run ID: <code>{runId}</code></p>
        <a
          href={`/prospection/${runId}`}
          className="inline-block px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
        >
          Ver progreso
        </a>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h2 className="text-2xl font-bold text-gray-900 mb-6">Nueva Búsqueda de Prospectos</h2>
      <form onSubmit={handleSubmit} className="space-y-6 bg-white p-6 rounded-lg shadow-sm border border-gray-200">
        {error && (
          <div className="text-red-600 text-sm" role="alert">{error}</div>
        )}
        <div>
          <label htmlFor="giro" className="block text-sm font-medium text-gray-700 mb-1">
            Giro / Tipo de negocio
          </label>
          <input
            id="giro"
            type="text"
            required
            value={giro}
            onChange={(e) => setGiro(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="Ej: restaurantes, dentistas, talleres mecánicos"
            disabled={isLoading}
          />
        </div>
        <div>
          <label htmlFor="location-label" className="block text-sm font-medium text-gray-700 mb-1">
            Nombre de la zona
          </label>
          <input
            id="location-label"
            value={locationLabel}
            onChange={(e) => setLocationLabel(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-md"
            placeholder="Ej: Roma Norte, CDMX"
            disabled={isLoading}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Área de búsqueda
          </label>
          <MapSelector onLocationSelect={handleLocationSelect} />
        </div>
        <div>
          <button
            type="submit"
            disabled={isLoading}
            className="w-full py-2 px-4 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          >
            {isLoading ? "Creando búsqueda..." : "Iniciar búsqueda"}
          </button>
        </div>
      </form>
    </div>
  );
}
