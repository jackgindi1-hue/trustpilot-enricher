# Quick Setup Guide

## Step 1: Install Dependencies

```bash
cd trustpilot-enrichment
pip install -r requirements.txt
```

## Step 2: Set Up Environment Variables

1. Copy the example environment file:
```bash
cp .env.example .env
```

2. Edit `.env` and add your API keys. You can start with just a few keys and add more later:

**Minimum recommended keys:**
- `GOOGLE_PLACES_API_KEY` - Best source for SMB phone numbers
- `YELP_API_KEY` - Good for local business data

**Optional but recommended:**
- `HUNTER_API_KEY` - For email discovery
- `APOLLO_API_KEY` - For company domain discovery

**Nice to have:**
- `OPENCORPORATES_API_KEY` - For legal verification (works without key but slower)
- `SNOV_API_KEY` - Additional email source
- `FULLENRICH_API_KEY` - Additional domain source

## Step 3: Test the Installation

Run the classification test to verify everything is working:

```bash
python test_classification.py
```

You should see:
```
Testing Name Classification
============================================================
✓ ABC Trucking LLC              -> business   (expected: business)
✓ Green Valley Cafe             -> business   (expected: business)
...
✓ All tests passed!
```

## Step 4: Test with Sample Data

Run the tool on the provided sample CSV:

```bash
python main.py sample_input.csv -o sample_output.csv
```

This will:
1. Classify the display names
2. Attempt to enrich businesses (will skip if API keys not set)
3. Create `sample_output.csv` with results
4. Create `enrichment_cache.json` for caching

## Step 5: Use with Your Data

Once you've verified the tool works, use it with your Trustpilot CSV:

```bash
python main.py your_trustpilot_data.csv -o enriched_results.csv
```

## Troubleshooting

### Missing API Keys
If you see warnings like:
```
✗ GOOGLE_PLACES_API_KEY not provided - will skip this integration
```

This is normal. The tool will work with whatever keys you provide. More keys = more data.

### Import Errors
If you get import errors, make sure you're in the correct directory and have installed dependencies:
```bash
cd trustpilot-enrichment
pip install -r requirements.txt
```

### Rate Limits
If you hit API rate limits:
- The tool will log warnings but continue
- Results are cached, so you can re-run without re-enriching
- Consider spacing out large batches

## Getting API Keys

### Google Places API (Recommended)
1. Go to https://console.cloud.google.com/
2. Create a new project or select existing
3. Enable "Places API"
4. Go to Credentials → Create Credentials → API Key
5. Copy the key to your `.env` file

### Yelp API (Recommended)
1. Go to https://www.yelp.com/developers
2. Create an app
3. Copy the API Key to your `.env` file

### Hunter.io
1. Go to https://hunter.io/
2. Sign up for free account
3. Go to API → Get your API key
4. Copy to `.env` file

### Apollo.io
1. Go to https://www.apollo.io/
2. Sign up and log in
3. Go to Settings → API → Generate API Key
4. Copy to `.env` file

## Next Steps

1. Upload your Trustpilot CSV (from Apify or manual export)
2. Run the enrichment tool
3. Review the output CSV with all enriched contact data
4. Use the enriched data for your business needs

## Support

If you encounter issues:
1. Check the logs for specific error messages
2. Verify your API keys are correct in `.env`
3. Try running with `--verbose` flag for detailed logging
4. Test with the sample data first to isolate issues
