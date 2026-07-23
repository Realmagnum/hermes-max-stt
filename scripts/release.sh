#!/bin/bash
# Release script for hermes-max-integration
# Usage: ./scripts/release.sh v2.5.0
set -e

if [ $# -ne 1 ]; then
  echo "Usage: $0 vX.Y.Z"
  echo "Example: $0 v2.5.0"
  exit 1
fi

VERSION="$1"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# Проверка: мы на main, чистое рабочее дерево
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "❌ Must be on 'main' branch, currently on '$BRANCH'"
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "❌ Working tree is not clean. Commit or stash first."
  exit 1
fi

# Проверка, что версия в plugin.yaml соответствует
PLUGIN_VERSION="$(grep '^version:' plugin.yaml | awk '{print $2}')"
TAG_VERSION="${VERSION#v}"
if [ "$PLUGIN_VERSION" != "$TAG_VERSION" ]; then
  echo "⚠️  plugin.yaml version ($PLUGIN_VERSION) != tag version ($TAG_VERSION)"
  echo "   Update plugin.yaml first, then commit."
  echo "   => patch plugin.yaml version: $PLUGIN_VERSION -> $TAG_VERSION"
  echo "   => git commit -m \"chore: bump version to $TAG_VERSION\""
  exit 1
fi

# Pull latest
echo "⟳ Pulling latest from gitea..."
git pull gitea main

# Тег
echo "🏷️  Creating tag $VERSION..."
git tag -a "$VERSION" -m "Release $VERSION"

# Генерация CHANGELOG_EN.md
echo "📝 Generating CHANGELOG_EN.md..."
git cliff --config cliff.toml --tag "$VERSION" -l --prepend CHANGELOG_EN.md

# Генерация CHANGELOG.md (RU)
echo "📝 Generating CHANGELOG.md..."
git cliff --config cliff-ru.toml --tag "$VERSION" -l --prepend CHANGELOG.md

# Показать что изменилось
echo ""
echo "=== Changes in CHANGELOG.md ==="
git diff CHANGELOG.md | head -80
echo ""
echo "=== Changes in CHANGELOG_EN.md ==="
git diff CHANGELOG_EN.md | head -80

# Коммит changelog
echo ""
echo "⟳ Committing changelog update..."
git add CHANGELOG.md CHANGELOG_EN.md
git commit -m "chore: update changelog for $VERSION"

# Пуш
echo "⟳ Pushing to gitea..."
git push gitea main --tags

echo ""
echo "✅ Release $VERSION published!"
echo "   GitHub mirror will sync automatically."
