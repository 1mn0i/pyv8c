#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${PYV8_V8_HOME:?Set PYV8_V8_HOME to the matching Linux V8 build}"
export PYV8_SANITIZERS=address,undefined
export ASAN_OPTIONS=detect_leaks=0:abort_on_error=1
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
python3.12 setup.py build_ext --inplace --force
export LD_PRELOAD="$(c++ -print-file-name=libasan.so)${LD_PRELOAD:+:$LD_PRELOAD}"
export PYTHONPATH="$PWD:${PYV8C_ROOT:-$PWD/../pyv8c}"
python3.12 -X faulthandler -m pytest -q
cd "${PYV8C_ROOT:-../pyv8c}"
python3.12 -X faulthandler -m pytest -q
