#!/bin/sh
# Install git hooks from scripts/ into .git/hooks/
cp scripts/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
echo "Installed pre-commit hook."
