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
      // PHASE 5 FIX: Async job flow (prevents multiple Apify re-runs)
      // Step 1: Start job
      const startEndpoint = apiUrl("/phase5/trustpilot/start");
      console.log("[PHASE5] Starting job:", startEndpoint);
      setStatusMsg("Starting job...");

      const startRes = await fetch(startEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          urls: [u],
          max_reviews_per_company: Number(maxReviews) || 5000
        })
      });

      if (!startRes.ok) {
        throw new Error(`Start failed: ${startRes.status}`);
      }

      const { job_id } = await startRes.json();
      console.log("[PHASE5] Job started:", job_id);

      // Step 2: Poll job status
      let lastProgress = "";
      while (true) {
        await new Promise(r => setTimeout(r, 1500)); // Poll every 1.5s

        const statusEndpoint = apiUrl(`/phase5/trustpilot/status/${job_id}`);
        const stRes = await fetch(statusEndpoint);

        if (!stRes.ok) {
          throw new Error(`Status check failed: ${stRes.status}`);
        }

        const st = await stRes.json();
        console.log("[PHASE5] Job status:", st);

        // Update status message based on progress
        if (st.progress !== lastProgress) {
          lastProgress = st.progress;
          if (st.progress === "scraping") {
            setStatusMsg("Scraping Trustpilot reviews...");
          } else if (st.progress === "enriching") {
            setStatusMsg(`Enriching ${st.row_count_scraped} reviews with business data...`);
          } else if (st.progress === "csv") {
            setStatusMsg("Preparing CSV download...");
          }
        }

        // Check for completion or error
        if (st.status === "error") {
          throw new Error(st.error || "Job failed");
        }

        if (st.status === "done") {
          console.log("[PHASE5] Job complete!");
          break;
        }
      }

      // Step 3: Download CSV (direct download - more reliable than blob)
      setStatusMsg("Downloading CSV...");
      console.log("[PHASE5] Initiating download for job:", job_id);

      // PHASE 5 FIX: Use direct URL navigation instead of blob (more reliable)
      const downloadUrl = apiUrl(`/phase5/trustpilot/download/${job_id}`);
      window.location.href = downloadUrl;

      setStatusMsg("✅ Download started!");

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
