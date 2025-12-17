# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed - 2025-12-17

#### Phase 2 Enrichment Fixes (v3)

**1. Yelp API 400 Errors - RESOLVED**
- Fixed: Yelp API calls were missing required location parameters
- Solution: Implemented `yelp_phone_lookup_safe()` wrapper that guarantees either location string OR lat/lon coordinates
- Impact: No more 400 Bad Request errors from Yelp API
- Files changed: `tp_enrich/phone_enrichment.py`, `tp_enrich/phase2_enrichment.py`

**2. YellowPages Link Extraction - RESOLVED**
- Fixed: SerpAPI results were not being parsed correctly, leading to `link=None` for valid businesses
- Solution: Implemented robust `_pick_best_link_any()` function that searches multiple result types
- Impact: Successful link extraction for businesses with YellowPages listings
- Files changed: `tp_enrich/phase2_enrichment.py`

**3. OpenCorporates Link Extraction - RESOLVED**
- Fixed: SerpAPI results were not being parsed correctly, leading to `link=None` for registered companies
- Solution: Applied same robust link extraction logic with forgiving domain matching
- Impact: Successful link extraction for registered businesses
- Files changed: `tp_enrich/phase2_enrichment.py`

**Environment Variable Updates**
- Added: `SERP_API_KEY` documentation to `.env.example`
- Clarified: Required API keys for Phase 2 fallback enrichment

**Documentation**
- Added: `.same/PHASE2_FIX_APPLIED.md` - Detailed fix documentation
- Added: `.same/PHASE2_TESTING_GUIDE.md` - Testing instructions
- Added: `.same/PHASE2_FIX_SUMMARY.md` - Quick reference guide

### Technical Details

**Old Behavior:**
```python
# Yelp call without guaranteed location
yelp_search_phone(term=name, city=city, state=state, address=address)
# Result: 400 errors when location fields were empty/incomplete
```

**New Behavior:**
```python
# Safe wrapper guarantees valid parameters
yelp_phone_lookup_safe(business_name=name, google_payload=google_hit, logger=logger)
# Result: Always provides either location string OR lat/lon
```

**Link Extraction Improvements:**
```python
# Old: Only checked organic_results[0]['link']
# New: Searches organic_results, inline_results, top_stories, related_results, search_metadata
```

### Testing

Run the test suite to verify fixes:
```bash
python main.py --input sample_input.csv --output test_output.csv
```

Expected log output:
- ✅ `Yelp FIX400 attempted=True notes=ok`
- ✅ `PHASE2 YP: link=https://www.yellowpages.com/...`
- ✅ `PHASE2 OC: link=https://opencorporates.com/companies/...`
- ❌ No more `status=400` errors
- ❌ No more `link=None` for valid businesses

### Migration Notes

No breaking changes. Existing code will continue to work.

**Recommended actions:**
1. Update `.env` with `SERP_API_KEY` if using Phase 2 enrichment
2. Restart any running API servers to pick up the changes
3. Monitor logs for improved success rates

### Dependencies

No new dependencies required. Uses existing:
- `requests` - HTTP client
- Python 3.8+ - Standard library

---

## Previous Versions

See git commit history for earlier changes.
