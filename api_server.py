"""
FastAPI server for Trustpilot enrichment API
Provides HTTP endpoint for CSV upload and enrichment
"""

import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from tp_enrich.pipeline import run_pipeline
from tp_enrich.logging_utils import setup_logger

# Load environment variables
load_dotenv()

logger = setup_logger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Trustpilot Enrichment API",
    description="Enrich Trustpilot review CSV with business contact information",
    version="1.0.0"
)

# Configure CORS - Allow frontend origin
allowed_origins = [
    'https://same-ds94u6p1ays-latest.netlify.app',
    'http://localhost:3000',
    'http://localhost:5173',
]

# Add custom origin from env var if set
frontend_origin = os.getenv('FRONTEND_ORIGIN')
if frontend_origin and frontend_origin not in allowed_origins:
    allowed_origins.append(frontend_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """
    Health check endpoint
    Returns: JSON with status
    """
    return {"status": "ok"}


@app.post("/enrich")
async def enrich_csv(
    file: UploadFile = File(..., description="Trustpilot CSV file to enrich"),
    lender_name_override: Optional[str] = Form(None, description="Optional: Override source_lender_name for all rows")
):
    """
    Enrich a Trustpilot CSV file

    Args:
        file: CSV file upload
        lender_name_override: Optional override for lender/source name

    Returns:
        Enriched CSV file download
    """
    # Validate file type
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV file")

    logger.info(f"Received enrichment request for file: {file.filename}")

    # Create temp directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)

        # Save uploaded file
        input_path = temp_dir_path / "input.csv"
        output_path = temp_dir_path / "enriched.csv"
        cache_path = temp_dir_path / "cache.json"

        try:
            # Write uploaded file to disk
            with open(input_path, 'wb') as f:
                contents = await file.read()
                f.write(contents)

            logger.info(f"Saved input file: {input_path}")

            # Prepare config
            config = {}
            if lender_name_override:
                config['lender_name_override'] = lender_name_override
                logger.info(f"Using lender name override: {lender_name_override}")

            # Run enrichment pipeline
            logger.info("Starting enrichment pipeline...")
            stats = run_pipeline(
                str(input_path),
                str(output_path),
                str(cache_path),
                config=config
            )

            logger.info(f"Enrichment complete: {stats}")

            # Check if output file was created
            if not output_path.exists():
                raise HTTPException(status_code=500, detail="Enrichment failed to produce output file")

            # Read enriched CSV into memory BEFORE temp directory cleanup
            logger.info(f"Reading enriched CSV from: {output_path}")
            with open(output_path, 'rb') as f:
                csv_bytes = f.read()

            logger.info(f"Returning enriched CSV ({len(csv_bytes)} bytes)")

            # Return enriched CSV as streaming response from memory
            return StreamingResponse(
                iter([csv_bytes]),
                media_type='text/csv',
                headers={
                    'Content-Disposition': 'attachment; filename="enriched.csv"'
                }
            )

        except Exception as e:
            logger.error(f"Enrichment error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Enrichment failed: {str(e)}")


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "service": "Trustpilot Enrichment API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "enrich": "/enrich (POST)"
        },
        "docs": "/docs"
    }


if __name__ == "__main__":
    import uvicorn

    # Run server
    # For production, use: uvicorn api_server:app --host 0.0.0.0 --port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)
