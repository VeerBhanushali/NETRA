"use client";

import { useEffect, useRef } from "react";
import type { Camera, TrajectoryPoint } from "@/lib/api";
import "leaflet/dist/leaflet.css";

/** Leaflet driven directly rather than through react-leaflet: one less
 *  dependency with React 19 peer constraints, and full control over
 *  marker markup so the map obeys the design system.
 *
 *  OpenStreetMap tiles need no access token, so the map works without a
 *  Mapbox account and keeps functioning if a key is ever revoked.
 */

export interface MapViewProps {
  cameras?: Camera[];
  /** Ordered trajectory points; drawn as a numbered polyline. */
  points?: TrajectoryPoint[];
  /** Incident pins, drawn above everything else. */
  incidents?: { lat: number; lon: number; severity: string; title: string }[];
  height?: string;
  onCameraClick?: (id: string) => void;
}

const SEVERITY_HEX: Record<string, string> = {
  critical: "#b91c1c", high: "#c2410c", medium: "#a16207",
  low: "#1d4ed8", info: "#52525b",
};

export default function MapView({
  cameras = [], points = [], incidents = [], height = "100%", onCameraClick,
}: MapViewProps) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<any>(null);
  const layer = useRef<any>(null);

  // Create the map once. Recreating it on every prop change causes the
  // grey-tile flicker that makes a live demo look broken.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !el.current || map.current) return;

      map.current = L.map(el.current, {
        center: [30.7333, 76.7794],   // Chandigarh
        zoom: 12,
        zoomControl: true,
        attributionControl: true,
        preferCanvas: true,           // far smoother with many markers
      });

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors",
      }).addTo(map.current);

      layer.current = L.layerGroup().addTo(map.current);
    })();
    return () => {
      cancelled = true;
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // Redraw overlays whenever the data changes.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const L = (await import("leaflet")).default;
      if (cancelled || !map.current || !layer.current) return;
      layer.current.clearLayers();

      // --- cameras: small hollow squares, deliberately quiet ---
      for (const c of cameras) {
        const live = c.is_active === 1;
        L.circleMarker([c.lat, c.lon], {
          radius: 4,
          color: live ? "#52525b" : "#a1a1aa",
          weight: 1.5,
          fillColor: "#ffffff",
          fillOpacity: 1,
        })
          .bindTooltip(
            `<span style="font-weight:600">${c.name}</span><br/>` +
            `<span style="font-family:ui-monospace,monospace;font-size:11px">${c.id}</span>` +
            `${c.zone_name ? `<br/><span style="font-size:11px">${c.zone_name}</span>` : ""}`,
            { direction: "top", offset: [0, -6] }
          )
          .on("click", () => onCameraClick?.(c.id))
          .addTo(layer.current);
      }

      // --- trajectory polyline with numbered stops ---
      if (points.length > 1) {
        const latlngs = points.map((p) => [p.lat, p.lon] as [number, number]);
        L.polyline(latlngs, {
          color: "#1d4ed8", weight: 2, opacity: 0.85,
        }).addTo(layer.current);

        // Direction arrows at each segment midpoint, so the path reads
        // chronologically instead of as an ambiguous loop.
        for (let i = 0; i < latlngs.length - 1; i++) {
          const [y1, x1] = latlngs[i];
          const [y2, x2] = latlngs[i + 1];
          const angle = (Math.atan2(y2 - y1, x2 - x1) * 180) / Math.PI;
          L.marker([(y1 + y2) / 2, (x1 + x2) / 2], {
            icon: L.divIcon({
              className: "",
              html: `<div style="transform:rotate(${-angle}deg);color:#1d4ed8;
                     font-size:13px;line-height:1">&#10148;</div>`,
              iconSize: [12, 12], iconAnchor: [6, 6],
            }),
            interactive: false,
          }).addTo(layer.current);
        }
      }

      points.forEach((p, i) => {
        const first = i === 0;
        const last = i === points.length - 1;
        L.marker([p.lat, p.lon], {
          icon: L.divIcon({
            className: "",
            html:
              `<div style="width:20px;height:20px;border-radius:2px;
                 display:flex;align-items:center;justify-content:center;
                 font-family:ui-monospace,monospace;font-size:10px;font-weight:700;
                 border:1px solid ${last ? "#1d4ed8" : "#3f3f46"};
                 background:${last ? "#1d4ed8" : first ? "#3f3f46" : "#ffffff"};
                 color:${last || first ? "#ffffff" : "#18181b"}">${i + 1}</div>`,
            iconSize: [20, 20], iconAnchor: [10, 10],
          }),
        })
          .bindTooltip(
            `<b>${i + 1}. ${p.camera_name}</b><br/>` +
            `<span style="font-family:ui-monospace,monospace;font-size:11px">` +
            `${new Date(p.seen_at).toLocaleString("en-GB", { hour12: false })}</span>`,
            { direction: "top", offset: [0, -10] }
          )
          .addTo(layer.current);
      });

      // --- incidents on top ---
      for (const inc of incidents) {
        const hex = SEVERITY_HEX[inc.severity] ?? "#52525b";
        L.circleMarker([inc.lat, inc.lon], {
          radius: 9, color: hex, weight: 2,
          fillColor: hex, fillOpacity: 0.18,
        })
          .bindTooltip(inc.title, { direction: "top", offset: [0, -8] })
          .addTo(layer.current);
      }

      // Fit to the data rather than animating from a default viewport.
      const all = [
        ...points.map((p) => [p.lat, p.lon] as [number, number]),
        ...incidents.map((i) => [i.lat, i.lon] as [number, number]),
      ];
      if (all.length > 1) {
        map.current.fitBounds(L.latLngBounds(all), { padding: [48, 48], maxZoom: 15 });
      } else if (all.length === 1) {
        map.current.setView(all[0], 15);
      } else if (cameras.length > 1) {
        map.current.fitBounds(
          L.latLngBounds(cameras.map((c) => [c.lat, c.lon] as [number, number])),
          { padding: [48, 48] }
        );
      }
    })();
    return () => { cancelled = true; };
  }, [cameras, points, incidents, onCameraClick]);

  return <div ref={el} style={{ height, width: "100%" }} />;
}
