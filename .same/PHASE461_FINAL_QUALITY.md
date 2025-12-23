# PHASE 4.6.1 — FINAL QUALITY PATCH

**Status:** ✅ INTEGRATED
**Date:** December 23, 2025

## Overview

This patch fixes four critical quality issues in the canonical entity matching and email enrichment pipeline:

1. **Candidate name None bug** - Google/Yelp candidates sometimes missing name field
2. **Smart threshold override** - Accept >=0.80 normally, >=0.75 ONLY if phone OR domain matches
3. **Directory email preservation** - Keep directory/aggregator emails as secondary, not primary
4. **Dtype warning fix** - Prevent pandas dtype warning for company_search_name assignment

## Changes Made

### 1. New Utility Modules

**`tp_enrich/normalize.py`** (already created)
- Shared normalization utilities for entity matching
- `normalize_company_name()` function strips punctuation, corp suffixes, and normalizes whitespace
- Used by candidate builders and entity matching

**`tp_enrich/candidates.py`** (already created)
- `build_google_candidate()` - builds Google Places candidate with guaranteed name field
- `build_yelp_candidate()` - builds Yelp candidate with guaranteed name field
- Both functions include robust fallback logic to ensure name is never None
- Includes domain normalization helper

### 2. Updated Entity Matching

**`tp_enrich/entity_match.py`**
- Updated `pick_best()` function with smart threshold logic:
  - **Hard gate:** Accept if score >= 0.80
  - **Soft gate:** Accept if score >= 0.75 AND (phone_match == 1.0 OR domain_match == 1.0)
- Added `soft_threshold` parameter (default 0.75)
- Enhanced reason strings for better debugging

### 3. Updated Canonical Selection

**`tp_enrich/canonical.py`**
- Imports new candidate builders: `build_google_candidate`, `build_yelp_candidate`
- Updated `choose_canonical_business()` to use new builders instead of manual dict construction
- Fixes "name=None" bug by ensuring all candidates have valid names
- Passes both `threshold=0.80` and `soft_threshold=0.75` to `pick_best()`

### 4. Directory Email Preservation

**`tp_enrich/email_enrichment.py`**
- Added `DIRECTORY_EMAIL_DOMAINS` set with known aggregator domains:
  - chamberofcommerce.com, thebluebook.com, buzzfile.com, brokersnapshot.com
  - zoominfo.com, opencorporates.com, yelp.com, facebookmail.com
- Added `_email_domain()` helper to extract domain from email
- Added `_append_secondary_email()` to append emails without duplicates
- Added `assign_email()` function with smart logic:
  - Directory emails -> always secondary
  - Other emails -> primary if empty, otherwise secondary
  - Tracks sources in debug_notes

### 5. Dtype Warning Fix

**`tp_enrich/pipeline.py`**
- Line ~860: Convert `company_search_name` column to string dtype before assignment
- Prevents pandas SettingWithCopyWarning and dtype inconsistencies

## Integration Status

All components are integrated:
- ✅ Utility modules created (`normalize.py`, `candidates.py`)
- ✅ Entity matching updated with smart threshold
- ✅ Canonical selection using new candidate builders
- ✅ Directory email preservation logic added
- ✅ Dtype warning fixed in pipeline

## Testing Notes

### Expected Behavior

1. **Entity Matching:**
   - Scores 0.80+ should be accepted immediately
   - Scores 0.75-0.79 should be accepted ONLY if phone or domain matches exactly
   - Scores below 0.75 should always be rejected
   - Logs should show reason: "accepted_threshold_0.80" or "accepted_soft_0.75_strong_anchor"

2. **Candidate Names:**
   - Google/Yelp candidates should never have None for name field
   - Fallback to row business_name or company_search_name if provider name missing
   - Debug logs should show "Google Places: name=..." with actual name string

3. **Directory Emails:**
   - Emails from directory domains should appear in `secondary_email` field
   - Non-directory emails should be `primary_email` if none exists yet
   - `debug_notes` should track email sources

4. **Dtype Warning:**
   - No pandas warnings about setting values on dtype='object' when assigning company_search_name

## Next Steps

1. Commit all changes to git
2. Push to GitHub (triggers Railway auto-deploy)
3. Monitor Railway logs for entity matching behavior
4. Test with sample CSV to verify:
   - No "name=None" errors in logs
   - Smart threshold accepting 0.75-0.79 scores with phone/domain match
   - Directory emails preserved as secondary
   - No dtype warnings in pipeline

## Files Modified

- `tp_enrich/normalize.py` (new)
- `tp_enrich/candidates.py` (new)
- `tp_enrich/entity_match.py` (updated `pick_best`)
- `tp_enrich/canonical.py` (updated to use new builders)
- `tp_enrich/email_enrichment.py` (added directory preservation)
- `tp_enrich/pipeline.py` (fixed dtype warning)
- `.same/PHASE461_FINAL_QUALITY.md` (this file)

## Git Commit Message

```
Phase 4.6.1 final quality patch

- Fix candidate name None bug with new builders
- Smart threshold: 0.75-0.79 accepted with phone/domain match
- Preserve directory emails as secondary
- Fix company_search_name dtype warning

🤖 Generated with Same (https://same.new)

Co-Authored-By: Same <noreply@same.new>
```
