/**
 * PHASE 5 — Trustpilot URL Scraper UI Component
 *
 * Provides a simple input panel for:
 * - Trustpilot company URL
 * - Max reviews to scrape
 * - One-click: Scrape → Enrich → Download CSV
 *
 * Usage:
 * import { TrustpilotPhase5Panel } from './components/TrustpilotPhase5Panel';
 *
 * <TrustpilotPhase5Panel />
 */
import React, { useState } from "react";
import config from '../config';

// PHASE 5 HOTFIX: Force direct Railway backend connection (bypass Netlify proxy)
const API_BASE =
  (window && window.__API_BASE_URL__) ||
  (typeof import.meta !== "undefined" &&
    import.meta.env &&
    import.meta.env.VITE_API_BASE_URL) ||
  (typeof process !== "undefined" &&
    process.env &&
    (process.env.REACT_APP_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL)) ||
  config.API_BASE_URL || "";

function apiUrl(path) {
  if (!API_BASE) return path;
  return `${API_BASE.replace(/\/+$/, "")}/${String(path).replace(/^\/+/, "")}`;
}

export function TrustpilotPhase5Panel() {
  const [url, setUrl] = useState("");
  const [maxReviews, setMaxReviews] = useState(5000);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [statusMsg, setStatusMsg] = useState("");

  async function run() {
    setErr("");
    setStatusMsg("");
    const u = (url || "").trim();
    if (!u) {
      setErr("Paste a Trustpilot company URL.");
      return;
    }

    setBusy(true);

    try {
      const endpoint = apiUrl("/phase5/trustpilot/scrape_and_enrich.csv");
      console.log("[PHASE5] Starting request to:", endpoint);
      console.log("[PHASE5] Request payload:", { urls: [u], max_reviews_per_company: Number(maxReviews) || 5000 });

      setStatusMsg("Connecting to backend...");

      // PHASE 5 HOTFIX: Add timeout warning (but don't abort - let it complete)
      const timeoutWarning = setTimeout(() => {
        console.warn("[PHASE5] Request taking longer than 30s - this is normal for large scrapes");
        setStatusMsg("Scraping in progress (this may take 2-5 minutes)...");
      }, 30000);

      const res = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          urls: [u],
          max_reviews_per_company: Number(maxReviews) || 5000
        })
      });

      clearTimeout(timeoutWarning);
      console.log("[PHASE5] Response received:", res.status, res.statusText);

      if (!res.ok) {
        const j = await res.json().catch(() => null);
        const errorMsg = (j && (j.detail || j.error)) || `Request failed: ${res.status}`;
        console.error("[PHASE5] Error response:", errorMsg);
        throw new Error(errorMsg);
      }

      setStatusMsg("Downloading CSV...");
      const blob = await res.blob();
      console.log("[PHASE5] CSV blob received:", blob.size, "bytes");

      const a = document.createElement("a");
      const href = window.URL.createObjectURL(blob);
      a.href = href;
      a.download = "phase5_trustpilot_enriched.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(href);

      console.log("[PHASE5] CSV download complete");
      setStatusMsg("✅ Download complete!");

      // Success - clear URL after delay
      setTimeout(() => {
        setUrl("");
        setStatusMsg("");
      }, 2000);

    } catch (e) {
      console.error("[PHASE5] Error:", e);
      setErr(e?.message || "Failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        border: "1px solid rgba(0,0,0,0.1)",
        borderRadius: 12,
        padding: 16,
        marginBottom: 16,
        backgroundColor: "rgba(255,255,255,0.5)"
      }}
    >
      <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 16 }}>
        📊 Phase 5 — Trustpilot URL → Scrape → Enrich → CSV
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.trustpilot.com/review/example.com"
          style={{
            flex: "1 1 420px",
            padding: 10,
            borderRadius: 10,
            border: "1px solid rgba(0,0,0,0.15)",
            fontSize: 14
          }}
          disabled={busy}
        />
        <input
          value={maxReviews}
          onChange={(e) => setMaxReviews(e.target.value)}
          type="number"
          min={1}
          max={5000}
          style={{
            width: 140,
            padding: 10,
            borderRadius: 10,
            border: "1px solid rgba(0,0,0,0.15)",
            fontSize: 14
          }}
          disabled={busy}
          title="Max reviews (cap 5000)"
        />
        <button
          onClick={run}
          disabled={busy}
          style={{
            padding: "10px 20px",
            borderRadius: 10,
            border: "none",
            cursor: busy ? "not-allowed" : "pointer",
            backgroundColor: busy ? "#ccc" : "#007bff",
            color: "white",
            fontWeight: 600,
            fontSize: 14
          }}
        >
          {busy ? "Running..." : "Run"}
        </button>
      </div>

      {statusMsg ? (
        <div
          style={{
            marginTop: 10,
            padding: 10,
            borderRadius: 8,
            backgroundColor: "rgba(59, 130, 246, 0.1)",
            color: "#1e40af",
            fontSize: 13
          }}
        >
          ℹ️ {statusMsg}
        </div>
      ) : null}

      {err ? (
        <div
          style={{
            marginTop: 10,
            padding: 10,
            borderRadius: 8,
            backgroundColor: "rgba(220, 53, 69, 0.1)",
            color: "crimson",
            whiteSpace: "pre-wrap",
            fontSize: 13
          }}
        >
          ❌ {err}
        </div>
      ) : null}

      <div style={{ marginTop: 8, opacity: 0.7, fontSize: 13 }}>
        ✨ <strong>New:</strong> Scrapes Trustpilot reviews via Apify Dino, enriches with Phase 4, downloads one CSV. Phase 4 logic remains locked.
        {busy && <div style={{ marginTop: 4, fontSize: 12, fontStyle: "italic" }}>⏱️ Large scrapes take 2-5 minutes. Keep this tab open.</div>}
      </div>
    </div>
  );
}
