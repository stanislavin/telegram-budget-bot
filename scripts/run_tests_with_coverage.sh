#!/usr/bin/env bash

# Run tests with coverage reporting
echo "Running tests with coverage..."

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Run pytest with coverage
python -m pytest tests/ -v --cov=. --cov-report=term-missing --cov-report=html --cov-report=xml

echo ""
echo "Coverage reports generated:"
echo "- Terminal output: Immediate summary"
echo "- HTML report: htmlcov/index.html"
echo "- XML report: coverage.xml"