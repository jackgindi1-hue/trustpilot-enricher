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

      if (lenderNameOverride.trim()) {
        formData.append('lender_name_override', lenderNameOverride.trim())
      }

      // Start the fetch, then immediately set status to processing
      const responsePromise = fetch(`${config.API_BASE_URL}/enrich`, {
        method: 'POST',
        body: formData
      })

      setStatus('processing')

      const response = await responsePromise

      if (!response.ok) {
        const errorText = await response.text()
        throw new Error(`Server error (${response.status}): ${errorText}`)
      }

      // Get the enriched CSV as blob
      const blob = await response.blob()

      // Create download link
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'enriched.csv'
      document.body.appendChild(a)
      a.click()

      // Cleanup
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)

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
      console.error('Enrichment error:', err)
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
              ) : status === 'processing' ? (
                <>
                  <span className="status-icon spinner">⟳</span>
                  <span>
                    Processing... {rowCount !== null && `(${rowCount} rows)`}. This may take a while.
                  </span>
                </>
              ) : status === 'done' ? (
                <>
                  <span className="status-icon">✓</span>
                  <span>Done. Processed {rowCount !== null ? `${rowCount} rows.` : 'successfully.'}</span>
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
            <li>Click "Run Enrichment" to process</li>
            <li>Wait while we enrich your data with business contacts</li>
            <li>Download the enriched CSV automatically</li>
          </ol>

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
