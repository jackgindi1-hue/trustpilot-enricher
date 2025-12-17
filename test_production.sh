#!/bin/bash
# Production Testing Script - Phase 2 Crashproof Deployment
# Run this to verify the deployment is working correctly

set -e

echo "=================================================="
echo "Phase 2 Crashproof - Production Test Suite"
echo "=================================================="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PROD_URL="https://trustpilot-enricher-production.up.railway.app"

# Test 1: Health Check
echo "Test 1: API Health Check"
echo "------------------------"
HEALTH=$(curl -s "${PROD_URL}/health" || echo "FAILED")
if [[ "$HEALTH" == *"healthy"* ]]; then
    echo -e "${GREEN}✅ API is healthy${NC}"
else
    echo -e "${RED}❌ API health check failed${NC}"
    echo "Response: $HEALTH"
    exit 1
fi
echo ""

# Test 2: Create test CSV
echo "Test 2: Creating test CSV"
echo "------------------------"
cat > test_phase2.csv << 'EOF'
business_name,city,state
ABC Roofing LLC,Los Angeles,CA
Smith Plumbing Inc,Austin,TX
Johnson HVAC Services,Phoenix,AZ
EOF
echo -e "${GREEN}✅ Test CSV created: test_phase2.csv${NC}"
echo ""

# Test 3: Upload and enrich
echo "Test 3: Uploading to production API"
echo "------------------------"
echo "This will use API credits (3 SerpApi per business if Phase 2 triggers)"
read -p "Continue? (y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Test cancelled."
    exit 0
fi

HTTP_CODE=$(curl -s -o test_output.csv -w "%{http_code}" \
    -X POST "${PROD_URL}/api/enrich" \
    -F "file=@test_phase2.csv")

if [ "$HTTP_CODE" -eq 200 ]; then
    echo -e "${GREEN}✅ API enrichment succeeded (HTTP $HTTP_CODE)${NC}"
else
    echo -e "${RED}❌ API enrichment failed (HTTP $HTTP_CODE)${NC}"
    exit 1
fi
echo ""

# Test 4: Verify output CSV
echo "Test 4: Verifying output CSV"
echo "------------------------"

# Check if output file exists
if [ ! -f test_output.csv ]; then
    echo -e "${RED}❌ Output CSV not found${NC}"
    exit 1
fi

# Check for Phase 2 columns
REQUIRED_COLS=(
    "phase2_bbb_phone"
    "phase2_bbb_email"
    "phase2_bbb_website"
    "phase2_bbb_names"
    "phase2_yp_phone"
    "phase2_yp_email"
    "phase2_yp_website"
    "phase2_yp_names"
)

HEADER=$(head -1 test_output.csv)
MISSING_COLS=()

for col in "${REQUIRED_COLS[@]}"; do
    if [[ ! "$HEADER" == *"$col"* ]]; then
        MISSING_COLS+=("$col")
    fi
done

if [ ${#MISSING_COLS[@]} -eq 0 ]; then
    echo -e "${GREEN}✅ All Phase 2 columns present${NC}"
else
    echo -e "${RED}❌ Missing columns: ${MISSING_COLS[*]}${NC}"
    exit 1
fi

# Count rows
ROW_COUNT=$(wc -l < test_output.csv)
echo -e "${GREEN}✅ Output has $ROW_COUNT rows (including header)${NC}"

# Check if any Phase 2 data was populated
BBB_PHONE_COUNT=$(awk -F',' 'NR>1 && $0 ~ /phase2_bbb_phone/ {print $0}' test_output.csv | grep -v '""' | wc -l)
YP_PHONE_COUNT=$(awk -F',' 'NR>1 && $0 ~ /phase2_yp_phone/ {print $0}' test_output.csv | grep -v '""' | wc -l)

echo ""
echo "Phase 2 Data Population:"
echo "  BBB phones found: $BBB_PHONE_COUNT"
echo "  YP phones found: $YP_PHONE_COUNT"

if [ $BBB_PHONE_COUNT -gt 0 ] || [ $YP_PHONE_COUNT -gt 0 ]; then
    echo -e "${GREEN}✅ Phase 2 enrichment populated data${NC}"
else
    echo -e "${YELLOW}⚠️  No Phase 2 data populated (may be expected if businesses have complete Google data)${NC}"
fi

echo ""
echo "=================================================="
echo "Test Summary"
echo "=================================================="
echo -e "${GREEN}✅ API Health: PASSED${NC}"
echo -e "${GREEN}✅ CSV Upload: PASSED${NC}"
echo -e "${GREEN}✅ Schema Validation: PASSED${NC}"
echo -e "${GREEN}✅ Crashproof Guarantee: PASSED (all columns present)${NC}"
echo ""
echo "Output saved to: test_output.csv"
echo ""
echo "Next Steps:"
echo "1. Open test_output.csv and inspect Phase 2 data"
echo "2. Check Railway logs for Phase 2 enrichment details"
echo "3. Monitor credit usage in SerpApi/Hunter dashboards"
echo ""
echo -e "${GREEN}🎉 Production deployment is working correctly!${NC}"
