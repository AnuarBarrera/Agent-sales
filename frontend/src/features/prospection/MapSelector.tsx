import { useCallback, useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import * as turf from "@turf/turf";

interface PolygonSelection {
  type: "Polygon";
  coordinates: number[][][];
}

interface MapSelectorProps {
  onLocationSelect: (location: {
    center: [number, number];
    radiusKm: number;
    polygon?: PolygonSelection;
  }) => void;
}

const INITIAL_CENTER: [number, number] = [-99.1332, 19.4326];

export function MapSelector({ onLocationSelect }: MapSelectorProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const searchTypeRef = useRef<"circle" | "polygon">("circle");
  const isDrawingRef = useRef(false);
  const [searchType, setSearchType] = useState<"circle" | "polygon">("circle");
  const [radiusKm, setRadiusKm] = useState(5);
  const [center, setCenter] = useState<[number, number] | null>(null);
  const [polygonCoords, setPolygonCoords] = useState<number[][]>([]);
  const [isDrawing, setIsDrawing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    searchTypeRef.current = searchType;
    isDrawingRef.current = isDrawing;
  }, [searchType, isDrawing]);

  const removeSelectionLayers = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    for (const layer of ["circle-layer", "polygon-fill", "polygon-outline"]) {
      if (map.getLayer(layer)) map.removeLayer(layer);
    }
    for (const source of ["circle", "polygon"]) {
      if (map.getSource(source)) map.removeSource(source);
    }
  }, []);

  useEffect(() => {
    if (!mapContainer.current) return;
    maplibregl.setWorkerUrl(maplibreWorkerUrl);
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: import.meta.env.VITE_MAP_STYLE_URL || "https://tiles.openfreemap.org/styles/liberty",
      center: INITIAL_CENTER,
      zoom: 10,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    mapRef.current = map;

    map.on("click", (event) => {
      if (searchTypeRef.current === "circle") {
        setCenter([event.lngLat.lng, event.lngLat.lat]);
      } else if (isDrawingRef.current) {
        setPolygonCoords((previous) => [...previous, [event.lngLat.lng, event.lngLat.lat]]);
      }
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const draw = () => {
      removeSelectionLayers();
      if (searchType === "circle" && center) {
        const circle = turf.circle(turf.point(center), radiusKm, { steps: 64, units: "kilometers" } as never);
        map.addSource("circle", { type: "geojson", data: circle });
        map.addLayer({ id: "circle-layer", type: "fill", source: "circle", paint: { "fill-color": "#2563eb", "fill-opacity": 0.2 } });
      }
      if (searchType === "polygon" && polygonCoords.length >= 3) {
        const coordinates = [...polygonCoords, polygonCoords[0]];
        const polygon: GeoJSON.Feature<GeoJSON.Polygon> = {
          type: "Feature",
          properties: {},
          geometry: { type: "Polygon", coordinates: [coordinates] },
        };
        map.addSource("polygon", { type: "geojson", data: polygon });
        map.addLayer({ id: "polygon-fill", type: "fill", source: "polygon", paint: { "fill-color": "#2563eb", "fill-opacity": 0.2 } });
        map.addLayer({ id: "polygon-outline", type: "line", source: "polygon", paint: { "line-color": "#2563eb", "line-width": 2 } });
      }
    };

    if (map.isStyleLoaded()) draw();
    else map.once("load", draw);
    return () => { map.off("load", draw); };
  }, [center, polygonCoords, radiusKm, searchType, removeSelectionLayers]);

  const clearSelection = () => {
    setCenter(null);
    setPolygonCoords([]);
    setIsDrawing(false);
    setError("");
    removeSelectionLayers();
  };

  const selectArea = () => {
    setError("");
    if (searchType === "circle") {
      if (!center) {
        setError("Selecciona una ubicación en el mapa");
        return;
      }
      onLocationSelect({ center, radiusKm });
      return;
    }
    if (polygonCoords.length < 3) {
      setError("El polígono debe tener al menos 3 puntos");
      return;
    }
    const coordinates = [...polygonCoords, polygonCoords[0]];
    const polygon: PolygonSelection = { type: "Polygon", coordinates: [coordinates] };
    const centroid = turf.centroid({ type: "Feature", properties: {}, geometry: polygon });
    onLocationSelect({ center: centroid.geometry.coordinates as [number, number], radiusKm: 0, polygon });
  };

  const changeType = (type: "circle" | "polygon") => {
    setSearchType(type);
    clearSelection();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium text-gray-700">Tipo de búsqueda:</span>
        <button type="button" onClick={() => changeType("circle")} className={searchType === "circle" ? "btn-primary" : "btn-secondary"}>Círculo</button>
        <button type="button" onClick={() => changeType("polygon")} className={searchType === "polygon" ? "btn-primary" : "btn-secondary"}>Polígono</button>
      </div>
      {searchType === "circle" && (
        <label className="block text-sm text-gray-700">
          Radio: <strong>{radiusKm} km</strong>
          <input aria-label="Radio de búsqueda" type="range" min="1" max="50" value={radiusKm} onChange={(event) => setRadiusKm(Number(event.target.value))} className="mt-2 w-full" />
        </label>
      )}
      {searchType === "polygon" && (
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" checked={isDrawing} onChange={(event) => setIsDrawing(event.target.checked)} />
          {isDrawing ? "Dibujando: haz clic para agregar puntos" : "Activar dibujo del polígono"}
        </label>
      )}
      {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
      <div className="relative h-96 w-full overflow-hidden rounded-lg border border-gray-200"><div ref={mapContainer} className="h-full w-full" /></div>
      <div className="flex gap-2">
        <button type="button" onClick={clearSelection} className="btn-secondary">Limpiar</button>
        <button type="button" onClick={selectArea} className="btn-primary">Usar esta área</button>
      </div>
    </div>
  );
}
