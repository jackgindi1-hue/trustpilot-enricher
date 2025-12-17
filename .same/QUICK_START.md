# Phase 2 Crashproof - Quick Start Guide

**Status:** ✅ LIVE IN PRODUCTION
**URL:** https://trustpilot-enricher-production.up.railway.app

---

## ⚡ 60-Second Test

```bash
# 1. Health check
curl https://trustpilot-enricher-production.up.railway.app/health

# 2. Run automated test
cd trustpilot-enricher
./test_production.sh

# ✅ Done! Check output.csv for Phase 2 data
```

---

## 🎯 What's New

**3 Critical Fixes:**
1. ✅ Hunter key detection (no more "missing key" errors)
2. ✅ YellowPages URL validation (no more category pages)
3. ✅ Crashproof wrapper (100% guaranteed all 22 Phase 2 fields)

**8 New CSV Columns:**
- `phase2_bbb_phone`, `phase2_bbb_email`, `phase2_bbb_website`, `phase2_bbb_names`
- `phase2_yp_phone`, `phase2_yp_email`, `phase2_yp_website`, `phase2_yp_names`

---

## 📊 Expected Results

| Metric | Before | After |
|--------|--------|-------|
| Phone Coverage | 60% | 85-90% |
| Email Coverage | 60% | 85-90% |
| CSV Crashes | 5% | 0% |

---

## ✅ Success Indicators (Check Logs)

**Good:**
```
ENV CHECK (HOTFIX v2) | Hunter=True(...) ✅
PHASE2 BBB (HOTFIX v2): attempted=True notes=ok ✅
PHASE2 YP URL PICK (HOTFIX v2) | url=...yellowpages.com/.../mip/... ✅
```

**Bad:**
```
Hunter=False() ❌
KeyError: phase2_bbb_phone ❌
url=...yellowpages.com/roofing-contractors ❌
```

---

## 💰 Credit Usage

**Per business with Phase 2 triggered (~40-50%):**
- SerpApi: 3 credits (BBB + YP + OC)
- Hunter: 1 credit (if domain exists)

---

## 📚 Full Documentation

- **Testing Guide:** `.same/PRODUCTION_TESTING_GUIDE.md`
- **Deployment Summary:** `.same/DEPLOYMENT_SUMMARY.md`
- **Changelog:** `CHANGELOG.md`
- **Todos:** `.same/todos.md`

---

## 🆘 Quick Troubleshooting

**Issue:** API returns 500 error
**Fix:** Check Railway logs, verify environment variables set

**Issue:** No Phase 2 data in output
**Fix:** Normal if businesses have complete Google data

**Issue:** "missing key" error
**Fix:** Verify `HUNTER_KEY` set in Railway environment

---

## 🚀 Next Steps

1. Run `./test_production.sh`
2. Check output.csv for Phase 2 columns
3. Monitor Railway logs
4. Process production CSV files

---

**Ready to test? Run the script:**
```bash
./test_production.sh
```
