#!/usr/bin/env python3
"""
Test script to verify name classification logic
Run this to test the classification module without API keys
"""

from tp_enrich.classification import classify_name

# Test cases from specification
test_cases = [
    # Business cases
    ("ABC Trucking LLC", "business"),
    ("Green Valley Cafe", "business"),
    ("Atlas Construction Inc", "business"),
    ("Smith & Sons", "business"),
    ("Senior INS Services", "business"),
    ("XYZ Corp", "business"),

    # Person cases
    ("John Smith", "person"),
    ("Mary Johnson", "person"),
    ("Uncle Leo", "person"),
    ("Big Dame Big Dame", "person"),

    # Other cases
    ("Customer Service", "other"),
    ("Anonymous", "other"),
    ("consumer", "other"),
    ("TD", "other"),  # Acronym without business keywords
    ("Atlanta, Georgia", "other"),
]

print("Testing Name Classification")
print("=" * 60)

passed = 0
failed = 0

for name, expected in test_cases:
    result = classify_name(name)
    status = "✓" if result == expected else "✗"

    if result == expected:
        passed += 1
    else:
        failed += 1

    print(f"{status} {name:30s} -> {result:10s} (expected: {expected})")

print("=" * 60)
print(f"Results: {passed} passed, {failed} failed")

if failed == 0:
    print("✓ All tests passed!")
else:
    print(f"✗ {failed} tests failed")
