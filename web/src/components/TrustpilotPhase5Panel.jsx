/**
 * PHASE 5 — Trustpilot URL Scraper UI Component (ONE-SHOT)
 *
 * Calls single endpoint: POST /phase5/trustpilot/scrape_and_enrich.csv
 * Returns CSV directly (no polling needed).
 * Includes Reset button to clear stuck jobs.
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
  const [resetting, setResetting] = useState(false);
  const [err, setErr] = useState("");
  const [statusMsg, setStatusMsg] = useState("");

  // Reset stuck job
  async function resetJob() {
    const u = (url || "").trim();
    if (!u) {
      setErr("Paste a Trustpilot URL first.");
      return;
    }

    setResetting(true);
    setErr("");
    setStatusMsg("Resetting stuck job...");

    try {
      const endpoint = `${API_BASE.replace(/\/+$/, "")}/phase5/trustpilot/reset`;
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: u }),
      });

      const data = await res.json().catch(() => ({}));

      if (data.prev_status === "RUNNING") {
        setStatusMsg("✅ Stuck job cleared! You can now click Run.");
      } else {
        setStatusMsg(`Job status: ${data.status || "unknown"}. Try clicking Run.`);
      }
    } catch (e) {
      setErr("Reset failed: " + (e?.message || "Unknown error"));
    } finally {
      setResetting(false);
    }
  }

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

      // Handle 409 (job already running) - poll status until ready
      if (res.status === 409) {
        const j = await res.json().catch(() => ({}));
        const jobId = j.job_id;

        if (!jobId) {
          setErr(`Job already running but no job_id returned. Click "Reset" to clear it.`);
          setBusy(false);
          return;
        }

        console.log("[PHASE5] Got 409, polling job:", jobId);
        setStatusMsg(`Job already running (${jobId}). Waiting for completion...`);

        // Poll status endpoint until CSV ready
        for (let i = 0; i < 300; i++) {
          await new Promise(r => setTimeout(r, 2000));

          try {
            const statusEndpoint = `${API_BASE.replace(/\/+$/, "")}/phase5/trustpilot/status/${jobId}`;
            const st = await fetch(statusEndpoint);
            const sj = await st.json();

            console.log("[PHASE5] Poll status:", sj);

            if (sj.status === "ERROR") {
              throw new Error(`Phase5 failed: ${sj.error || "unknown error"}`);
            }

            if (sj.status === "DONE" && sj.csv_ready) {
              // Download from finish endpoint
              setStatusMsg("Job complete! Downloading CSV...");
              const downloadEndpoint = `${API_BASE.replace(/\/+$/, "")}/phase5/trustpilot/finish_and_enrich.csv`;
              const csvRes = await fetch(downloadEndpoint, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ job_id: jobId }),
              });

              if (!csvRes.ok) {
                throw new Error(`Download failed: ${csvRes.status}`);
              }

              const blob = await csvRes.blob();
              const fname = `phase5_trustpilot_enriched_${Date.now()}.csv`;
              const a = document.createElement("a");
              a.href = URL.createObjectURL(blob);
              a.download = fname;
              document.body.appendChild(a);
              a.click();
              a.remove();
              setTimeout(() => URL.revokeObjectURL(a.href), 5000);

              setStatusMsg("✅ Download complete!");
              setBusy(false);
              return;
            }

            setStatusMsg(`Job ${sj.status}... (poll ${i + 1}/300)`);
          } catch (pollErr) {
            console.error("[PHASE5] Poll error:", pollErr);
            // Continue polling on transient errors
          }
        }

        throw new Error("Timed out waiting for Phase5 CSV (10 minutes)");
      }

      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Phase5 failed (${res.status}): ${text || res.statusText}`);
      }

      // Immediate CSV response (200)
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
          disabled={busy || resetting}
        />
        <input
          value={maxReviews}
          onChange={(e) => setMaxReviews(e.target.value)}
          type="number"
          min={1}
          max={5000}
          style={{
            width: 100,
            padding: 10,
            borderRadius: 10,
            border: "1px solid rgba(0,0,0,0.15)",
            fontSize: 14
          }}
          disabled={busy || resetting}
          title="Max reviews (cap 5000)"
        />
        <button
          onClick={run}
          disabled={busy || resetting}
          style={{
            padding: "10px 20px",
            borderRadius: 10,
            border: "none",
            cursor: (busy || resetting) ? "not-allowed" : "pointer",
            backgroundColor: (busy || resetting) ? "#ccc" : "#007bff",
            color: "white",
            fontWeight: 600,
            fontSize: 14
          }}
        >
          {busy ? "Running..." : "Run"}
        </button>
        <button
          onClick={resetJob}
          disabled={busy || resetting}
          style={{
            padding: "10px 16px",
            borderRadius: 10,
            border: "1px solid #dc3545",
            cursor: (busy || resetting) ? "not-allowed" : "pointer",
            backgroundColor: resetting ? "#ccc" : "white",
            color: "#dc3545",
            fontWeight: 600,
            fontSize: 14
          }}
          title="Clear stuck job for this URL"
        >
          {resetting ? "Resetting..." : "Reset"}
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
        <div style={{ marginTop: 4, fontSize: 11 }}>
          <strong>Reset:</strong> Clears stuck jobs if you see "already RUNNING" error.
        </div>
      </div>
    </div>
  );
}
