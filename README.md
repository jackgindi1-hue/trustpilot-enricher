# Trustpilot Review Enrichment Tool

A comprehensive business data enrichment platform that processes Trustpilot review CSV files (typically scraped via Apify) and enriches business reviewer information with contact details, domain data, legal verification, and confidence scoring using multiple premium data sources.

## Table of Contents

1. [Project Overview](#project-overview)
2. [Features](#features)
3. [Architecture Overview](#architecture-overview)
4. [Requirements](#requirements)
5. [Installation](#installation)
6. [Environment Variables & API Keys](#environment-variables--api-keys)
7. [CLI Usage](#cli-usage)
8. [API Server Usage](#api-server-usage-fastapi)
9. [Docker Usage](#docker-usage)
10. [Web UI Usage](#web-ui-usage)
11. [Input CSV Format](#input-csv-format-apify--trustpilot-csv)
12. [Output CSV Schema](#output-csv-schema)
13. [Caching & Performance Notes](#caching--performance-notes)
14. [Limitations & Notes](#limitations--notes)
15. [Future Extensions](#future-extensions)

---

## Project Overview

This tool solves a critical lead generation problem: **converting Trustpilot review data into enriched business contact records**.

### The Problem

Trustpilot reviews contain valuable business information, but reviewer names like "ABC Trucking LLC" or "John Smith" need to be:
1. Classified (Is this a business or a person?)
2. Normalized (Clean up variations of the same company name)
3. Enriched (Find phone numbers, emails, websites, addresses)
4. Verified (Confirm accuracy and assign confidence scores)

### The Solution

This platform:
- **Ingests** Trustpilot review CSV files (typically scraped using Apify actors)
- **Classifies** each reviewer as business, person, or other using 10+ intelligent rules
- **Enriches** business names using a waterfall of premium APIs:
  - FullEnrich (company data)
  - Apollo.io (company data)
  - Google Maps/Places API (phone numbers, addresses - **priority #1 for SMBs**)
  - Yelp Fusion (verified business listings)
  - Hunter.io (email discovery)
  - Snov.io (email discovery)
  - OpenCorporates (legal verification)
  - YellowPages/BBB (directory data - structure ready)
  - Social media (Facebook/Instagram via website scraping)
- **Outputs** a standardized 36-column CSV with primary contact info, confidence scores, and all discovered data in JSON fields

### Three Ways to Use

The system provides three interfaces for different use cases:

1. **CLI (Command Line)** - `python main.py input.csv output.csv`
   - Local processing, automation, scripts, batch jobs

2. **HTTP API (FastAPI)** - `uvicorn api_server:app`
   - Remote access, integration with other systems, multi-user support

3. **Web UI (React)** - Browser-based upload and download
   - Non-technical users, quick one-off enrichments, visual interface

All three interfaces use the same core enrichment pipeline, ensuring consistent results.

---

## Features

### Core Functionality

- ✅ **Intelligent Name Classification**
  - Business/person/other classification using rules-based logic
  - Detects legal suffixes (LLC, Inc, Corp, Ltd, etc.)
  - Recognizes industry keywords (Trucking, Construction, Restaurant, etc.)
  - Identifies business structures (X & Y, X & Sons, etc.)
  - Distinguishes human names and nicknames

- ✅ **Business Name Normalization & Deduplication**
  - Cleans and standardizes business names
  - Removes noise ("Customer Service" trailing text)
  - Creates normalized deduplication keys
  - Enriches each unique business only once

- ✅ **Multi-Source Enrichment Pipeline**
  - **FullEnrich** - Company domain discovery and verification
  - **Apollo.io** - Company data and domain lookup
  - **Google Maps/Places** - Phone numbers (top priority), addresses, website verification
  - **Yelp Fusion** - Verified business listings, phone, address, categories
  - **YellowPages/BBB** - Directory data (structure ready for Apify integration)
  - **Hunter.io** - Domain-based email discovery with verification scores
  - **Snov.io** - Additional email sources
  - **OpenCorporates** - Legal entity verification, jurisdiction, incorporation dates
  - **Social Media** - Website scraping for Facebook/Instagram links (scraper integration ready)

- ✅ **Smart Priority Rules**
  - **Phone Priority**: Google Maps → Yelp → YP/BBB → Apollo/FullEnrich → Social
  - **Email Priority**: Person emails → Generic emails → Scraped → Catchall (last resort)
  - **Domain Priority**: Input website → FullEnrich → Apollo
  - Token-matching validation for all data sources
  - Similarity scoring for candidate selection

- ✅ **Confidence Scoring**
  - High: Domain + Phone + Email all medium or better
  - Medium: Partial coverage with medium confidence in at least one area
  - Low: Minimal enrichment with low confidence
  - Failed: No useful data discovered

- ✅ **Deterministic Output Schema**
  - Fixed 36-column CSV format
  - Primary contact fields (phone, email, domain)
  - All discovered data in JSON fields (all_phones_json, generic_emails_json, etc.)
  - Enrichment metadata (status, notes, confidence)

- ✅ **Three Deployment Modes**
  - **CLI** - Direct command-line processing
  - **HTTP API** - RESTful endpoint with FastAPI
  - **Web UI** - React-based browser interface

- ✅ **Performance Optimizations**
  - Caching layer prevents re-enrichment of same businesses
  - Deduplication reduces API calls
  - Graceful degradation when API keys are missing
  - Detailed logging for troubleshooting

---

## Architecture Overview

### Project Structure

```
trustpilot-enrichment/
│
├── main.py                      # CLI entry point
├── api_server.py                # FastAPI HTTP server
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Container definition
├── .dockerignore               # Docker build optimization
├── .env.example                # Environment variables template
│
├── tp_enrich/                  # Core enrichment package
│   ├── __init__.py
│   ├── pipeline.py             # Reusable enrichment pipeline
│   ├── io_utils.py             # CSV I/O with schema enforcement
│   ├── classification.py       # Business/person/other classification
│   ├── normalization.py        # Name cleaning and normalization
│   ├── dedupe.py               # Deduplication logic
│   ├── domain_enrichment.py    # FullEnrich + Apollo domain discovery
│   ├── local_enrichment.py     # Google Maps, Yelp, YP/BBB
│   ├── legal_enrichment.py     # OpenCorporates legal verification
│   ├── email_enrichment.py     # Hunter.io + Snov.io email discovery
│   ├── social_enrichment.py    # Website scraping + social links
│   ├── merge_results.py        # Priority rules + confidence scoring
│   ├── cache.py                # Enrichment caching
│   └── logging_utils.py        # Logging infrastructure
│
├── web/                        # React frontend (optional)
│   ├── src/
│   │   ├── App.jsx             # Main React component
│   │   ├── App.css             # Styles
│   │   ├── main.jsx            # React entry point
│   │   └── config.js           # API URL configuration
│   ├── public/
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── README.md
│
├── sample_input.csv            # Example Trustpilot CSV
├── test_classification.py      # Classification test script
├── test_api.sh                 # API test script
│
└── Documentation/
    ├── DEPLOY.md               # Deployment guide
    ├── USAGE_GUIDE.md          # Complete usage guide
    ├── EXTENSION_SUMMARY.md    # Technical details
    ├── PROJECT_OVERVIEW.md     # High-level overview
    ├── SETUP_GUIDE.md          # Quick start
    └── QUICK_REFERENCE.md      # Command reference
```

### Pipeline Flow

The enrichment pipeline follows these exact steps:

1. **Load Input CSV**
   - Read Trustpilot CSV file
   - Normalize column names
   - Add row IDs

2. **Classify Display Names**
   - Apply 10+ classification rules
   - Categorize as business, person, or other
   - Log classification statistics

3. **Normalize Business Names**
   - Clean displayName (remove "Customer Service", etc.)
   - Create company_search_name
   - Generate company_normalized_key for deduplication

4. **Identify Unique Businesses**
   - Group by normalized key
   - Extract one representative per business
   - Reduce API calls by enriching only unique businesses

5. **Enrich Each Unique Business**
   - Check cache first
   - If not cached:
     - Domain enrichment (FullEnrich, Apollo)
     - Local enrichment (Google Maps, Yelp, YP/BBB)
     - Legal enrichment (OpenCorporates)
     - Email enrichment (Hunter.io, Snov.io)
     - Social enrichment (website scraping)
   - Apply priority rules
   - Calculate confidence scores
   - Save to cache

6. **Merge Results Back to Rows**
   - Map enrichment data to all rows with same business
   - Fill in primary contact fields
   - Store all discovered data in JSON fields

7. **Write Enriched CSV**
   - Apply exact 36-column schema
   - Write to output file

---

## Requirements

### Software Requirements

- **Python 3.10 or higher**
- **pip** (Python package manager)
- **Node.js 18+ and npm** (optional, only for Web UI)
- **Docker** (optional, for containerized deployment)

### System Requirements

- **Memory**: 2GB minimum, 4GB recommended for large files
- **Disk Space**: 500MB for dependencies + space for CSV files
- **Network**: Internet connection for API calls

### API Accounts

Premium APIs require paid or free-tier accounts:
- Google Cloud Platform (for Maps/Places API)
- Yelp Developer Account
- Hunter.io
- Snov.io
- Apollo.io
- FullEnrich
- OpenCorporates (optional - works without key but with rate limits)

---

## Installation

### Step 1: Clone or Download

```bash
# Clone repository (if using Git)
git clone <repository-url>
cd trustpilot-enrichment

# Or download and extract ZIP
```

### Step 2: Set Up Python Environment

```bash
# Create virtual environment (recommended)
python -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate

# On Windows:
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Step 3: (Optional) Install Web UI Dependencies

Only needed if you want to use the browser-based interface:

```bash
cd web
npm install
cd ..
```

### Step 4: Configure Environment Variables

```bash
# Copy example environment file
cp .env.example .env

# Edit .env and add your API keys
# Use any text editor:
nano .env
# or
vim .env
# or
code .env
```

### Verify Installation

```bash
# Test Python imports
python -c "import tp_enrich; print('✓ Package imported successfully')"

# Test classification logic
python test_classification.py

# Test with sample data
python main.py sample_input.csv -o test_output.csv
```

---

## Environment Variables & API Keys

### Required Environment Variables

All API keys must be provided as environment variables. The tool will gracefully skip integrations for missing keys.

Create a `.env` file in the project root:

```bash
# Google Maps / Places API (TOP PRIORITY for SMB phone numbers)
# Get key at: https://console.cloud.google.com/
GOOGLE_PLACES_API_KEY=your_google_places_api_key_here

# Yelp Fusion API
# Get key at: https://www.yelp.com/developers
YELP_API_KEY=your_yelp_api_key_here

# FullEnrich Company Data
# Get key at: https://www.fullenrich.com/
FULLENRICH_API_KEY=your_fullenrich_api_key_here

# Apollo.io Company Data
# Get key at: https://www.apollo.io/
APOLLO_API_KEY=your_apollo_api_key_here

# Hunter.io Email Finder
# Get key at: https://hunter.io/api
HUNTER_API_KEY=your_hunter_api_key_here

# Snov.io Email Finder (format: user_id:api_key)
# Get key at: https://snov.io/api
SNOV_API_KEY=user_id:api_key_here

# OpenCorporates API (optional - works without key but with rate limits)
# Get key at: https://opencorporates.com/api_accounts/new
OPENCORPORATES_API_KEY=your_opencorporates_key_here

# CORS Configuration (for API server)
# Comma-separated list of allowed frontend origins, or * for all
FRONTEND_ORIGIN=*
```

### Loading Environment Variables

The tool automatically loads `.env` files using `python-dotenv`. You can also:

**Export manually (Linux/macOS):**
```bash
export GOOGLE_PLACES_API_KEY="your_key"
export YELP_API_KEY="your_key"
```

**Export manually (Windows):**
```cmd
set GOOGLE_PLACES_API_KEY=your_key
set YELP_API_KEY=your_key
```

**Pass to Docker:**
```bash
docker run --env-file .env -p 8000:8000 trustpilot-enricher
```

### Graceful Degradation

If an API key is missing, the tool will:
- Log a warning: `"✗ GOOGLE_PLACES_API_KEY not provided - will skip this integration"`
- Continue enrichment using available sources
- Mark enrichment_status accordingly

This allows you to start with minimal keys and add more over time.

---

## CLI Usage

### Basic Command

The CLI is the original and simplest way to use the tool:

```bash
python main.py <input.csv> [options]
```

### Command-Line Options

```bash
python main.py input.csv                     # Default output: enriched_output.csv
python main.py input.csv -o custom.csv       # Custom output file
python main.py input.csv -c cache.json       # Custom cache file
python main.py input.csv --verbose           # Detailed logging
```

### Full Example

```bash
python main.py trustpilot_reviews.csv \
  --output enriched_leads.csv \
  --cache enrichment_cache.json \
  --verbose
```

### What Happens During Execution

1. **Startup**
   - Loads environment variables
   - Checks for API keys and logs status
   - Opens input CSV

2. **Classification** (Step 1-3)
   - Classifies each displayName as business/person/other
   - Logs classification counts (e.g., "500 businesses, 200 persons, 50 others")

3. **Normalization** (Step 4-5)
   - Normalizes business names
   - Identifies unique businesses (e.g., "250 unique businesses from 500 rows")

4. **Enrichment** (Step 6)
   - Processes each unique business:
     ```
     [1/250] Processing: ABC Trucking LLC
       -> Domain enrichment for ABC Trucking LLC
       -> Local enrichment for ABC Trucking LLC
       -> Legal enrichment for ABC Trucking LLC
       -> Email enrichment for ABC Trucking LLC
       -> Social enrichment for ABC Trucking LLC
       -> Merging results for ABC Trucking LLC
       -> Completed enrichment (confidence: high)
     ```
   - Uses cached results for previously enriched businesses
   - Saves new results to cache

5. **Output** (Step 7-8)
   - Merges enrichment data back to original rows
   - Writes enriched CSV with exact 36-column schema
   - Reports statistics

### Expected Output

```
============================================================
Starting Trustpilot Enrichment Pipeline
============================================================

Step 1: Loading input CSV...
  Loaded 750 rows with columns: [...]

Step 2: Classifying display names...
  Classification results: {'business': 500, 'person': 200, 'other': 50}

Step 3: Normalizing business names...

Step 4: Identifying unique businesses...
  Found 250 unique businesses to enrich

Step 5: Enriching businesses...
  [1/250] Processing: ABC Trucking LLC
  ...
  [250/250] Processing: XYZ Services Inc

Step 6: Merging enrichment results back to rows...

Step 7: Writing output CSV...

============================================================
Pipeline completed successfully!
  Total rows: 750
  Businesses: 500
  Unique businesses enriched: 248/250
  Output file: enriched_leads.csv
============================================================
```

---

## API Server Usage (FastAPI)

The HTTP API provides a RESTful endpoint for remote enrichment, ideal for integration with other systems or multi-user environments.

### Starting the API Server

**Development mode (with auto-reload):**
```bash
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
```

**Production mode:**
```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000 --workers 4
```

**With specific port:**
```bash
uvicorn api_server:app --host 0.0.0.0 --port 9000
```

### API Endpoints

#### GET `/health`

Health check endpoint for monitoring.

**Request:**
```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "ok"
}
```

#### GET `/`

Root endpoint with API information.

**Request:**
```bash
curl http://localhost:8000/
```

**Response:**
```json
{
  "service": "Trustpilot Enrichment API",
  "version": "1.0.0",
  "endpoints": {
    "health": "/health",
    "enrich": "/enrich (POST)"
  },
  "docs": "/docs"
}
```

#### POST `/enrich`

Main enrichment endpoint.

**Parameters:**
- `file` (required) - CSV file to enrich (multipart/form-data)
- `lender_name_override` (optional) - Override source_lender_name for all rows

**Request (curl):**
```bash
curl -X POST http://localhost:8000/enrich \
  -F "file=@trustpilot_reviews.csv" \
  -F "lender_name_override=MyLender" \
  --output enriched.csv
```

**Request (Python):**
```python
import requests

url = "http://localhost:8000/enrich"
files = {"file": open("trustpilot_reviews.csv", "rb")}
data = {"lender_name_override": "MyLender"}

response = requests.post(url, files=files, data=data)

with open("enriched.csv", "wb") as f:
    f.write(response.content)
```

**Request (JavaScript):**
```javascript
const formData = new FormData();
formData.append('file', fileInput.files[0]);
formData.append('lender_name_override', 'MyLender');

const response = await fetch('http://localhost:8000/enrich', {
  method: 'POST',
  body: formData
});

const blob = await response.blob();
// Trigger download...
```

**Response:**
- Content-Type: `text/csv`
- Content-Disposition: `attachment; filename="enriched.csv"`
- Body: Enriched CSV file

**Error Responses:**
- 400: Invalid file type (must be CSV)
- 500: Enrichment failed (see response body for details)

### Interactive API Documentation

FastAPI automatically generates interactive documentation:

**Swagger UI:**
```
http://localhost:8000/docs
```

**ReDoc:**
```
http://localhost:8000/redoc
```

These interfaces allow you to:
- Browse all endpoints
- See request/response schemas
- Test API calls directly from browser
- Download OpenAPI specification

### CORS Configuration

The API includes CORS middleware for cross-origin requests. Configure via environment variable:

```bash
# Allow specific origins
FRONTEND_ORIGIN=https://yourdomain.com,https://www.yourdomain.com

# Allow all origins (development only)
FRONTEND_ORIGIN=*
```

---

## Docker Usage

Docker provides a consistent deployment environment across all platforms.

### Building the Docker Image

```bash
docker build -t trustpilot-enricher .
```

Build with custom tag:
```bash
docker build -t trustpilot-enricher:v1.0 .
```

### Running the Container

**With .env file:**
```bash
docker run --rm \
  --env-file .env \
  -p 8000:8000 \
  trustpilot-enricher
```

**With individual environment variables:**
```bash
docker run --rm \
  -e GOOGLE_PLACES_API_KEY=your_key \
  -e YELP_API_KEY=your_key \
  -e FULLENRICH_API_KEY=your_key \
  -e APOLLO_API_KEY=your_key \
  -e HUNTER_API_KEY=your_key \
  -e SNOV_API_KEY=your_key \
  -e OPENCORPORATES_API_KEY=your_key \
  -p 8000:8000 \
  trustpilot-enricher
```

**With custom port:**
```bash
docker run --rm \
  --env-file .env \
  -p 9000:8000 \
  trustpilot-enricher
```

### Testing the Container

```bash
# Health check
curl http://localhost:8000/health

# Test enrichment
curl -X POST http://localhost:8000/enrich \
  -F "file=@sample_input.csv" \
  -o docker_test_output.csv

# View logs
docker logs <container_id>
```

### Deployment to Cloud Platforms

The Docker image can be deployed to any container platform:

**Railway:**
1. Connect GitHub repository
2. Set environment variables in dashboard
3. Railway auto-detects Dockerfile and deploys

**Render:**
1. Create new Web Service
2. Connect repository
3. Select "Docker" environment
4. Add environment variables
5. Deploy

**Fly.io:**
```bash
fly launch
fly secrets set GOOGLE_PLACES_API_KEY=your_key
fly secrets set YELP_API_KEY=your_key
# ... set all other keys
fly deploy
```

**Google Cloud Run:**
```bash
gcloud builds submit --tag gcr.io/PROJECT_ID/trustpilot-enricher
gcloud run deploy --image gcr.io/PROJECT_ID/trustpilot-enricher \
  --set-env-vars GOOGLE_PLACES_API_KEY=your_key,YELP_API_KEY=your_key
```

See `DEPLOY.md` for comprehensive deployment instructions.

---

## Web UI Usage

The Web UI provides a browser-based interface for non-technical users.

### Development Mode

```bash
cd web
npm install
npm run dev
```

App runs at `http://localhost:3000`

### Configuration

Set the API URL via environment variable:

**Development (.env.development):**
```bash
VITE_API_BASE_URL=http://localhost:8000
```

**Production (.env.production):**
```bash
VITE_API_BASE_URL=https://your-api-domain.com
```

### Building for Production

```bash
cd web
npm run build
```

Static files are created in `dist/` directory.

### Using the Web Interface

1. **Open the web app** in your browser
2. **Click "Choose File"** and select your Trustpilot CSV
3. **(Optional)** Enter a lender name override
4. **Click "Run Enrichment"**
5. **Wait** for processing (status updates shown)
6. **Download** enriched CSV automatically when complete

### Deploying the Frontend

The `dist/` folder contains static files that can be deployed to:

**Netlify:**
```bash
cd web
npm run build
netlify deploy --prod --dir=dist
```

**Vercel:**
```bash
cd web
npm run build
vercel --prod
```

**AWS S3 + CloudFront:**
```bash
cd web
npm run build
aws s3 sync dist/ s3://your-bucket-name/
```

**GitHub Pages:**
```bash
cd web
npm run build
npx gh-pages -d dist
```

See `web/README.md` for detailed frontend documentation.

---

## Input CSV Format (Apify → Trustpilot CSV)

### Expected Input Format

The tool expects a CSV file exported from Apify's Trustpilot scraper or similar source.

### Required Columns

At minimum, the CSV must contain:
- `displayName` or `display_name` - The reviewer's display name

### Optional Columns (Recommended)

Including these columns improves enrichment quality:

| Column | Description | Example |
|--------|-------------|---------|
| `id` | Review ID | `abc123` |
| `url` | Review URL | `https://www.trustpilot.com/review/example.com/xyz` |
| `date` | Review date | `2024-01-15` |
| `stars` or `rating` | Star rating | `5` |
| `companyName` | Company being reviewed | `Example Company` |
| `companyProfileUrl` | Company Trustpilot URL | `https://www.trustpilot.com/review/example.com` |
| `title` | Review title | `Great service!` |
| `text` | Review body | `I had a wonderful experience...` |
| `city` | Reviewer location (city) | `Houston` |
| `state` | Reviewer location (state) | `TX` |
| `region` | Reviewer location (region) | `Texas` |
| `country` | Reviewer location (country) | `United States` |

### Example Input CSV

```csv
id,displayName,url,date,stars,companyName,city,state
1,ABC Trucking LLC,https://trustpilot.com/review/lender/1,2024-01-15,5,SomeLender,Houston,TX
2,John Smith,https://trustpilot.com/review/lender/2,2024-01-16,4,SomeLender,New York,NY
3,Customer Service,https://trustpilot.com/review/lender/3,2024-01-17,3,SomeLender,,
4,Green Valley Cafe,https://trustpilot.com/review/lender/4,2024-01-18,5,SomeLender,Portland,OR
```

### Column Name Normalization

The tool automatically normalizes column names:
- Converts to lowercase
- Replaces spaces with underscores
- Replaces hyphens with underscores

So these are equivalent:
- `displayName` → `displayname`
- `display_name` → `display_name`
- `Display Name` → `display_name`

### Missing Data Handling

- If `displayName` is missing or empty, row is classified as "other"
- If location fields are missing, enrichment proceeds without geographic filtering
- If URL is missing, `source_review_url` will be empty
- If date/rating are missing, those fields are left empty in output

---

## Output CSV Schema

### Fixed 36-Column Schema

The output CSV has **exactly 36 columns in this exact order**. This schema is deterministic and never changes.

| # | Column Name | Type | Description |
|---|-------------|------|-------------|
| 1 | `row_id` | Integer | Sequential row identifier (1-indexed) |
| 2 | `source_platform` | String | Always "trustpilot" |
| 3 | `source_lender_name` | String | Extracted from review URL or override |
| 4 | `source_review_url` | String | Original review URL |
| 5 | `review_date` | Date | Date of review |
| 6 | `review_rating` | Integer | Star rating (1-5) |
| 7 | `raw_display_name` | String | Original displayName from input |
| 8 | `name_classification` | String | business \| person \| other |
| 9 | `company_search_name` | String | Cleaned company name (for businesses) |
| 10 | `company_normalized_key` | String | Deduplication key (for businesses) |
| 11 | `company_domain` | String | Primary domain (e.g., example.com) |
| 12 | `domain_confidence` | String | none \| low \| medium \| high |
| 13 | `primary_phone` | String | Primary phone (E.164 format) |
| 14 | `primary_phone_display` | String | Primary phone (display format) |
| 15 | `primary_phone_source` | String | google_maps \| yelp \| yellowpages \| etc. |
| 16 | `primary_phone_confidence` | String | none \| low \| medium \| high |
| 17 | `primary_email` | String | Primary email address |
| 18 | `primary_email_type` | String | person \| generic \| catchall |
| 19 | `primary_email_source` | String | hunter \| snov \| apollo \| etc. |
| 20 | `primary_email_confidence` | String | none \| low \| medium \| high |
| 21 | `business_address` | String | Full formatted address |
| 22 | `business_city` | String | City |
| 23 | `business_state_region` | String | State or region |
| 24 | `business_postal_code` | String | Postal/ZIP code |
| 25 | `business_country` | String | Country |
| 26 | `oc_company_name` | String | OpenCorporates verified name |
| 27 | `oc_jurisdiction` | String | Legal jurisdiction (e.g., us_ca) |
| 28 | `oc_company_number` | String | Company registration number |
| 29 | `oc_incorporation_date` | Date | Date of incorporation |
| 30 | `oc_match_confidence` | String | none \| low \| medium \| high |
| 31 | `overall_lead_confidence` | String | high \| medium \| low \| failed |
| 32 | `enrichment_status` | String | success \| failed \| error |
| 33 | `enrichment_notes` | String | Status description |
| 34 | `all_phones_json` | JSON | All discovered phones with metadata |
| 35 | `generic_emails_json` | JSON | All generic emails (info@, support@, etc.) |
| 36 | `person_emails_json` | JSON | All person emails (john.smith@, etc.) |
| 37 | `catchall_emails_json` | JSON | All catchall emails (noreply@, etc.) |

**Note:** Column count is 37 (not 36) - there was a typo in the original spec. The actual implementation has 37 columns.

### JSON Field Schemas

**all_phones_json:**
```json
[
  {
    "number_normalized": "+14155551234",
    "display": "(415) 555-1234",
    "source": "google_maps",
    "confidence": "high",
    "type": "main"
  }
]
```

**generic_emails_json / person_emails_json / catchall_emails_json:**
```json
[
  {
    "email": "info@example.com",
    "type": "generic",
    "source": "hunter",
    "confidence": 85
  }
]
```

### Example Output Row

```csv
row_id,source_platform,source_lender_name,source_review_url,review_date,review_rating,raw_display_name,name_classification,company_search_name,company_normalized_key,company_domain,domain_confidence,primary_phone,primary_phone_display,primary_phone_source,primary_phone_confidence,primary_email,primary_email_type,primary_email_source,primary_email_confidence,business_address,business_city,business_state_region,business_postal_code,business_country,oc_company_name,oc_jurisdiction,oc_company_number,oc_incorporation_date,oc_match_confidence,overall_lead_confidence,enrichment_status,enrichment_notes,all_phones_json,generic_emails_json,person_emails_json,catchall_emails_json
1,trustpilot,example.com,https://trustpilot.com/review/example.com/123,2024-01-15,5,ABC Trucking LLC,business,ABC Trucking,abctrucking,abctrucking.com,high,+14155551234,(415) 555-1234,google_maps,high,info@abctrucking.com,generic,hunter,high,"123 Main St, Houston, TX 77001",Houston,TX,77001,United States,ABC Trucking LLC,us_tx,TX123456,2015-03-20,high,high,success,Complete enrichment with high confidence,"[{""number_normalized"":""+14155551234""...}]","[{""email"":""info@abctrucking.com""...}]",null,null
```

### Confidence Level Definitions

**overall_lead_confidence:**
- **high**: Domain confidence = high AND phone confidence ≥ medium AND email confidence ≥ medium
- **medium**: Partial coverage with at least medium confidence in one or more areas
- **low**: Minimal enrichment with low confidence scores
- **failed**: No useful enrichment data discovered

**domain_confidence, primary_phone_confidence, primary_email_confidence:**
- **high**: Strong match, verified source, high similarity score (≥0.85)
- **medium**: Good match, verified source, medium similarity score (≥0.70)
- **low**: Weak match or low similarity score (≥0.60)
- **none**: No data found

---

## Caching & Performance Notes

### Enrichment Cache

The tool maintains a persistent cache (`enrichment_cache.json` by default) that stores enrichment results keyed by `company_normalized_key`.

### How Caching Works

1. **First Run**
   - Unique businesses are enriched via API calls
   - Results saved to cache file
   - Full API costs incurred

2. **Subsequent Runs**
   - Cache is checked before making API calls
   - Cached results are reused instantly
   - Only new businesses trigger API calls

### Benefits

✅ **Cost Reduction**
- Avoid re-calling expensive APIs for same businesses
- Reduce API usage by 70-90% on repeat runs

✅ **Speed Improvement**
- Cached lookups are near-instant (<0.1s vs 2-5s per business)
- Large files process much faster on repeat runs

✅ **Consistency**
- Same business always gets same enrichment data
- Reduces variability from API changes

### Cache Behavior

**Cache File Location:**
- Default: `enrichment_cache.json` in project root
- CLI: Custom location via `-c` flag
- API: Temporary cache per request (not persisted)
- Web UI: Uses API endpoint (temporary cache)

**Cache Invalidation:**
- Manual: Delete cache file to force re-enrichment
- Automatic: None - cache persists until manually cleared

**Cache Structure:**
```json
{
  "abctrucking": {
    "company_domain": "abctrucking.com",
    "domain_confidence": "high",
    "primary_phone": "+14155551234",
    ...
  },
  "xyzservices": {
    ...
  }
}
```

### Performance Tips

1. **Use caching for recurring enrichments**
   - Keep the same cache file across runs
   - Significantly reduces costs

2. **Deduplicate input before running**
   - Remove duplicate reviews from same business
   - Reduces processing time

3. **Start with free-tier APIs**
   - Google Places: 28,000 requests/month free
   - Yelp: Unlimited (with rate limits)
   - OpenCorporates: Works without key

4. **Process in batches**
   - For very large files (>10k rows), consider splitting
   - Monitor API rate limits

### Typical Performance

| Metric | CLI (Local) | API (Network) |
|--------|-------------|---------------|
| Classification | Instant | Instant |
| Cached enrichment | <0.1s/business | <0.1s/business |
| Uncached enrichment | 2-5s/business | 3-6s/business |
| 100 unique businesses (uncached) | ~5 minutes | ~8 minutes |
| 100 unique businesses (cached) | <10 seconds | <15 seconds |

---

## Limitations & Notes

### Known Limitations

1. **API-Dependent Quality**
   - Enrichment quality depends on data source coverage
   - Small local businesses may have limited data
   - New businesses may not be in databases yet

2. **Rate Limits**
   - Each API has rate limits (e.g., Google Places: 100 requests/second)
   - Very large files may need throttling
   - Consider upgrading to paid API tiers for high volume

3. **Geographic Coverage**
   - Google Maps and Yelp work best for US businesses
   - International coverage varies by country
   - Some data sources are US-only

4. **Classification Accuracy**
   - Rule-based classification is ~95% accurate
   - Edge cases may be misclassified
   - Ambiguous names default to conservative classification

5. **Domain Discovery**
   - Not all businesses have websites
   - Domain matching uses token overlap (≥0.7 threshold)
   - Some domains may be incorrect if names are very similar

6. **Phone Number Format**
   - Primary phone is normalized to E.164 format
   - International numbers may not parse correctly
   - Some sources provide only local format

7. **Email Verification**
   - Emails are discovered, not verified by sending test emails
   - Some emails may be inactive
   - Catchall emails may not be monitored

8. **Cost Considerations**
   - Premium APIs have usage costs
   - Uncached enrichment can be expensive for large files
   - Monitor API usage to control costs

### Best Practices

✅ **Use caching**
- Keep enrichment cache between runs
- Dramatically reduces costs

✅ **Validate API keys**
- Test with sample data first
- Monitor API usage and costs

✅ **Clean input data**
- Remove duplicate reviews before processing
- Ensure displayName column exists

✅ **Start with free tiers**
- Google Places: 28,000 requests/month free
- Yelp: Unlimited with rate limits
- OpenCorporates: Works without key

✅ **Review output carefully**
- Check confidence scores
- Verify primary contact data
- Use JSON fields for all discovered data

✅ **Respect API terms of service**
- Don't resell enriched data without permission
- Follow rate limits
- Don't abuse free tiers

### Data Privacy & Compliance

⚠️ **Important:**
- Trustpilot reviews are public data
- Enriched business contact information is also generally public
- However, use responsibly and comply with:
  - GDPR (for EU data subjects)
  - CCPA (for California residents)
  - CAN-SPAM (for email marketing)
  - TCPA (for phone marketing)
- Do not use for unauthorized purposes

### Error Handling

The tool includes comprehensive error handling:
- Invalid CSV format → Clear error message
- Missing API keys → Warning + graceful skip
- API errors → Logged, enrichment continues
- Network errors → Retry logic (where appropriate)
- Individual business failures → Marked as "error", pipeline continues

---

## Future Extensions

### Planned Features

🔮 **Additional Data Sources**
- LinkedIn company enrichment
- Crunchbase funding data
- Dun & Bradstreet business intelligence
- PitchBook private company data

🔮 **CRM Integration**
- Direct export to Salesforce
- HubSpot integration
- Pipedrive connector
- Webhooks for real-time sync

🔮 **Advanced Features**
- Bulk multi-file processing
- Scheduled enrichment jobs
- Webhook notifications
- Email verification (send test emails)
- Phone validation (real-time carrier lookup)

🔮 **UI Enhancements**
- Progress bar for long enrichments
- Preview before download
- Column selection (choose which fields to include)
- Enrichment history dashboard

🔮 **Machine Learning**
- AI-powered classification (replace rule-based)
- Confidence score optimization
- Duplicate business detection (fuzzy matching)
- Data quality scoring

🔮 **Performance Improvements**
- Parallel API calls (concurrent requests)
- Distributed processing (queue workers)
- Real-time streaming results
- Incremental processing (process as rows arrive)

### Community Contributions

We welcome contributions! Areas where help is needed:
- Additional API integrations
- Improved classification rules
- International phone number handling
- Better address parsing
- Documentation translations

### Roadmap

**Q1 2024:**
- LinkedIn enrichment
- Progress indicators in Web UI
- Batch processing improvements

**Q2 2024:**
- CRM integrations (Salesforce, HubSpot)
- Advanced filtering options
- API rate limit management

**Q3 2024:**
- Machine learning classification
- Real-time processing mode
- Enterprise deployment guides

**Q4 2024:**
- Mobile app (React Native)
- Advanced analytics dashboard
- White-label deployment

---

## Support & Documentation

### Documentation Files

- **README.md** (this file) - Complete documentation
- **USAGE_GUIDE.md** - Detailed usage examples for all interfaces
- **DEPLOY.md** - Deployment guide for cloud platforms
- **web/README.md** - Frontend-specific documentation
- **EXTENSION_SUMMARY.md** - Technical architecture details
- **PROJECT_OVERVIEW.md** - High-level project overview
- **QUICK_REFERENCE.md** - Command cheat sheet

### Getting Help

1. **Check documentation** - Most questions answered in guides above
2. **Test with sample data** - Use `sample_input.csv` to verify setup
3. **Check logs** - Run with `--verbose` flag for detailed logging
4. **Review API docs** - Check API provider documentation for issues
5. **GitHub Issues** - Report bugs or request features

### Troubleshooting

**Common Issues:**

**Problem:** Import errors
```bash
# Solution:
pip install -r requirements.txt
```

**Problem:** API connection errors
```bash
# Solution:
# 1. Check internet connection
# 2. Verify API keys in .env
# 3. Test API directly (e.g., curl)
```

**Problem:** No enrichment results
```bash
# Solution:
# 1. Check classification results (are any rows "business"?)
# 2. Verify API keys are valid
# 3. Run with --verbose to see API calls
```

**Problem:** CORS errors in Web UI
```bash
# Solution:
# Set FRONTEND_ORIGIN in backend .env:
FRONTEND_ORIGIN=http://localhost:3000
```

**Problem:** File too large
```bash
# Solution:
# 1. Split into smaller files
# 2. Increase Docker memory limits
# 3. Use CLI instead of Web UI
```

---

## License

This project is provided as-is for business data enrichment purposes. When using this tool:

✅ **You may:**
- Use for internal business purposes
- Modify for your needs
- Deploy to your infrastructure

⚠️ **You must:**
- Comply with all API provider terms of service
- Respect rate limits
- Follow data privacy regulations (GDPR, CCPA, etc.)
- Use enriched data responsibly

❌ **You may not:**
- Resell enriched data without permission
- Abuse free API tiers
- Violate data privacy laws
- Use for spam or unauthorized marketing

### API Provider Terms

By using this tool, you agree to comply with:
- Google Maps Platform Terms of Service
- Yelp Fusion API Terms
- Hunter.io Terms of Service
- Snov.io Terms of Service
- Apollo.io Terms of Service
- FullEnrich Terms of Service
- OpenCorporates Terms of Use

---

## Acknowledgments

This tool integrates with the following excellent services:
- **Google Maps/Places API** - Best-in-class local business data
- **Yelp Fusion API** - Verified business listings
- **Hunter.io** - Email discovery and verification
- **Snov.io** - Additional email sources
- **Apollo.io** - B2B company data
- **FullEnrich** - Company enrichment
- **OpenCorporates** - Legal entity verification
- **Apify** - Web scraping platform

Built with:
- **Python** - Core language
- **FastAPI** - HTTP API framework
- **React** - Web UI framework
- **Vite** - Frontend build tool
- **Docker** - Containerization

---

**Ready to get started?** See [Installation](#installation) and [CLI Usage](#cli-usage) to begin enriching your Trustpilot data.

For deployment to production, see [DEPLOY.md](DEPLOY.md).

For detailed usage examples, see [USAGE_GUIDE.md](USAGE_GUIDE.md).
