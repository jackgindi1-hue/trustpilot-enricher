# Complete Usage Guide

This guide covers all three ways to use the Trustpilot Enrichment Tool:
1. CLI (Command Line)
2. API Server
3. Web UI

---

## Option 1: CLI (Command Line Interface)

**Best for:** Local processing, automation, scripts, cron jobs

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env with your API keys
```

### Usage

```bash
# Basic usage
python main.py input.csv

# Custom output file
python main.py input.csv -o enriched.csv

# Verbose logging
python main.py input.csv --verbose

# Custom cache location
python main.py input.csv -c my_cache.json

# All options
python main.py input.csv \
  -o enriched.csv \
  -c cache.json \
  --verbose
```

### Pros & Cons

✅ **Pros:**
- No server needed
- Direct file access
- Fast for local files
- Great for automation
- Full control over caching

❌ **Cons:**
- Requires Python environment
- Command line knowledge needed
- No remote access

---

## Option 2: API Server

**Best for:** Remote access, integration with other services, programmatic access

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env with your API keys
```

### Run Locally

```bash
# Development mode (with auto-reload)
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000

# Production mode
uvicorn api_server:app --host 0.0.0.0 --port 8000 --workers 4
```

### Docker Deployment

```bash
# Build image
docker build -t trustpilot-enricher .

# Run container
docker run --env-file .env -p 8000:8000 trustpilot-enricher
```

See `DEPLOY.md` for cloud deployment options (Railway, Render, Fly.io, etc.).

### API Endpoints

#### Health Check
```bash
curl http://localhost:8000/health
```

Response:
```json
{
  "status": "ok"
}
```

#### Enrich CSV
```bash
curl -X POST http://localhost:8000/enrich \
  -F "file=@trustpilot_reviews.csv" \
  -F "lender_name_override=MyLender" \
  -o enriched.csv
```

#### Interactive Documentation
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### Usage from Code

**Python:**
```python
import requests

url = "http://localhost:8000/enrich"
files = {"file": open("input.csv", "rb")}
data = {"lender_name_override": "MyLender"}

response = requests.post(url, files=files, data=data)

with open("enriched.csv", "wb") as f:
    f.write(response.content)
```

**JavaScript/Node.js:**
```javascript
const FormData = require('form-data');
const fs = require('fs');
const axios = require('axios');

const form = new FormData();
form.append('file', fs.createReadStream('input.csv'));
form.append('lender_name_override', 'MyLender');

const response = await axios.post(
  'http://localhost:8000/enrich',
  form,
  {
    headers: form.getHeaders(),
    responseType: 'stream'
  }
);

response.data.pipe(fs.createWriteStream('enriched.csv'));
```

**curl:**
```bash
curl -X POST http://localhost:8000/enrich \
  -F "file=@input.csv" \
  -o enriched.csv
```

### Pros & Cons

✅ **Pros:**
- Remote access
- Multi-user support
- Language agnostic (HTTP API)
- Easy integration
- Scalable

❌ **Cons:**
- Requires server setup
- Need to manage deployment
- Network latency

---

## Option 3: Web UI

**Best for:** Non-technical users, quick one-off enrichments, visual interface

### Setup

```bash
cd web

# Install dependencies
npm install

# Configure API URL (optional)
cp .env.example .env
# Edit VITE_API_BASE_URL if needed
```

### Run Locally

```bash
npm run dev
```

App runs at `http://localhost:3000`

### Build for Production

```bash
npm run build
```

Static files created in `dist/` directory.

### Deploy Frontend

The web UI can be deployed to:
- **Netlify**: Connect GitHub repo, auto-deploy
- **Vercel**: Connect GitHub repo, auto-deploy
- **Cloudflare Pages**: Connect GitHub repo
- **AWS S3 + CloudFront**: Upload `dist/` files
- **GitHub Pages**: `npx gh-pages -d dist`

See `web/README.md` for detailed deployment instructions.

### Configuration

Set the API URL:

**Development (.env.development):**
```bash
VITE_API_BASE_URL=http://localhost:8000
```

**Production (.env.production):**
```bash
VITE_API_BASE_URL=https://your-api.railway.app
```

### Usage

1. Open web app in browser
2. Click "Choose File" and select your CSV
3. (Optional) Enter lender name override
4. Click "Run Enrichment"
5. Wait for processing
6. Enriched CSV downloads automatically

### Pros & Cons

✅ **Pros:**
- User-friendly interface
- No technical knowledge required
- Visual feedback
- Works from any device
- No local setup needed

❌ **Cons:**
- Requires both frontend and backend deployment
- Browser file size limits
- Network dependent

---

## Comparison Matrix

| Feature | CLI | API | Web UI |
|---------|-----|-----|--------|
| **Ease of Use** | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Setup Complexity** | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| **Automation** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ |
| **Remote Access** | ⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Integration** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| **Performance** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |

---

## Recommended Workflows

### For Developers
1. Use **CLI** for local development and testing
2. Deploy **API** for production backend
3. Skip Web UI or use for demos

### For Teams
1. Deploy **API** to cloud (Railway, Render, etc.)
2. Deploy **Web UI** to static hosting (Netlify, Vercel)
3. Share Web UI URL with team members

### For End Users
1. Use **Web UI** exclusively
2. IT team handles backend deployment
3. No technical knowledge required

### For Automation
1. Use **CLI** in cron jobs or scripts
2. Or use **API** from automated workflows
3. Web UI not suitable for automation

---

## Common Tasks

### Process Single File

**CLI:**
```bash
python main.py reviews.csv
```

**API:**
```bash
curl -X POST http://localhost:8000/enrich \
  -F "file=@reviews.csv" \
  -o enriched.csv
```

**Web UI:**
Upload via browser interface

### Batch Processing

**CLI:**
```bash
for file in *.csv; do
  python main.py "$file" -o "enriched_${file}"
done
```

**API:**
```python
import glob
import requests

for file_path in glob.glob("*.csv"):
    files = {"file": open(file_path, "rb")}
    response = requests.post(
        "http://localhost:8000/enrich",
        files=files
    )

    output_name = f"enriched_{file_path}"
    with open(output_name, "wb") as f:
        f.write(response.content)
```

**Web UI:**
Process files one at a time manually

### Integration with Data Pipeline

**CLI:**
```bash
# Example: Airflow DAG task
python main.py /data/input.csv -o /data/output.csv
```

**API:**
```python
# Example: In a data processing script
import requests

response = requests.post(
    "https://api.example.com/enrich",
    files={"file": open("data.csv", "rb")}
)

# Continue processing...
```

---

## Troubleshooting

### CLI Issues

**Import errors:**
```bash
pip install -r requirements.txt
```

**File not found:**
```bash
# Use absolute path
python main.py /full/path/to/file.csv
```

### API Issues

**CORS errors:**
Set `FRONTEND_ORIGIN` in backend:
```bash
export FRONTEND_ORIGIN=http://localhost:3000
```

**Connection refused:**
Check API is running:
```bash
curl http://localhost:8000/health
```

### Web UI Issues

**API connection failed:**
Check `VITE_API_BASE_URL` in `.env`

**Build errors:**
```bash
rm -rf node_modules
npm install
```

---

## Next Steps

1. **Choose your interface** based on your needs
2. **Follow setup instructions** for that interface
3. **Configure API keys** in `.env` file
4. **Test with sample data** (`sample_input.csv`)
5. **Deploy to production** if needed

## Documentation

- Main README: `README.md`
- Deployment Guide: `DEPLOY.md`
- Web UI Guide: `web/README.md`
- API Documentation: `http://localhost:8000/docs` (when server running)
- Project Overview: `PROJECT_OVERVIEW.md`

---

**Questions?** Check the documentation or test with `sample_input.csv` first.
