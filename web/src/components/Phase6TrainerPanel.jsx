/**
 * PHASE 6 — Classification Override Trainer Panel
 *
 * Provides UI for:
 * - Viewing current Phase 6 mode
 * - Adding business/person overrides
 * - Training the token-based model
 *
 * SAFE: Only affects Phase 6 routes, does NOT touch existing flows.
 */
import React, { useState, useEffect } from "react";

export default function Phase6TrainerPanel({ API_BASE }) {
  const [mode, setMode] = useState("off");
  const [biz, setBiz] = useState("");
  const [per, setPer] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const API = (API_BASE || "").replace(/\/+$/, "");

  async function loadStatus() {
    try {
      const r = await fetch(`${API}/phase6/status`);
      const j = await r.json();
      setMode(j.phase6_mode || "off");
    } catch {
      // Ignore errors - Phase 6 may not be enabled
    }
  }

  useEffect(() => {
    loadStatus();
  }, []);

  function splitLines(s) {
    return (s || "").split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  }

  async function saveOverrides(label) {
    setErr("");
    setMsg("");
    try {
      const names = splitLines(label === "business" ? biz : per);
      if (!names.length) {
        setErr("No names provided.");
        return;
      }
      const r = await fetch(`${API}/phase6/overrides`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ names, label, note })
      });
      const t = await r.text();
      if (!r.ok) throw new Error(t);
      setMsg(`Saved ${names.length} ${label} overrides.`);
    } catch (e) {
      setErr(String(e?.message || e));
    }
  }

  async function train() {
    setErr("");
    setMsg("");
    try {
      const business_names = splitLines(biz);
      const person_names = splitLines(per);
      if (!business_names.length && !person_names.length) {
        setErr("Provide at least 1 name.");
        return;
      }
      const r = await fetch(`${API}/phase6/train`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ business_names, person_names })
      });
      const t = await r.text();
      if (!r.ok) throw new Error(t);
      setMsg(`Trained model: ${t}`);
    } catch (e) {
      setErr(String(e?.message || e));
    }
  }

  return (
    <div style={{
      border: "1px solid #333",
      borderRadius: 12,
      padding: 16,
      marginTop: 16,
      backgroundColor: "#1a1a1a"
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{
          fontWeight: 700,
          fontSize: 14,
          color: "#00ff41",
          textTransform: "uppercase",
          letterSpacing: 1
        }}>
          Phase 6 Trainer
        </div>
        <div style={{ opacity: 0.8, fontSize: 12, color: "#888" }}>
          Mode: <b style={{ color: mode === "off" ? "#666" : mode === "shadow" ? "#ff6b00" : "#00ff41" }}>{mode}</b>
        </div>
      </div>

      <p style={{ opacity: 0.7, marginTop: 8, marginBottom: 12, fontSize: 12, color: "#888" }}>
        Start with <b>PHASE6_MODE=shadow</b> (no output changes).
        Then flip to <b>PHASE6_MODE=enforce</b> only when ready.
      </p>

      <div style={{ marginBottom: 10 }}>
        <div style={{ marginBottom: 6, fontSize: 12, color: "#aaa" }}><b>Note (optional)</b></div>
        <input
          value={note}
          onChange={e => setNote(e.target.value)}
          style={{
            width: "100%",
            padding: 8,
            borderRadius: 8,
            border: "1px solid #333",
            background: "#2a2a2a",
            color: "#e0e0e0",
            fontSize: 13
          }}
          placeholder="why you marked these names"
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <div style={{ marginBottom: 6, fontSize: 12, color: "#aaa" }}><b>Mark as BUSINESS</b> (one per line)</div>
          <textarea
            value={biz}
            onChange={e => setBiz(e.target.value)}
            rows={8}
            style={{
              width: "100%",
              padding: 8,
              borderRadius: 8,
              border: "1px solid #333",
              background: "#2a2a2a",
              color: "#e0e0e0",
              fontSize: 12,
              resize: "vertical"
            }}
            placeholder="ABC Trucking LLC&#10;XYZ Services Inc&#10;..."
          />
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button
              onClick={() => saveOverrides("business")}
              style={{
                padding: "8px 14px",
                borderRadius: 8,
                border: "none",
                cursor: "pointer",
                backgroundColor: "#00ff41",
                color: "#000",
                fontWeight: 600,
                fontSize: 12
              }}
            >
              Save business overrides
            </button>
          </div>
        </div>

        <div>
          <div style={{ marginBottom: 6, fontSize: 12, color: "#aaa" }}><b>Mark as PERSON</b> (one per line)</div>
          <textarea
            value={per}
            onChange={e => setPer(e.target.value)}
            rows={8}
            style={{
              width: "100%",
              padding: 8,
              borderRadius: 8,
              border: "1px solid #333",
              background: "#2a2a2a",
              color: "#e0e0e0",
              fontSize: 12,
              resize: "vertical"
            }}
            placeholder="John Smith&#10;Jane Doe&#10;..."
          />
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button
              onClick={() => saveOverrides("person")}
              style={{
                padding: "8px 14px",
                borderRadius: 8,
                border: "1px solid #ff6b00",
                cursor: "pointer",
                backgroundColor: "transparent",
                color: "#ff6b00",
                fontWeight: 600,
                fontSize: 12
              }}
            >
              Save person overrides
            </button>
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button
          onClick={train}
          style={{
            padding: "10px 20px",
            borderRadius: 10,
            border: "none",
            cursor: "pointer",
            backgroundColor: "#3b82f6",
            color: "white",
            fontWeight: 600,
            fontSize: 13
          }}
        >
          Train model
        </button>
        <button
          onClick={loadStatus}
          style={{
            padding: "10px 16px",
            borderRadius: 10,
            border: "1px solid #555",
            cursor: "pointer",
            backgroundColor: "transparent",
            color: "#888",
            fontWeight: 500,
            fontSize: 13
          }}
        >
          Refresh status
        </button>
      </div>

      {msg && (
        <div style={{
          marginTop: 10,
          padding: 10,
          borderRadius: 8,
          backgroundColor: "rgba(0, 255, 65, 0.1)",
          color: "#00ff41",
          fontSize: 13
        }}>
          {msg}
        </div>
      )}

      {err && (
        <div style={{
          marginTop: 10,
          padding: 10,
          borderRadius: 8,
          backgroundColor: "rgba(255, 68, 68, 0.1)",
          color: "#ff4444",
          whiteSpace: "pre-wrap",
          fontSize: 13
        }}>
          {err}
        </div>
      )}
    </div>
  );
}
