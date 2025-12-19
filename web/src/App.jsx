import { useState } from 'react'
import config from './config'
import './App.css'

function App() {
  const [file, setFile] = useState(null)
  const [lenderNameOverride, setLenderNameOverride] = useState('')
  const [status, setStatus] = useState('idle')
  const [isProcessing, setIsProcessing] = useState(false)
  const [error, setError] = useState(null)
  const [rowCount, setRowCount] = useState(null)

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

  const handleSubmit = async (e) => {
    e.preventDefault()

    // Validation
    if (!file) {
      setError('Please select a CSV file first')
      return
    }

    setIsProcessing(true)
    setError(null)
    setStatus('uploading')

    try {
      // Prepare form data
      const formData = new FormData()
      formData.append('file', file)
      formData.append('concurrency', '8')

      if (lenderNameOverride.trim()) {
        formData.append('lender_name_override', lenderNameOverride.trim())
      }

      // ============================================================
      // PHASE 4 CORRECT FLOW: POST /jobs -> poll -> download
      // ============================================================

      // 1) CREATE JOB (returns JSON only)
      setStatus('creating_job')
      const createRes = await fetch(`${config.API_BASE_URL}/jobs`, {
        method: 'POST',
        body: formData
      })

      if (!createRes.ok) {
        const errorText = await createRes.text()
        throw new Error(`Job creation failed (${createRes.status}): ${errorText}`)
      }

      // Parse JSON response
      const createData = await createRes.json()
      const jobId = createData.job_id

      if (!jobId) {
        throw new Error('Missing job_id from server response')
      }

      console.log(`✅ Job created: ${jobId}`)
      setStatus('running')

      // 2) POLL STATUS UNTIL DONE (resilient polling with transient failure tolerance)
      let pollCount = 0
      let pollFails = 0
      const maxPolls = 600 // 10 minutes

      while (pollCount < maxPolls) {
        await new Promise(r => setTimeout(r, 1000))
        pollCount++

        let meta = {}
        try {
          const statusRes = await fetch(`${config.API_BASE_URL}/jobs/${jobId}`, { cache: 'no-store' })
          if (!statusRes.ok) {
            const txt = await statusRes.text().catch(() => '')
            throw new Error(`poll_http_${statusRes.status}:${txt.slice(0, 120)}`)
          }
          meta = await statusRes.json().catch(() => ({}))
          pollFails = 0 // reset on successful poll
        } catch (e) {
          pollFails += 1
          console.log(`Poll glitch (${pollFails}/12): ${String(e).slice(0, 160)}`)
          setStatus(`running (retrying poll ${pollFails}/12...)`)
          // tolerate up to 12 consecutive failures (~18-30 seconds)
          if (pollFails >= 12) throw e
          await new Promise(r => setTimeout(r, 1500))
          continue
        }

        const jobStatus = (meta.status || '').toLowerCase()
        const progress = meta.progress || 0

        console.log(`Poll ${pollCount}: status=${jobStatus}, progress=${Math.round(progress * 100)}%`)

        // Update UI with progress
        if (progress > 0) {
          setStatus(`running (${Math.round(progress * 100)}%)`)
        }

        // Check if done
        if (jobStatus === 'done') {
          console.log('✅ Job completed!')
          setStatus('downloading')
          break
        }

        if (jobStatus === 'error') {
          throw new Error(meta.error || 'Job failed on server')
        }
      }

      if (pollCount >= maxPolls) {
        throw new Error('Job timeout: exceeded 10 minutes')
      }

      // 3) DOWNLOAD CSV (ONLY AFTER status == "done")
      const downloadRes = await fetch(`${config.API_BASE_URL}/jobs/${jobId}/download`)

      if (!downloadRes.ok) {
        const errorText = await downloadRes.text()
        throw new Error(`Download failed (${downloadRes.status}): ${errorText}`)
      }

      // Validate content-type (must be CSV, not JSON)
      const contentType = downloadRes.headers.get('content-type') || ''
      if (contentType.includes('application/json')) {
        const jsonText = await downloadRes.text()
        throw new Error(`Server returned JSON instead of CSV: ${jsonText.slice(0, 200)}`)
      }

      // Download as blob
      const blob = await downloadRes.blob()

      // Double-check: refuse JSON-looking content
      const buffer = await blob.arrayBuffer()
      const header = new TextDecoder('utf-8').decode(buffer.slice(0, 64)).trim()
      if (header.startsWith('{') || header.startsWith('[')) {
        throw new Error(`Refusing to download JSON-looking content: ${header}`)
      }

      // Create download link
      const url = window.URL.createObjectURL(new Blob([buffer], { type: 'text/csv' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `enriched-${jobId}.csv`
      document.body.appendChild(a)
      a.click()

      // Cleanup
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)

      console.log('✅ CSV downloaded successfully!')
      setStatus('done')
      setIsProcessing(false)

      // Reset form after success
      setTimeout(() => {
        setFile(null)
        setLenderNameOverride('')
        setRowCount(null)
        setStatus('idle')
        // Reset file input
        const fileInput = document.getElementById('csvFile')
        if (fileInput) fileInput.value = ''
      }, 3000)

    } catch (err) {
      console.error('❌ Enrichment error:', err)
      setError(err.message || 'Enrichment failed. Please try again.')
      setStatus('error')
      setIsProcessing(false)
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Trustpilot Review Enrichment</h1>
        <p className="subtitle">Upload your Trustpilot CSV to enrich it with business contact information</p>
      </header>

      <main className="app-main">
        <div className="card">
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label htmlFor="csvFile">
                Select Trustpilot CSV File *
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
          </div>
        </div>

        <div className="info-section">
          <h3>How it works</h3>
          <ol>
            <li>Upload your Trustpilot review CSV file</li>
            <li>Optionally specify a lender name override</li>
            <li>Click "Run Enrichment" to create an async job</li>
            <li>Watch live progress as we enrich your data</li>
            <li>CSV downloads automatically when complete</li>
          </ol>
          <p className="help-text">
            ℹ️ Using async job system for reliability and progress tracking
          </p>

          <h3>Data Sources</h3>
          <ul>
            <li>🗺️ Google Maps - Phone numbers, addresses</li>
            <li>⭐ Yelp - Business verification</li>
            <li>📧 Hunter.io & Snov.io - Email discovery</li>
            <li>🏢 Apollo & FullEnrich - Company data</li>
            <li>⚖️ OpenCorporates - Legal verification</li>
          </ul>

          <h3>Output Format</h3>
          <p>
            The enriched CSV includes 36 columns with classification results,
            primary contact info, all discovered contacts, confidence scores,
            and enrichment metadata.
          </p>
        </div>
      </main>

      <footer className="app-footer">
        <p>
          API: <code>{config.API_BASE_URL}</code>
        </p>
        <p>
          Powered by multi-source business data enrichment
        </p>
      </footer>
    </div>
  )
}

export default App
