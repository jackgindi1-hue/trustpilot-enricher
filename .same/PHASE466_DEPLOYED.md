# ✅ PHASE 4.6.6 — ADDRESS-TRIGGERED DIRECTORY RETRIES

**Date:** December 25, 2025  
**Status:** 🟢 **DEPLOYED TO PRODUCTION**

---

## 🎯 What Was Added

### Smart Directory Retry Logic

**Goal:** Re-run BBB and YellowPages when address is discovered but contact info is still incomplete

**Problem Solved:**
- Address discovered after initial BBB/YP run
- Contact info (phone/email) still missing
- Directory lookups would benefit from address context
- Need to avoid infinite retry loops

**Solution:**
- Retry BBB + YP exactly ONCE when conditions are met
- Use discovered_address for better directory matching
- Prevent loops with address_retry_ran flag
- Only retry if contact info is incomplete

---

## 🔧 Implementation Details

### Helper Functions Added

**1. `_all_blank(*vals) -> bool`**
- Checks if all provided values are blank/empty
- Returns True only if ALL are blank
- Used to check BBB/YP output fields

**2. `_needs_contact(row: dict) -> bool`**
- Returns True if missing phone OR email
- Logic: `(not has_phone) or (not has_email)`
- Ensures we only retry when contact info needed

**3. `_bbbyp_outputs_empty(row: dict) -> bool`**
- Checks if all BBB/YP output fields are empty
- Fields checked:
  - phase2_bbb_phone, phase2_bbb_email, phase2_bbb_website
  - phase2_yp_phone, phase2_yp_email, phase2_yp_website
- Returns True if directories not yet exploited

**4. `_should_retry_directories_post_address(row: dict) -> bool`**
- Decision logic with 4 conditions (ALL must be true):
  1. discovered_address exists
  2. BBB/YP outputs are empty
  3. Still need contact info
  4. Haven't already retried (address_retry_ran flag)

**5. `_run_post_address_directory_retries(row: dict, logger)`**
- Executes BBB + YP retries
- Sets address_retry_ran flag FIRST (prevents loops)
- Logs ADDRESS_RETRY_SENTINEL
- Safe try/except around each directory call

---

## 📊 Decision Flow

### When Does Retry Happen?

```
1. Is discovered_address present? ✓
   ↓ YES
2. Are BBB/YP outputs empty? ✓
   ↓ YES
3. Missing phone OR email? ✓
   ↓ YES
4. address_retry_ran = False? ✓
   ↓ YES
 RETRY BBB + YP with address
```

### When Does Retry NOT Happen?

**Scenario 1: No address**
```
discovered_address = ""
 Skip retry (nothing new to search with)
```

**Scenario 2: Already have contact info**
```
primary_phone = "(555) 123-4567"
primary_email = "info@example.com"
 Skip retry (don't need more contact info)
```

**Scenario 3: BBB/YP already ran**
```
phase2_bbb_phone = "(555) 987-6543"
 Skip retry (directories already exploited)
```

**Scenario 4: Already retried**
```
address_retry_ran = True
 Skip retry (prevent infinite loop)
```

---

## 🔍 Sentinel Logging

### ADDRESS_RETRY_SENTINEL

**Purpose:** Track when address-triggered retries run

**Log Example:**
```
INFO: ADDRESS_RETRY_SENTINEL row_id=123 name=ABC Restaurant addr=123 Main St, City, CA
```

**Verification:**
```bash
grep "ADDRESS_RETRY_SENTINEL" railway.log | wc -l
# Count how many rows benefited from address retry
```

### Error Logging

**BBB_RETRY_ERROR:**
```
WARNING: BBB_RETRY_ERROR row_id=123 err=ConnectionTimeout
```

**YP_RETRY_ERROR:**
```
WARNING: YP_RETRY_ERROR row_id=123 err=RateLimitExceeded
```

---

## 📁 Files Modified

| File | Changes | Purpose |
|------|---------|---------|
| `tp_enrich/adaptive_enrich.py` | +96 lines | Directory retry logic |

**Commit:** `da769cf`

**Total:** 96 insertions, 1 deletion

---

## 📈 Expected Impact

### Retry Rate

| Metric | Expected |
|--------|----------|
| **Rows with discovered_address** | 40-60% |
| **Rows needing contact** | 20-40% |
| **Retry executions** | 10-20% |
| **Better contact from retry** | +5-10% |

### Contact Coverage Improvement

**Before Phase 4.6.6:**
```
1. Initial BBB/YP run (no address)
2. Anchor discovery finds address
3. BBB/YP not re-run ❌
4. Contact info incomplete
```

**After Phase 4.6.6:**
```
1. Initial BBB/YP run (no address)
2. Anchor discovery finds address
3. BBB/YP retried with address ✅
4. Better contact coverage
```

**Expected Improvement:** +5-10% phone/email coverage

---

## 🧪 Verification

### Check Logs for Retries

```bash
# How many retries ran?
grep "ADDRESS_RETRY_SENTINEL" railway.log | wc -l

# Which rows retried?
grep "ADDRESS_RETRY_SENTINEL" railway.log | head -10

# Any errors during retry?
grep "BBB_RETRY_ERROR\|YP_RETRY_ERROR" railway.log
```

### Analyze CSV Output

```python
import pandas as pd
df = pd.read_csv("enriched.csv")

# Retry rate
if 'address_retry_ran' in df.columns:
    retry_count = df['address_retry_ran'].sum()
    print(f"Address retries: {retry_count} / {len(df)}")

# Success rate
retried = df[df['address_retry_ran'] == True]
has_bbb_data = retried['phase2_bbb_phone'].notna().sum()
has_yp_data = retried['phase2_yp_phone'].notna().sum()
print(f"BBB success after retry: {has_bbb_data} / {len(retried)}")
print(f"YP success after retry: {has_yp_data} / {len(retried)}")
```

### Expected CSV Columns

**New Column:**
- `address_retry_ran` (boolean) - Flag indicating retry was executed

**Populated After Retry:**
- `phase2_bbb_phone`, `phase2_bbb_email`, `phase2_bbb_website`
- `phase2_yp_phone`, `phase2_yp_email`, `phase2_yp_website`

---

## ✅ Safety Guarantees

**LOOP PREVENTION:**
- ✅ address_retry_ran flag set BEFORE execution
- ✅ Only retries if flag is False
- ✅ Impossible to retry twice

**SMART CONDITIONS:**
- ✅ Only retries when address discovered
- ✅ Only retries when contact info needed
- ✅ Only retries when directories not yet exploited
- ✅ All 4 conditions must be met

**ERROR HANDLING:**
- ✅ Safe try/except around BBB call
- ✅ Safe try/except around YP call
- ✅ Errors logged but don't stop enrichment
- ✅ Row returned even if retry fails

**NO BREAKING CHANGES:**
- ✅ Existing enrichment logic unchanged
- ✅ All previous phases preserved
- ✅ Surgical addition only

---

## 🔄 Integration Points

### Works With Anchor Discovery

**If anchor discovery finds address:**
- Address stored in discovered_address
- Retry conditions checked
- BBB/YP retried with address context

**If no address discovered:**
- Retry skipped (condition 1 fails)
- No wasted API calls

### Compatible With All Phases

 Phase 4.6.5.7: SERP-first + metrics  
 Phase 4.6.5.6: Business name promotion  
 Phase 4.6.5 HOTFIX: Multi-strategy Google  
 Phase 4.7.0: Atomic job writes  
 Phase 4.7.1: UI stuck running fix  

**No conflicts or regressions**

---

## 📊 Use Cases

### Use Case 1: Restaurant with Street Address

**Initial State:**
- company_search_name: "ABC Restaurant"
- discovered_address: "" (empty)
- primary_phone: "" (empty)

**Initial BBB/YP:**
- Runs with name only
- No results (common name, no address)

**After Anchor Discovery:**
- discovered_address: "123 Main St, Springfield, CA"
- Retry conditions met ✓

**Retry Execution:**
- BBB/YP run with "ABC Restaurant + 123 Main St, Springfield, CA"
- Better matching → phone found ✓

### Use Case 2: Business with Existing Contact

**Initial State:**
- primary_phone: "(555) 123-4567" ✓
- primary_email: "info@abc.com" ✓

**After Anchor Discovery:**
- discovered_address: "456 Oak Ave"

**Retry Decision:**
- Condition 3 fails: has phone AND email
- Skip retry (don't need more contact info)

### Use Case 3: Already Exploited Directories

**Initial State:**
- phase2_bbb_phone: "(555) 987-6543" (already found)

**After Anchor Discovery:**
- discovered_address: "789 Elm St"

**Retry Decision:**
- Condition 2 fails: BBB outputs not empty
- Skip retry (already exploited directories)

---

## 🚀 Next Steps

### Analysis Opportunities

1. **Measure Retry Effectiveness:**
   - Compare contact coverage: address_retry_ran=true vs false
   - Identify which directory (BBB vs YP) benefits most

2. **Optimize Retry Logic:**
   - Consider retrying only BBB or only YP based on vertical
   - Tune retry conditions based on success rates

3. **Quality Validation:**
   - Verify discovered_address quality
   - Validate phone/email from retry matches business

---

## 🔍 Troubleshooting

### Retry Not Running

**Check Conditions:**
```python
# Debug why retry didn't run
row = df.iloc[0]  # example row

print(f"Condition 1 (has address): {bool(row.get('discovered_address'))}")
print(f"Condition 2 (BBB/YP empty): {_bbbyp_outputs_empty(row)}")
print(f"Condition 3 (needs contact): {_needs_contact(row)}")
print(f"Condition 4 (not retried): {row.get('address_retry_ran') != True}")
```

### Low Retry Success Rate

**Possible Reasons:**
1. discovered_address is low quality (generic, ambiguous)
2. Business name too common for directory matching
3. Directory data simply doesn't exist

**Check:**
```bash
# Review retry attempts vs successes
grep "ADDRESS_RETRY_SENTINEL" railway.log | wc -l
grep "phase2_bbb_phone\|phase2_yp_phone" enriched.csv | grep -v "^$" | wc -l
```

---

## 📈 Success Criteria

 Retry runs when all 4 conditions met  
 Loop prevention works (only retries once)  
 Contact coverage improves (+5-10%)  
 Sentinels visible in logs  
 No regressions in existing phases  
 Error handling prevents crashes  

---

**Status:** 🟢 **PRODUCTION READY**  
**Commit:** `da769cf`  
**Impact:** Smarter directory retries + better contact coverage
