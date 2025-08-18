.PHONY: test coverage clean

# Default test target
test:
	@echo "Running tests..."
	@source .venv/bin/activate && python -m pytest tests/ -v

# Test with coverage reporting
coverage:
	@echo "Running tests with coverage..."
	@source .venv/bin/activate && python -m pytest tests/ -v --cov=. --cov-report=term-missing --cov-report=html --cov-report=xml

# Clean coverage reports
clean:
	@echo "Cleaning coverage reports..."
	@rm -rf htmlcov/
	@rm -f coverage.xml
	@rm -f .coverage

# Install dependencies
install:
	@echo "Installing dependencies..."
	@pip install -r requirements.txt
	@pip install -r requirements-test.txt

# Run tests with coverage using the script
coverage-script:
	@./scripts/run_tests_with_coverage.sh

# Help
help:
	@echo "Available targets:"
	@echo "  test            - Run tests"
	@echo "  coverage        - Run tests with coverage reporting"
	@echo "  coverage-script - Run tests with coverage using script"
	@echo "  clean           - Clean coverage reports"
	@echo "  install         - Install dependencies"
	@echo "  help            - Show this help"