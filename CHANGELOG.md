# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed - 2025-12-17

#### Phase 2 Hotfix v2 - HUNTER KEY + YP CATEGORY PAGES + DATA EXTRACTION

**Issues Fixed (from 05:36 logs):**
1. Hunter: "missing key" error even though HUNTER_KEY exists in Railway
2. YellowPages: Getting category page URLs (useless) instead of business listing URLs
3. Phase 2: Need DATA extraction (phone/email/website/names) not just URLs

**Solutions:**
- **Hunter Key Detection**: Centralized `get_hunter_key()` to support both `HUNTER_KEY` (Railway) and `HUNTER_API_KEY` (fallback)
- **YellowPages URL Validation**: Only accept business listing URLs containing `/mip/` or `/biz/` (reject category pages)
- **Data Extraction**: Extract actual contact data from BBB and YellowPages HTML (phones, emails, websites, names)

**New CSV Output Fields:**
- `phase2_bbb_phone`, `phase2_bbb_email`, `phase2_bbb_website`, `phase2_bbb_names`
- `phase2_yp_phone`, `phase2_yp_email`, `phase2_yp_website`, `phase2_yp_names`

**Files Changed:**
- `tp_enrich/phase2_enrichment.py` - Added v2 functions with improved validation and data extraction
- `tp_enrich/pipeline.py` - Updated to use `apply_phase2_data_enrichment()` function
- `tp_enrich/io_utils.py` - Added new website and names fields to output schema

**Improvements:**
- BBB profiles now validated to ensure they're actual business profiles (not search/category pages)
- YellowPages now only accepts business listing URLs (category pages like `/roofing-contractors` are rejected)
- Extract websites from BBB and YellowPages (filtered to exclude bbb.org, google.com, yellowpages.com)
- Extract contact names from BBB with improved filtering to avoid junk like "Business Profile"

---

### Added - 2025-12-17

#### Phase 2 Contact Data Patch - ACTUAL DATA EXTRACTION

**Goal:** Transform Phase 2 from URL-only outputs to actual contact data extraction.

**New Features:**
- BBB contact data extraction: phones, emails, contact/owner names
- YellowPages snippet parsing: phones, emails, names (no HTML fetch to avoid bot blocks)
- OpenCorporates verification: company match yes/no

**New CSV Output Fields:**
- `phase2_bbb_phone` - Phone number from BBB profile
- `phase2_bbb_email` - Email address from BBB profile
- `phase2_bbb_contact_name` - Contact/owner name from BBB profile
- `phase2_yp_phone` - Phone number from YellowPages snippet
- `phase2_yp_email` - Email address from YellowPages snippet
- `phase2_yp_contact_name` - Contact name from YellowPages snippet
- `phase2_oc_match` - Boolean: company found in OpenCorporates
- `phase2_oc_company_url` - OpenCorporates company URL

**Files Changed:**
- `tp_enrich/phase2_enrichment.py` - Added `apply_phase2_contact_boost_DATA()` function
- `tp_enrich/pipeline.py` - Integrated Phase 2 contact data extraction
- `tp_enrich/io_utils.py` - Added new output schema fields

**Performance:**
- BBB: ~3-5 seconds (SerpApi + HTML fetch + extraction)
- YP: ~2-3 seconds (SerpApi snippet parsing only)
- OC: ~2 seconds (SerpApi verification)
- Total: ~7-10 seconds per business (only when Phase 2 triggered)

**Credits:** 3 SerpApi credits per business with Phase 2 triggered (BBB + YP + OC searches)

**Documentation:** See `.same/PHASE2_CONTACT_DATA_PATCH.md` for full details.

---

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
