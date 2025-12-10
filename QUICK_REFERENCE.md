# Quick Reference Card

## Installation (One-Time)
```bash
cd trustpilot-enrichment
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

## Basic Commands

### Test Installation
```bash
python test_classification.py
```

### Run on Sample Data
```bash
python main.py sample_input.csv
```

### Process Your CSV
```bash
python main.py your_file.csv
```

### Custom Output File
```bash
python main.py input.csv -o output.csv
```

### Verbose Logging
```bash
python main.py input.csv --verbose
```

### Custom Cache
```bash
python main.py input.csv -c my_cache.json
```

## Required API Keys (Minimum)

Add to `.env` file:
```bash
GOOGLE_PLACES_API_KEY=your_key_here
YELP_API_KEY=your_key_here
```

## Optional API Keys (Recommended)

```bash
HUNTER_API_KEY=your_key_here
APOLLO_API_KEY=your_key_here
OPENCORPORATES_API_KEY=your_key_here
SNOV_API_KEY=user_id:api_key
FULLENRICH_API_KEY=your_key_here
```

## Input CSV Format

Must have `displayName` column (or `display_name`).

Optional columns: `url`, `date`, `rating`, `city`, `state`

Example:
```csv
displayName,url,date,rating,city,state
ABC Trucking LLC,https://...,2024-01-15,5,Houston,TX
John Smith,https://...,2024-01-16,4,New York,NY
```

## Output Format

36 columns including:
- `name_classification` - business/person/other
- `company_domain` - Discovered domain
- `primary_phone` - Best phone number
- `primary_email` - Best email
- `business_address` - Address
- `overall_lead_confidence` - high/medium/low/failed
- `all_phones_json` - All discovered phones
- `generic_emails_json` - All generic emails
- `person_emails_json` - All person emails

## Classification Rules Quick View

**Business:**
- Legal suffixes: LLC, Inc, Corp, Ltd, etc.
- Industry keywords: Trucking, Construction, Restaurant, etc.
- Structure: "X & Y", "X & Sons"

**Person:**
- Name-like: "John Smith", "Mary Johnson"
- Nicknames: "Uncle Leo"
- 1-3 name tokens

**Other:**
- "Customer Service", "Anonymous", "Consumer"
- Locations: "Atlanta, Georgia"
- Acronyms without business context

## Phone Priority (Automatic)

1. Google Maps (high confidence)
2. Yelp (high confidence)
3. YellowPages/BBB
4. Apollo/FullEnrich
5. Social media

## Email Priority (Automatic)

1. Person emails (john@domain.com)
2. Generic emails (info@domain.com)
3. Scraped emails
4. Catchall emails (last resort)

## Confidence Levels

**High:** Domain + Phone + Email all medium or better
**Medium:** Partial coverage
**Low:** Minimal enrichment
**Failed:** No useful data

## Common Issues

**"API key not provided"**
- Add key to `.env` file
- Tool will skip that integration

**"No results"**
- Check input CSV format
- Verify displayName column exists
- Try with `--verbose` for details

**"Rate limit exceeded"**
- Results are cached
- Re-run will use cache
- Space out large batches

## File Locations

- **Input:** Your Trustpilot CSV
- **Output:** `enriched_output.csv` (default) or custom via `-o`
- **Cache:** `enrichment_cache.json` (default) or custom via `-c`
- **Logs:** Console output (use `--verbose` for details)

## Getting API Keys

| Service | URL | Cost |
|---------|-----|------|
| Google Places | https://console.cloud.google.com/ | Free tier available |
| Yelp | https://www.yelp.com/developers | Free |
| Hunter.io | https://hunter.io/ | Free tier: 25/month |
| Apollo.io | https://www.apollo.io/ | Free tier available |
| OpenCorporates | https://opencorporates.com/ | Works without key |

## Performance

- Classification: Instant
- Enrichment: 2-5 sec/business (all APIs)
- Cache hits: <0.1 sec/business
- Large files: Progress logged

## Documentation Files

- `PROJECT_OVERVIEW.md` - What was built
- `README.md` - Full documentation
- `SETUP_GUIDE.md` - Step-by-step setup
- `.same/IMPLEMENTATION_SUMMARY.md` - Technical details

## Support

1. Check error message in console
2. Run with `--verbose` for details
3. Verify API keys in `.env`
4. Test with `sample_input.csv` first

## Example Workflow

```bash
# 1. Setup (one-time)
pip install -r requirements.txt
cp .env.example .env
vim .env  # Add API keys

# 2. Test
python test_classification.py

# 3. Sample run
python main.py sample_input.csv

# 4. Real data
python main.py trustpilot_export.csv -o enriched.csv --verbose

# 5. Review output
head enriched.csv
```

---

**Need help?** Check the documentation files listed above.
