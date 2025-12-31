/**
 * PHASE 5 — Trustpilot URL Scraper UI Component (ONE-SHOT)
 *
 * Calls single endpoint: POST /phase5/trustpilot/scrape_and_enrich.csv
 * Returns CSV directly (no polling needed).
 */
import React, { useState } from "react";
import config from '../config';

// API base URL (same as CSV upload flow)
const API_BASE =
  (typeof window !== "undefined" && window.__API_BASE__) ||
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_BASE_URL) ||
  config.API_BASE_URL || "";

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
    setStatusMsg("Starting Apify scrape... (this may take 2-5 minutes)");

    try {
      const endpoint = `${API_BASE.replace(/\/+$/, "")}/phase5/trustpilot/scrape_and_enrich.csv`;
      console.log("[PHASE5] Calling one-shot endpoint:", endpoint);

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: u, max_reviews: Number(maxReviews) || 5000 }),
      });

      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Phase5 failed (${res.status}): ${text || res.statusText}`);
      }

      setStatusMsg("Downloading CSV...");
      const blob = await res.blob();

      // Download the CSV
      const fname = `phase5_trustpilot_enriched_${Date.now()}.csv`;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);

      setStatusMsg("✅ Download complete!");
      console.log("[PHASE5] CSV downloaded:", fname);

      // Clear form after success
      setTimeout(() => {
        setUrl("");
        setStatusMsg("");
      }, 3000);

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
        Phase 5 — Trustpilot URL → Scrape → Enrich → CSV
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

      {statusMsg && (
        <div
          style={{
            marginTop: 10,
            padding: 10,
            borderRadius: 8,
            backgroundColor: statusMsg.includes("✅") ? "rgba(34, 197, 94, 0.1)" : "rgba(59, 130, 246, 0.1)",
            color: statusMsg.includes("✅") ? "#166534" : "#1e40af",
            fontSize: 13
          }}
        >
          {statusMsg}
        </div>
      )}

      {err && (
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
          {err}
        </div>
      )}

      <div style={{ marginTop: 8, opacity: 0.7, fontSize: 13 }}>
        <strong>One-shot flow:</strong> Scrapes Trustpilot → Enriches with Phase 4 → Downloads businesses-only CSV.
        {busy && <div style={{ marginTop: 4, fontSize: 12, fontStyle: "italic" }}>⏱️ Large scrapes take 2-5 minutes. Keep this tab open.</div>}
      </div>
    </div>
  );
}
