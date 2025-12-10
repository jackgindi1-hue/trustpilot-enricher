# Deployment Guide

This guide explains how to deploy the Trustpilot Enrichment API using Docker.

## Docker Deployment

### Build the Docker Image

```bash
docker build -t trustpilot-enricher .
```

### Run Locally with Docker

```bash
docker run --env-file .env -p 8000:8000 trustpilot-enricher
```

The API will be available at `http://localhost:8000`

### Required Environment Variables

The container needs these API keys as environment variables. You can provide them via:
- `--env-file .env` (recommended for local)
- Individual `-e` flags
- Your deployment platform's secrets management

#### Required Environment Variables:

```bash
# Google Maps / Places API (TOP PRIORITY for SMB phone numbers)
GOOGLE_PLACES_API_KEY=your_key_here

# Yelp Fusion API
YELP_API_KEY=your_key_here

# OpenCorporates API (optional - works without key but with rate limits)
OPENCORPORATES_API_KEY=your_key_here

# Hunter.io Email Finder
HUNTER_API_KEY=your_key_here

# Snov.io Email Finder (format: user_id:api_key)
SNOV_API_KEY=user_id:api_key

# Apollo.io Company Data
APOLLO_API_KEY=your_key_here

# FullEnrich Company Data
FULLENRICH_API_KEY=your_key_here

# Optional: CORS configuration
FRONTEND_ORIGIN=http://localhost:3000,https://yourdomain.com
```

### Test the Deployment

```bash
# Health check
curl http://localhost:8000/health

# API info
curl http://localhost:8000/

# Interactive docs
open http://localhost:8000/docs
```

## Deploying to Cloud Platforms

### Railway

1. Push code to GitHub repository
2. Create new project on Railway.app
3. Connect your GitHub repository
4. Add environment variables in Railway dashboard
5. Railway will auto-detect the Dockerfile and deploy

### Render

1. Create `render.yaml` in project root (optional):
```yaml
services:
  - type: web
    name: trustpilot-enricher
    env: docker
    plan: starter
    envVars:
      - key: GOOGLE_PLACES_API_KEY
        sync: false
      - key: YELP_API_KEY
        sync: false
```

2. Connect GitHub repository
3. Add environment variables in Render dashboard
4. Deploy

### Fly.io

1. Install Fly CLI: `https://fly.io/docs/getting-started/installing-flyctl/`
2. Login: `fly auth login`
3. Launch app: `fly launch`
4. Set secrets:
```bash
fly secrets set GOOGLE_PLACES_API_KEY=your_key
fly secrets set YELP_API_KEY=your_key
# ... add all other keys
```
5. Deploy: `fly deploy`

### Google Cloud Run

1. Build and push to Google Container Registry:
```bash
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/trustpilot-enricher
```

2. Deploy to Cloud Run:
```bash
gcloud run deploy trustpilot-enricher \
  --image gcr.io/YOUR_PROJECT_ID/trustpilot-enricher \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GOOGLE_PLACES_API_KEY=your_key,YELP_API_KEY=your_key
```

### AWS ECS / Fargate

1. Push to Amazon ECR:
```bash
aws ecr create-repository --repository-name trustpilot-enricher
docker tag trustpilot-enricher:latest YOUR_ACCOUNT.dkr.ecr.REGION.amazonaws.com/trustpilot-enricher:latest
docker push YOUR_ACCOUNT.dkr.ecr.REGION.amazonaws.com/trustpilot-enricher:latest
```

2. Create ECS task definition with environment variables
3. Create ECS service

## API Usage

### Health Check

```bash
curl http://your-api-url/health
```

Response:
```json
{
  "status": "ok"
}
```

### Enrich CSV

```bash
curl -X POST http://your-api-url/enrich \
  -F "file=@trustpilot_reviews.csv" \
  -F "lender_name_override=MyLender" \
  -o enriched.csv
```

Parameters:
- `file` (required): CSV file to enrich
- `lender_name_override` (optional): Override source_lender_name for all rows

### Interactive API Documentation

FastAPI provides automatic interactive docs:
- Swagger UI: `http://your-api-url/docs`
- ReDoc: `http://your-api-url/redoc`

## Security Considerations

1. **Never commit API keys** to Git
2. Use environment variables or secrets management
3. For production:
   - Enable HTTPS (most platforms do this automatically)
   - Restrict CORS origins via `FRONTEND_ORIGIN` env var
   - Consider API rate limiting
   - Monitor API usage and costs

## Monitoring

### Logs

View logs in your deployment platform:
- Railway: Dashboard → Logs tab
- Render: Dashboard → Logs
- Fly.io: `fly logs`
- Cloud Run: Cloud Console → Logs
- Docker: `docker logs <container_id>`

### Metrics

The API logs:
- Request received
- File processing start/end
- Enrichment statistics
- Errors

## Troubleshooting

### Container won't start

Check logs for missing environment variables:
```bash
docker logs <container_id>
```

### API returns 500 errors

1. Check logs for specific error messages
2. Verify API keys are correctly set
3. Ensure input CSV is in correct format
4. Test with sample_input.csv first

### CORS errors in browser

Set `FRONTEND_ORIGIN` environment variable:
```bash
FRONTEND_ORIGIN=https://your-frontend-domain.com
```

### Out of memory

For large CSVs, increase container memory:
- Railway: Adjust in settings
- Render: Upgrade plan
- Fly.io: `fly scale memory 2048`
- Cloud Run: `--memory 2Gi` flag

## Scaling

### Vertical Scaling (More Resources)

Increase CPU/Memory for processing larger files:
- Railway: Auto-scales
- Render: Upgrade plan
- Fly.io: `fly scale vm`
- Cloud Run: Adjust memory/CPU limits

### Horizontal Scaling (More Instances)

Most platforms auto-scale based on load:
- Railway: Automatic
- Render: Automatic on paid plans
- Fly.io: `fly scale count 3`
- Cloud Run: Automatic based on requests

## Cost Optimization

1. **Use caching**: The enrichment cache reduces API calls
2. **Set timeouts**: Add request timeouts to prevent hanging
3. **Monitor API usage**: Track external API calls (Google, Yelp, etc.)
4. **Scale down when idle**: Use autoscaling to reduce costs

## Local Development

### Run API Server Locally (Without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your API keys

# Run server
uvicorn api_server:app --reload --host 0.0.0.0 --port 8000
```

### CLI Still Works

The CLI remains fully functional:
```bash
python main.py input.csv -o output.csv
```

## Next Steps

1. Deploy backend API to your preferred platform
2. Note the API URL (e.g., `https://your-app.railway.app`)
3. Deploy the web frontend (see `web/README.md`)
4. Configure frontend to point to your API URL
5. Test end-to-end workflow

---

For web UI deployment, see `web/README.md` after it's created.
