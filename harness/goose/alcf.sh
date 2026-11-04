#!/usr/bin/env bash
# Checked before `set -euo pipefail` below -- see install.sh for why.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "Error: run this script, don't source it. Use: ./alcf.sh" >&2
  return 1 2>/dev/null || exit 1
fi

set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  ./alcf.sh --model NAME [--cluster NAME]
  ./alcf.sh --list-models [--cluster NAME]
  ./alcf.sh --list-clusters
  ./alcf.sh --restore-default

Gets an ALCF (Argonne Leadership Computing Facility) inference-service
access token -- silently renewing a cached one if possible, otherwise
printing a URL to open in a browser and prompting you to paste back the
resulting authorization code -- and writes it into the LIVE, private
goose config.yaml under the `openai` provider slot (OPENAI_BASE_URL,
providers.openai.model), the same slot setup.sh seeds with the internal
vllm.fnal.gov default. This *overwrites* that slot; --restore-default
puts the template's vllm.fnal.gov settings back.

(ALCF doesn't use the `litellm` slot: that's reserved for the actual
litellm.fnal.gov proxy, configured separately by setup-litellm.sh.)

  --cluster NAME           Which ALCF cluster to use (default: metis);
                           --list-clusters shows what ALCF offers
                           (e.g. metis, sophia, minerva, tara).
  --model NAME             Model to request. Required unless
                            --list-models or --restore-default is given.
  --list-models            Print available models for the cluster and
                            exit, without changing anything.
  --list-clusters          Print ALCF's clusters (with model counts) and exit.
  --restore-default        Put back the openai provider settings from
                            config.yaml.example (undoes a previous run).
USAGE
  exit 2
}

cluster="metis"
model=""
list_only=0
restore_default=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cluster)
      cluster="${2:-}"
      shift 2
      ;;
    --model)
      model="${2:-}"
      shift 2
      ;;
    --list-models)
      list_only=1
      shift
      ;;
    --list-clusters)
      list_only=2
      shift
      ;;
    --restore-default)
      restore_default=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Error: unknown argument '$1'" >&2
      usage
      ;;
  esac
done

if [[ ! "$cluster" =~ ^[a-z0-9_-]+$ ]]; then
  echo "Error: --cluster must be a cluster name (see --list-clusters), got '$cluster'" >&2
  exit 2
fi

if [[ "$restore_default" == "1" && ( "$list_only" == "1" || -n "$model" ) ]]; then
  echo "Error: --restore-default can't be combined with --model/--list-models" >&2
  exit 2
fi

if [[ "$restore_default" == "0" && "$list_only" == "0" && -z "$model" ]]; then
  echo "Error: --model NAME is required (or pass --list-models / --restore-default)" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/env.sh"
config_file="$XDG_CONFIG_HOME/goose/config.yaml"

if [[ ! -f "$config_file" ]]; then
  echo "Error: no live config.yaml yet at $config_file -- run setup.sh first" >&2
  exit 1
fi

if [[ "$restore_default" == "1" ]]; then
  python3 "$script_dir/alcf_set_provider.py" restore-default "$config_file" "$script_dir/config.yaml.example"
  exit $?
fi

source "$script_dir/alcf_ensure_token.sh"

# Clusters, frameworks and models come from ALCF's own list-endpoints,
# so new clusters (minerva, tara, ...) work without changes here. The
# OpenAI-compatible base URL is .../resource_server/<cluster>/<framework>/v1
# (sophia/vllm, metis/api); for a cluster with several frameworks the one
# with models is used, preferring vllm, then api.
alcf_api="https://inference-api.alcf.anl.gov/resource_server"
endpoints="$(curl -sS -m 30 "$alcf_api/list-endpoints" -H "Authorization: Bearer ${ALCF_TOKEN}")" || {
  echo "Error: could not reach $alcf_api" >&2; exit 1; }
lookup="$(printf '%s' "$endpoints" | "$ALCF_VENV/bin/python" -I -c '
import sys, json
mode, cluster = sys.argv[1], sys.argv[2]
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit("Error: ALCF returned no usable answer")
if "error" in data:
    e = data["error"]
    sys.exit("Error: " + str(e.get("message", e) if isinstance(e, dict) else e))
clusters = data.get("clusters", {})
def frameworks(c):
    fw = (clusters.get(c) or {}).get("frameworks", {}) or {}
    return {k: list((v or {}).get("models", []) or []) for k, v in fw.items()}
if mode == "clusters":
    for c in sorted(clusters):
        n = sum(len(m) for m in frameworks(c).values())
        print(f"{c}\t{n} models")
    sys.exit(0)
fw = frameworks(cluster)
if not fw:
    have = ", ".join(sorted(clusters))
    sys.exit(f"Error: ALCF has no cluster {cluster!r} (have: {have})")
with_models = [k for k, m in fw.items() if m]
order = [k for k in ("vllm", "api") if k in with_models] + sorted(set(with_models) - {"vllm", "api"})
framework = (order or sorted(fw))[0]
print(framework)
for m in fw[framework]:
    print(m)
' "$([[ "$list_only" == 2 ]] && echo clusters || echo models)" "$cluster")" || exit 1

if [[ "$list_only" == "2" ]]; then
  echo "ALCF clusters:"
  printf '%s\n' "$lookup" | sed 's/^/  /'
  exit 0
fi

framework="$(printf '%s\n' "$lookup" | head -1)"
base_url="$alcf_api/$cluster/$framework/v1"

if [[ "$list_only" == "1" ]]; then
  echo "Available models on $cluster ($framework):"
  printf '%s\n' "$lookup" | tail -n +2 | sed 's/^/  /'
  exit 0
fi

OPENAI_API_KEY_VALUE="$ALCF_TOKEN" python3 "$script_dir/alcf_set_provider.py" set "$config_file" "$base_url" "$model"
echo "Run './alcf.sh --restore-default' to switch back to the internal vllm default."
