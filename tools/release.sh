#!/usr/bin/env bash
#
# Build one release carrying both halves of Rose Bouquet.
#
# The desktop app and the Android client live in the same repository and go out
# together, so a version number means the same thing on both. A phone and a
# desktop that disagree about what "0.3.0" contains is the sort of confusion
# nobody unpicks later.
#
# Usage: tools/release.sh 0.3.0
set -euo pipefail

version="${1:-}"
if [[ -z "$version" ]]; then
    echo "usage: tools/release.sh <version>   e.g. tools/release.sh 0.3.0" >&2
    exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
out="$root/dist/$version"
mkdir -p "$out"

echo "── Desktop ──────────────────────────────────────────────"
python -m pytest -q
python -m build --outdir "$out"
echo "   wheel and sdist in $out"

echo
echo "── Android ──────────────────────────────────────────────"
# Android Gradle cannot use a JDK newer than 21, and this machine has 26 as its
# default. Picked explicitly rather than hoping JAVA_HOME points somewhere it
# is happy with.
for candidate in "${JAVA_HOME:-}" /usr/lib/jvm/java-21-openjdk /usr/lib/jvm/java-21; do
    if [[ -n "$candidate" && -x "$candidate/bin/javac" ]]; then
        export JAVA_HOME="$candidate"
        break
    fi
done
echo "   JAVA_HOME=${JAVA_HOME:-<none found>}"

# Gradle needs to know where the Android SDK is. `local.properties` carries
# that on a machine somebody has set up by hand, and it is deliberately not in
# the repository — it is a path that only exists here. ANDROID_HOME is the
# portable answer, so a checkout on any other machine (or a build server) can
# say where its SDK is without editing a file.
if [[ ! -f android/local.properties && -z "${ANDROID_HOME:-}" && -z "${ANDROID_SDK_ROOT:-}" ]]; then
    echo "   no Android SDK: set ANDROID_HOME, or put sdk.dir in android/local.properties" >&2
    echo "   skipping the phone client" >&2
    android_skipped=1
fi

if [[ -z "${android_skipped:-}" ]]; then
    ( cd android && ./gradlew --quiet testDebugUnitTest assembleRelease )
fi

found="$(find android/app/build/outputs/apk/release -name '*.apk' 2>/dev/null | head -1)"
if [[ -n "$found" ]]; then
    cp "$found" "$out/rose-bouquet-$version.apk"
    echo "   apk in $out"
else
    echo "   no apk produced — release builds need a signing config" >&2
fi

echo
echo "── Ready ────────────────────────────────────────────────"
ls -1 "$out"
echo
echo "Then:  gh release create v$version $out/* --title \"Rose Bouquet $version\""
