#!/usr/bin/env python3
"""
Phase 2 Fix Validation Script

This script validates that the Phase 2 fixes have been applied correctly.

Usage:
    python validate_phase2_fixes.py
"""

import os
import sys
import re

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}\n")

def print_success(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.END}")

def check_file_exists(filepath):
    """Check if a file exists"""
    return os.path.isfile(filepath)

def check_import_in_file(filepath, import_statement):
    """Check if an import statement exists in a file"""
    if not check_file_exists(filepath):
        return False

    with open(filepath, 'r') as f:
        content = f.read()
        return import_statement in content

def check_function_call_in_file(filepath, function_name):
    """Check if a function is called in a file"""
    if not check_file_exists(filepath):
        return False

    with open(filepath, 'r') as f:
        content = f.read()
        pattern = rf'{function_name}\s*\('
        return bool(re.search(pattern, content))

def check_env_var(var_name):
    """Check if an environment variable is set"""
    return bool(os.getenv(var_name))

def main():
    print_header("Phase 2 Fix Validation")

    all_passed = True

    # Test 1: Check if phase2_enrichment.py exists
    print_info("Test 1: Checking if phase2_enrichment.py exists...")
    if check_file_exists("tp_enrich/phase2_enrichment.py"):
        print_success("phase2_enrichment.py exists")
    else:
        print_error("phase2_enrichment.py not found")
        all_passed = False

    # Test 2: Check if phone_enrichment.py imports the new function
    print_info("Test 2: Checking if phone_enrichment.py imports yelp_phone_lookup_safe...")
    if check_import_in_file("tp_enrich/phone_enrichment.py", "from .phase2_enrichment import yelp_phone_lookup_safe"):
        print_success("Import statement found in phone_enrichment.py")
    else:
        print_error("Import statement NOT found in phone_enrichment.py")
        all_passed = False

    # Test 3: Check if yelp_phone_lookup_safe is called
    print_info("Test 3: Checking if yelp_phone_lookup_safe is called...")
    if check_function_call_in_file("tp_enrich/phone_enrichment.py", "yelp_phone_lookup_safe"):
        print_success("yelp_phone_lookup_safe is called in phone_enrichment.py")
    else:
        print_error("yelp_phone_lookup_safe is NOT called in phone_enrichment.py")
        all_passed = False

    # Test 4: Check if yelp_fusion_search_business exists
    print_info("Test 4: Checking if yelp_fusion_search_business exists...")
    if check_function_call_in_file("tp_enrich/phase2_enrichment.py", "def yelp_fusion_search_business"):
        print_success("yelp_fusion_search_business function found")
    else:
        print_error("yelp_fusion_search_business function NOT found")
        all_passed = False

    # Test 5: Check if _pick_best_link_any exists
    print_info("Test 5: Checking if _pick_best_link_any exists...")
    if check_function_call_in_file("tp_enrich/phase2_enrichment.py", "def _pick_best_link_any"):
        print_success("_pick_best_link_any function found")
    else:
        print_error("_pick_best_link_any function NOT found")
        all_passed = False

    # Test 6: Check if yellowpages_link_via_serp exists
    print_info("Test 6: Checking if yellowpages_link_via_serp exists...")
    if check_function_call_in_file("tp_enrich/phase2_enrichment.py", "def yellowpages_link_via_serp"):
        print_success("yellowpages_link_via_serp function found")
    else:
        print_error("yellowpages_link_via_serp function NOT found")
        all_passed = False

    # Test 7: Check if opencorporates_link_via_serp exists
    print_info("Test 7: Checking if opencorporates_link_via_serp exists...")
    if check_function_call_in_file("tp_enrich/phase2_enrichment.py", "def opencorporates_link_via_serp"):
        print_success("opencorporates_link_via_serp function found")
    else:
        print_error("opencorporates_link_via_serp function NOT found")
        all_passed = False

    # Test 8: Check if .env.example has SERP_API_KEY
    print_info("Test 8: Checking if .env.example documents SERP_API_KEY...")
    if check_import_in_file(".env.example", "SERP_API_KEY"):
        print_success("SERP_API_KEY documented in .env.example")
    else:
        print_error("SERP_API_KEY NOT documented in .env.example")
        all_passed = False

    # Test 9: Check environment variables (warnings only)
    print_info("Test 9: Checking environment variables...")
    if check_env_var("YELP_API_KEY") or check_env_var("YELP_FUSION_API_KEY") or check_env_var("YELP_KEY"):
        print_success("YELP_API_KEY is set")
    else:
        print_warning("YELP_API_KEY is not set (optional for validation)")

    if check_env_var("SERP_API_KEY") or check_env_var("SERPAPI_API_KEY") or check_env_var("SERPAPI_KEY"):
        print_success("SERP_API_KEY is set")
    else:
        print_warning("SERP_API_KEY is not set (optional for validation)")

    # Test 10: Check if documentation files exist
    print_info("Test 10: Checking documentation files...")
    docs = [
        ".same/PHASE2_FIX_APPLIED.md",
        ".same/PHASE2_TESTING_GUIDE.md",
        ".same/PHASE2_FIX_SUMMARY.md",
        "CHANGELOG.md"
    ]
    for doc in docs:
        if check_file_exists(doc):
            print_success(f"{doc} exists")
        else:
            print_warning(f"{doc} not found (nice to have)")

    # Summary
    print_header("Validation Summary")

    if all_passed:
        print_success("All critical tests PASSED! ✨")
        print_info("Phase 2 fixes have been successfully applied.")
        print_info("Next steps:")
        print_info("  1. Set YELP_API_KEY and SERP_API_KEY in .env")
        print_info("  2. Restart any running services")
        print_info("  3. Run test: python main.py --input sample_input.csv --output test_output.csv")
        return 0
    else:
        print_error("Some tests FAILED!")
        print_info("Please review the errors above and apply the fixes.")
        print_info("See .same/PHASE2_FIX_APPLIED.md for detailed instructions.")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
