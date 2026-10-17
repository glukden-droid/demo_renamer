#!/usr/bin/env bash
# Собирает клиент Mumble 1.5 (Linux) из исходников.
# Использование: ./build_linux.sh [тег]   (по умолчанию v1.5.939)
set -euo pipefail

TAG="${1:-v1.5.939}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC="$ROOT/build/mumble-src"
OUT="$ROOT/build/mumble-$TAG"

if command -v apt-get >/dev/null; then
    sudo apt-get update
    sudo apt-get install -y build-essential cmake git pkg-config \
        qtbase5-dev qttools5-dev qttools5-dev-tools libqt5svg5-dev \
        libboost-dev libssl-dev libprotobuf-dev protobuf-compiler \
        libcap-dev libxi-dev libasound2-dev libpulse-dev libogg-dev \
        libsndfile1-dev libspeechd-dev libavahi-compat-libdnssd-dev \
        libxcb-xinerama0 libpoco-dev libopus-dev
fi

rm -rf "$SRC"
git clone --depth 1 --branch "$TAG" --recurse-submodules --shallow-submodules \
    https://github.com/mumble-voip/mumble.git "$SRC"

cmake -S "$SRC" -B "$OUT" -DCMAKE_BUILD_TYPE=Release \
    -Dclient=ON -Dserver=OFF -Doverlay=OFF -Dwarnings-as-errors=OFF \
    -Dplugins=ON -Dtests=OFF -Dbundled-opus=OFF -Dzeroconf=OFF \
    -Dspeechd=ON
cmake --build "$OUT" -j"$(nproc)"

echo "Готово: $OUT/mumble"
