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

export function TrustpilotPhase5Panel() {
  const [url, setUrl] = useState("");
  const [maxReviews, setMaxReviews] = useState(5000);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function run() {
    setErr("");
    const u = (url || "").trim();
    if (!u) {
      setErr("Paste a Trustpilot company URL.");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`/phase5/trustpilot/scrape_and_enrich.csv`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          urls: [u],
          max_reviews_per_company: Number(maxReviews) || 5000
        })
      });
      if (!res.ok) {
        const j = await res.json().catch(() => null);
        throw new Error((j && (j.detail || j.error)) || `Request failed: ${res.status}`);
      }
      const blob = await res.blob();
      const a = document.createElement("a");
      const href = window.URL.createObjectURL(blob);
      a.href = href;
      a.download = "phase5_trustpilot_enriched.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(href);
      // Success - clear URL
      setUrl("");
    } catch (e) {
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
      </div>
    </div>
  );
}
