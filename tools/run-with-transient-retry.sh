#!/usr/bin/env bash
set -u

if (($# == 0)); then
  echo "usage: $0 <command> [args...]" >&2
  exit 2
fi

max_attempts="${ABRED_RETRY_MAX_ATTEMPTS:-4}"
read -r -a retry_delays <<< "${ABRED_RETRY_DELAYS:-2 5 10}"
transient_pattern='(httpx|httpcore)\.(ReadTimeout|ConnectTimeout|ConnectError|RemoteProtocolError|PoolTimeout)|HTTPStatusError.*(429|500|502|503|504)|(^|[^0-9])(429|500|502|503|504)([^0-9]|$)'

for ((attempt=1; attempt<=max_attempts; attempt++)); do
  stdout_file="$(mktemp)"
  stderr_file="$(mktemp)"

  if [[ -n "${ABRED_RETRY_CLEAN_PATHS:-}" ]]; then
    read -r -a clean_paths <<< "$ABRED_RETRY_CLEAN_PATHS"
    rm -rf -- "${clean_paths[@]}"
  fi

  "$@" >"$stdout_file" 2>"$stderr_file"
  status=$?
  cat "$stderr_file" >&2

  if ((status == 0)); then
    cat "$stdout_file"
    rm -f "$stdout_file" "$stderr_file"
    exit 0
  fi

  if ! grep -Eqi "$transient_pattern" "$stderr_file"; then
    rm -f "$stdout_file" "$stderr_file"
    exit "$status"
  fi

  if ((attempt >= max_attempts)); then
    echo "Transient producer failure persisted after ${attempt}/${max_attempts} attempts" >&2
    rm -f "$stdout_file" "$stderr_file"
    exit "$status"
  fi

  delay_index=$((attempt - 1))
  if ((delay_index < ${#retry_delays[@]})); then
    delay="${retry_delays[$delay_index]}"
  else
    delay="${retry_delays[${#retry_delays[@]} - 1]:-10}"
  fi
  echo "Transient producer HTTP failure; retrying (${attempt}/${max_attempts}) in ${delay}s..." >&2
  rm -f "$stdout_file" "$stderr_file"
  sleep "$delay"
done

exit 1
