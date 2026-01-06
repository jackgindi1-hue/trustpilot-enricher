import { useState, useEffect } from 'react'
import config from './config'
import './App.css'
import { TrustpilotPhase5Panel } from './components/TrustpilotPhase5Panel'
import Phase6TrainerPanel from './components/Phase6TrainerPanel'

// PHASE 4.7.1 DEPLOY - UI Stuck "Running" Fix (Missing Job Reset)
// BUILD TIMESTAMP: 2025-12-31 - Papaya.ui Matrix Theme

// PHASE 4.5: Pagination constants
const PAGE_SIZE = 100
const CHECKPOINT_EVERY = 250

// PHASE 4.5.5: Single source of truth for job ID storage
const JOB_ID_KEY = "tp_active_job_id"

/* ============================================================
   PHASE 4.5 FRONTEND UI PATCH (REACT)
   GOALS:
   1) Pagination UI for preview rows
   2) Partial download button during run
   3) Refresh mid-run resumes polling (job_id persisted)
   4) Never reset to "Ready" if a job is still running
   ============================================================ */

/* ---- helpers (keep near top of component file) ---- */
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

function looksLikeJsonText(s) {
  const t = String(s || "").trim();
  return t.startsWith("{") || t.startsWith("[");
}

async function downloadArrayBufferAsCsv(buf, filename) {
  const blob = new Blob([buf], { type: "text/csv" });
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

async function safeDownloadCsv(url, filename, opts = {}) {
  // STABILIZER FIX: Retry download on 409 until ready (race condition hardening)
  const timeoutMs = opts.timeoutMs ?? 10 * 60 * 1000; // 10 minutes
  const intervalMs = opts.intervalMs ?? 1200;         // 1.2s
  const start = Date.now();

  while (true) {
    const res = await fetch(url, { cache: "no-store" });
    const ct = (res.headers.get("content-type") || "").toLowerCase();

    if (res.status === 409) {
      // NOT READY YET -> wait and retry (do NOT throw immediately)
      const txt = await res.text().catch(() => "");
      console.log(`[CSV] 409 not ready, retrying... elapsed=${Date.now() - start}ms`);
      if (Date.now() - start > timeoutMs) {
        throw new Error(`Download still not ready after timeout: ${txt.slice(0, 200)}`);
      }
      await new Promise(r => setTimeout(r, intervalMs));
      continue;
    }

    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`Download failed: ${res.status} ${txt.slice(0, 200)}`);
    }

    // refuse JSON downloads
    if (ct.includes("application/json")) {
      const txt = await res.text().catch(() => "");
      throw new Error(`Refusing JSON as CSV: ${txt.slice(0, 200)}`);
    }

    const buf = await res.arrayBuffer();
    const head = new TextDecoder("utf-8").decode(buf.slice(0, 64)).trim();
    if (looksLikeJsonText(head)) throw new Error(`Refusing JSON-looking content: ${head}`);
    await downloadArrayBufferAsCsv(buf, filename);
    return; // success
  }
}

function App() {
  const [file, setFile] = useState(null)
  const [lenderNameOverride, setLenderNameOverride] = useState('')
  const [status, setStatus] = useState('idle')
  const [isProcessing, setIsProcessing] = useState(false)
  const [error, setError] = useState(null)
  const [rowCount, setRowCount] = useState(null)
  const [currentJobId, setCurrentJobId] = useState(null)
  const [showPartialDownload, setShowPartialDownload] = useState(false)
  const [page, setPage] = useState(1)
  const [progress, setProgress] = useState(0)
  const [rowsPreview, setRowsPreview] = useState([])
  const [jobMeta, setJobMeta] = useState(null) // PHASE 4.5.4: Track job metadata
  const [runHistory, setRunHistory] = useState([]) // Run history for UI

  // PHASE 4.5: Helper functions
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n))

  // PHASE 4.5.5: Absolute stop helpers
  const clearJobId = () => {
    try {
      localStorage.removeItem(JOB_ID_KEY)
    } catch (e) {
      console.warn("Failed to clear job ID from localStorage:", e)
    }
  }

  const isTerminal = (status) => {
    return ["completed", "done", "failed", "error", "cancelled", "missing", "unknown"].includes(
      String(status || "").toLowerCase()
    )
  }

  const safeFetchJson = async (url, tries = 5) => {
    let lastErr = null
    for (let i = 0; i < tries; i++) {
      try {
        const r = await fetch(url, { cache: 'no-store' })

        // 🔥 PHASE 4.5.4: If 404, stop retrying (job not found)
        if (r.status === 404) {
          const err = new Error("job_not_found")
          err.code = 404
          throw err
        }

        const t = await r.text()
        const js = t ? JSON.parse(t) : {}
        return { ok: r.ok, status: r.status, json: js, raw: t }
      } catch (e) {
        // If 404, don't retry
        if (e.code === 404) throw e

        lastErr = e
        await new Promise(r => setTimeout(r, 500 * (i + 1))) // backoff
      }
    }
    throw lastErr || new Error("network_error")
  }

  // Pagination logic
  const totalRows = Array.isArray(rowsPreview) ? rowsPreview.length : 0;
  const totalPages = Math.max(1, Math.ceil(totalRows / PAGE_SIZE));
  const pageSafe = Math.min(Math.max(1, page), totalPages);
  const startIdx = (pageSafe - 1) * PAGE_SIZE;
  const endIdx = Math.min(startIdx + PAGE_SIZE, totalRows);
  const pageRows = (Array.isArray(rowsPreview) ? rowsPreview : []).slice(startIdx, endIdx);

  // Reset page when totalRows changes
  useEffect(() => {
    setPage(1);
  }, [totalRows]);

  /* ---- PHASE 4.5.5: Hardened polling with absolute stop ---- */
  const pollUntilDone = async (jobId, options = {}) => {
    return new Promise((resolve, reject) => {
      let cancelled = false;
      let timeoutId = null;

      const cleanup = () => {
        cancelled = true;
        if (timeoutId) clearTimeout(timeoutId);
      };

      const tick = async () => {
        if (cancelled) return;

        try {
          const res = await fetch(apiUrl(`/jobs/${jobId}`), { cache: 'no-store' });

          // ✅ ABSOLUTE STOP on 404
          if (res.status === 404) {
            console.warn("Job not found; stopping polling permanently", jobId);
            clearJobId();
            setCurrentJobId(null);
            setJobMeta(null);
            cleanup();
            reject(new Error("Job not found (404). Please start a new job."));
            return;
          }

          if (!res.ok) {
            throw new Error(`Job fetch failed: ${res.status}`);
          }

          const meta = await res.json();
          const status = String(meta.status || "").toLowerCase();
          const progress = typeof meta.progress === "number" ? meta.progress : null;

          // PHASE 4.7.1: Handle missing/unknown jobs - reset UI to idle
          if (status === "missing" || status === "unknown" || meta.missing === true) {
            console.warn("Job is missing/unknown; resetting UI to idle", jobId);
            clearJobId();
            setCurrentJobId(null);
            setJobMeta(null);
            setStatus("idle");
            setIsProcessing(false);
            setError(null);
            cleanup();
            resolve(); // Don't show error, just reset
            return;
          }

          // Update UI state
          setJobMeta(meta);
          if (progress !== null) setProgress(progress);

          // Update status display
          if (progress !== null && progress > 0) {
            setStatus(`running (${Math.round(progress * 100)}%)`);
          }

          // ✅ ABSOLUTE STOP on terminal status
          if (isTerminal(status)) {
            console.info("Job terminal; stopping polling", status);
            clearJobId();
            setCurrentJobId(null);
            cleanup();

            if (status === "done") {
              resolve(); // Success
            } else {
              reject(new Error(meta.error || `Job ${status}`));
            }
            return;
          }

          // Continue polling with chained timeout (prevents overlap)
          if (!cancelled) {
            timeoutId = setTimeout(tick, 1000);
          }

        } catch (e) {
          if (!cancelled) {
            cleanup();
            reject(e);
          }
        }
      };

      tick(); // Start polling
    });
  };

  /* ---- RUN button handler ---- */
  const handleRunEnrichment = async (e) => {
    if (e) e.preventDefault();

    if (!file) {
      setError("No file selected");
      return;
    }

    setError(null);
    setProgress(0);
    setStatus("queued");
    setIsProcessing(true);
    setShowPartialDownload(false);

    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("concurrency", "8");

      if (lenderNameOverride.trim()) {
        fd.append('lender_name_override', lenderNameOverride.trim());
      }

      // 1) Create job (JSON only)
      const res = await fetch(apiUrl("/jobs"), { method: "POST", body: fd });
      const raw = await res.text().catch(() => "");
      const ct = (res.headers.get("content-type") || "").toLowerCase();

      if (!res.ok) throw new Error(`Job create failed: ${res.status} ${raw.slice(0, 200)}`);

      if (!ct.includes("application/json") && !looksLikeJsonText(raw)) {
        throw new Error(
          `Expected JSON from POST /jobs; got content-type=${ct} body=${raw.slice(0, 200)}`
        );
      }

      const js = JSON.parse(raw);
      const id = js.job_id;
      if (!id) throw new Error("Missing job_id from /jobs");

      setCurrentJobId(id);
      localStorage.setItem(JOB_ID_KEY, id); // PHASE 4.5.5: Single source of truth

      setStatus("running");

      // 2) Poll until done (network-glitch tolerant)
      await pollUntilDone(id);

      // 3) Download final CSV (only after done)
      setStatus('downloading');
      await safeDownloadCsv(apiUrl(`/jobs/${id}/download`), `enriched-${id}.csv`);

      setStatus("done");
      // Add to run history
      setRunHistory(prev => [{
        name: file?.name || "CSV Upload",
        time: new Date().toLocaleTimeString(),
        status: "success"
      }, ...prev.slice(0, 9)]);
      clearJobId(); // PHASE 4.5.5: Use clearJobId helper
      setIsProcessing(false);

      // Reset form after success
      setTimeout(() => {
        setFile(null);
        setLenderNameOverride('');
        setRowCount(null);
        setStatus('idle');
        setCurrentJobId(null);
        // Reset file input
        const fileInput = document.getElementById('csvFile');
        if (fileInput) fileInput.value = '';
      }, 3000);

    } catch (err) {
      console.error('❌ Enrichment error:', err);
      setError(err.message || 'Enrichment failed. Please try again.');
      setStatus('error');
      // Add to run history
      setRunHistory(prev => [{
        name: file?.name || "CSV Upload",
        time: new Date().toLocaleTimeString(),
        status: "error"
      }, ...prev.slice(0, 9)]);
      setIsProcessing(false);

      // Show partial download button if we have a jobId
      if (currentJobId) {
        setShowPartialDownload(true);
      }
    }
  };

  // ---- Partial download handler (works while job runs) ----
  const handleDownloadPartial = async () => {
    if (!currentJobId) {
      setError("No active job_id");
      return;
    }
    try {
      await safeDownloadCsv(apiUrl(`/jobs/${currentJobId}/download?partial=1`), `partial-${currentJobId}.csv`);
    } catch (err) {
      setError(err.message || 'Partial download failed');
    }
  };

  // ---- PHASE 4.5.5: localStorage resume effect (hardened) ----
  useEffect(() => {
    const saved = localStorage.getItem(JOB_ID_KEY); // Single source of truth
    if (!saved) return;

    setCurrentJobId(saved);
    setStatus("running");
    setIsProcessing(true);
    setError(null);
    (async () => {
      try {
        await pollUntilDone(saved, { resume: true });
        setStatus('downloading');
        await safeDownloadCsv(apiUrl(`/jobs/${saved}/download`), `enriched-${saved}.csv`);
        setStatus("done");
      // Add to run history
      setRunHistory(prev => [{
        name: file?.name || "CSV Upload",
        time: new Date().toLocaleTimeString(),
        status: "success"
      }, ...prev.slice(0, 9)]);
        localStorage.removeItem("tp_active_job_id");
        setIsProcessing(false);

        // Reset after success
        setTimeout(() => {
          setFile(null);
          setLenderNameOverride('');
          setRowCount(null);
          setStatus('idle');
          setCurrentJobId(null);
          const fileInput = document.getElementById('csvFile');
          if (fileInput) fileInput.value = '';
        }, 3000);
      } catch (e) {
        setStatus("error");
        setError(String(e?.message || e));
        setIsProcessing(false);
        setShowPartialDownload(true);
      }
    })();
  }, []);

  const handleFileChange = (e) => {
    const selectedFile = e.target.files[0]

    if (selectedFile && !selectedFile.name.endsWith('.csv')) {
      setError('Please select a CSV file')
      setFile(null)
      setRowCount(null)
      return
    }

    setFile(selectedFile)
    setError(null)
    setStatus('idle')

    // Count rows in the CSV
    if (selectedFile) {
      const reader = new FileReader()
      reader.onload = (event) => {
        const text = event.target.result
        const lines = text.split('\n').filter(line => line.trim().length > 0)
        // Subtract 1 for header row
        const dataRows = Math.max(0, lines.length - 1)
        setRowCount(dataRows)
      }
      reader.readAsText(selectedFile)
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Papaya.ui</h1>

      </header>

      <main className="app-main">
        {/* PHASE 5: Trustpilot URL Scraper */}
        <TrustpilotPhase5Panel />

        {/* PHASE 6: Classification Override Trainer */}
        <Phase6TrainerPanel API_BASE={API_BASE} />

        <div className="card">
          <form onSubmit={handleRunEnrichment}>
            <div className="form-group">
              <label htmlFor="csvFile">
                option 2, upload csv (column A must have "consumer.displayName")
              </label>
              <input
                type="file"
                id="csvFile"
                accept=".csv"
                onChange={handleFileChange}
                disabled={isProcessing}
                className="file-input"
              />
              {file && (
                <p className="file-name">
                  Selected: {file.name}
                  {rowCount !== null && ` (${rowCount} rows estimated)`}
                </p>
              )}
            </div>

            <div className="form-group">
              <label htmlFor="lenderName">
                Lender Name Override (Optional)
              </label>
              <input
                type="text"
                id="lenderName"
                value={lenderNameOverride}
                onChange={(e) => setLenderNameOverride(e.target.value)}
                placeholder="e.g., MyLender"
                disabled={isProcessing}
                className="text-input"
              />
              <p className="help-text">
                If provided, overrides source_lender_name for all rows
              </p>
            </div>

            <button
              type="submit"
              disabled={isProcessing || !file}
              className="submit-button"
            >
              {isProcessing ? 'Processing...' : 'Run Enrichment'}
            </button>
          </form>

          <div className="status-area">
            <div className={`status ${status === 'error' ? 'error' : isProcessing ? 'processing' : 'ready'}`}>
              {status === 'error' ? (
                <>
                  <span className="status-icon">⚠️</span>
                  <span>{error}</span>
                </>
              ) : status === 'idle' ? (
                <>
                  <span className="status-icon">📋</span>
                  <span>Ready. Select a CSV to start.</span>
                </>
              ) : status === 'uploading' ? (
                <>
                  <span className="status-icon spinner">⟳</span>
                  <span>Uploading CSV...</span>
                </>
              ) : status === 'creating_job' ? (
                <>
                  <span className="status-icon spinner">⟳</span>
                  <span>Creating enrichment job...</span>
                </>
              ) : status.startsWith('running') ? (
                <>
                  <span className="status-icon spinner">⟳</span>
                  <span>
                    Running... {status.includes('%') ? status.replace('running', '').trim() : ''} {rowCount !== null && `(${rowCount} rows)`}
                  </span>
                </>
              ) : status === 'downloading' ? (
                <>
                  <span className="status-icon spinner">⟳</span>
                  <span>Downloading enriched CSV...</span>
                </>
              ) : status === 'done' ? (
                <>
                  <span className="status-icon">✓</span>
                  <span>Done! CSV downloaded successfully.</span>
                </>
              ) : (
                <>
                  <span className="status-icon">📋</span>
                  <span>Ready</span>
                </>
              )}
            </div>

            {(() => {
              // PHASE 4.5.4: Stable partial button logic (no blink/disappear)
              const rowsProcessed = jobMeta?.rows_processed ?? jobMeta?.current ?? 0;
              const jobStatus = (jobMeta?.status || status || "").toLowerCase();
              const partialAvailable = Boolean(jobMeta?.partial_available || jobMeta?.partial_csv_path);

              // Show button if:
              // 1. partial_available flag is set, OR
              // 2. job is running AND has processed some rows
              const showPartial = currentJobId && (
                partialAvailable ||
                (['running', 'processing', 'queued'].includes(jobStatus) && rowsProcessed > 0) ||
                (showPartialDownload && currentJobId)
              );

              return showPartial ? (
                <div className="partial-download-section" style={{ marginTop: '1rem' }}>
                  <button
                    onClick={handleDownloadPartial}
                    className="partial-download-button"
                    style={{
                      padding: '0.75rem 1.5rem',
                      backgroundColor: '#f59e0b',
                      color: 'white',
                      border: 'none',
                      borderRadius: '0.375rem',
                      cursor: 'pointer',
                      fontSize: '1rem',
                      fontWeight: '500'
                    }}
                  >
                    📥 Download partial results
                  </button>
                  <p className="help-text" style={{ marginTop: '0.5rem', fontSize: '0.875rem', color: '#6b7280' }}>
                    Stop-loss checkpoints (every {CHECKPOINT_EVERY} businesses). Some data may be recoverable.
                  </p>
                </div>
              ) : null;
            })()}
          </div>

          {rowsPreview.length > 0 && (
            <div className="preview-section" style={{ marginTop: '1.5rem' }}>
              <h3>Preview ({totalRows} rows)</h3>
              <div style={{ overflowX: 'auto', marginTop: '1rem' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '2px solid #e5e7eb' }}>
                      {pageRows.length > 0 && Object.keys(pageRows[0]).map((key) => (
                        <th key={key} style={{ padding: '0.5rem', textAlign: 'left', fontWeight: '600' }}>
                          {key}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((row, idx) => (
                      <tr key={idx} style={{ borderBottom: '1px solid #f3f4f6' }}>
                        {Object.values(row).map((val, colIdx) => (
                          <td key={colIdx} style={{ padding: '0.5rem' }}>
                            {String(val || '')}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {totalRows > PAGE_SIZE && (
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 12, justifyContent: 'center' }}>
                  <div style={{ fontSize: '0.875rem' }}>
                    Showing {totalRows === 0 ? 0 : startIdx + 1}-{endIdx} of {totalRows}
                  </div>
                  <button
                    disabled={pageSafe <= 1}
                    onClick={() => setPage(pageSafe - 1)}
                    style={{
                      padding: '0.5rem 1rem',
                      backgroundColor: pageSafe <= 1 ? '#e5e7eb' : '#3b82f6',
                      color: pageSafe <= 1 ? '#9ca3af' : 'white',
                      border: 'none',
                      borderRadius: '0.375rem',
                      cursor: pageSafe <= 1 ? 'not-allowed' : 'pointer',
                      fontSize: '0.875rem'
                    }}
                  >
                    Prev
                  </button>
                  <div style={{ fontSize: '0.875rem', fontWeight: '500' }}>
                    Page {pageSafe} / {totalPages}
                  </div>
                  <button
                    disabled={pageSafe >= totalPages}
                    onClick={() => setPage(pageSafe + 1)}
                    style={{
                      padding: '0.5rem 1rem',
                      backgroundColor: pageSafe >= totalPages ? '#e5e7eb' : '#3b82f6',
                      color: pageSafe >= totalPages ? '#9ca3af' : 'white',
                      border: 'none',
                      borderRadius: '0.375rem',
                      cursor: pageSafe >= totalPages ? 'not-allowed' : 'pointer',
                      fontSize: '0.875rem'
                    }}
                  >
                    Next
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Run History Section */}
        <div className="history-section">
          <h3>Run History</h3>
          {runHistory.length === 0 ? (
            <div className="empty-state">No runs yet</div>
          ) : (
            <ul className="history-list">
              {runHistory.map((run, idx) => (
                <li key={idx} className={`history-item ${run.status}`}>
                  <div>
                    <div>{run.name}</div>
                    <div className="time">{run.time}</div>
                  </div>
                  <span className={`status-badge ${run.status}`}>{run.status}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </main>

    </div>
  )
}

export default App
