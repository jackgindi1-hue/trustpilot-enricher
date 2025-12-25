#!/bin/bash
# Simple API test script
# Make sure API server is running first: uvicorn api_server:app --host 0.0.0.0 --port 8000

API_URL="${API_URL:-http://localhost:8000}"

echo "Testing Trustpilot Enrichment API at $API_URL"
echo "================================================"
echo ""

echo "1. Testing health endpoint..."
HEALTH=$(curl -s "$API_URL/health")
echo "   Response: $HEALTH"
echo ""

echo "2. Testing root endpoint..."
ROOT=$(curl -s "$API_URL/")
echo "   Response: $ROOT"
echo ""

echo "3. Testing file enrichment..."
if [ -f "sample_input.csv" ]; then
    echo "   Uploading sample_input.csv..."
    curl -X POST "$API_URL/enrich" \
      -F "file=@sample_input.csv" \
      -F "lender_name_override=TestLender" \
      -o test_api_output.csv \
      -w "\n   HTTP Status: %{http_code}\n"

    if [ -f "test_api_output.csv" ]; then
        echo "   ✓ Output file created: test_api_output.csv"
        LINES=$(wc -l < test_api_output.csv)
        echo "   ✓ Output has $LINES lines"
    else
        echo "   ✗ Output file not created"
    fi
else
    echo "   ✗ sample_input.csv not found"
    echo "   Skipping enrichment test"
fi
echo ""

echo "4. Testing interactive docs..."
echo "   Open in browser:"
echo "   - Swagger UI: $API_URL/docs"
echo "   - ReDoc: $API_URL/redoc"
echo ""

echo "================================================"
echo "Test complete!"
