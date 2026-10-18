#!/bin/sh
# These are browser ES modules. Force module parsing; Node's automatic .js
# detection can skip a syntax failure that --input-type=module catches.
set -eu
cd "$(dirname "$0")/.."
rg --files wash_dishes/static -g '*.js' | while IFS= read -r path; do
  node --input-type=module --check < "$path"
done
node --test tests/*.mjs
