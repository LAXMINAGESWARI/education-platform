#!/usr/bin/env bash
# Restore a custom-format dump into the CNPG primary and check the result.
# Prints table names and counts. Does not print secrets.
set -euo pipefail
set +x

namespace="${NAMESPACE:-curioed}"
cluster="${CLUSTER:-curioed-postgres}"
database="${DATABASE:-education}"
revision="${ALEMBIC_REVISION:-b3c4d5e6f7a8}"
dump=""
counts=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      namespace="$2"
      shift 2
      ;;
    --cluster)
      cluster="$2"
      shift 2
      ;;
    --database)
      database="$2"
      shift 2
      ;;
    --revision)
      revision="$2"
      shift 2
      ;;
    --dump)
      dump="$2"
      shift 2
      ;;
    --counts)
      counts="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${dump}" || -z "${counts}" || ! -f "${dump}" || ! -f "${counts}" ]]; then
  echo "usage: restore.sh --dump FILE --counts FILE" >&2
  exit 2
fi

pod="$(
  kubectl get pods \
    --namespace "${namespace}" \
    -l "cnpg.io/cluster=${cluster},cnpg.io/instanceRole=primary" \
    -o jsonpath='{.items[0].metadata.name}'
)"
if [[ -z "${pod}" ]]; then
  echo "CNPG primary pod not found" >&2
  exit 1
fi

echo "restoring into ${namespace}/${pod}"
kubectl exec --namespace "${namespace}" -i "${pod}" -c postgres -- \
  pg_restore --username postgres --dbname "${database}" --no-owner --exit-on-error \
  < "${dump}"

psql_at() {
  kubectl exec --namespace "${namespace}" "${pod}" -c postgres -- \
    psql --username postgres --dbname "${database}" --no-align --tuples-only --command "$1"
}

failed=0
note() {
  echo "$1" >&2
  failed=1
}

extension="$(psql_at "SELECT extname FROM pg_extension WHERE extname = 'vector'")"
if [[ "${extension}" != "vector" ]]; then
  note "vector extension is missing"
fi

roles="$(psql_at "SELECT rolname FROM pg_roles WHERE rolname IN ('education', 'text_to_sql_reader') ORDER BY 1")"
if [[ "${roles}" != $'education\ntext_to_sql_reader' ]]; then
  note "expected roles education and text_to_sql_reader"
fi

priv() {
  psql_at "SELECT ${1}"
}

if [[ "$(priv "has_table_privilege('text_to_sql_reader', 'institutions', 'SELECT')")" != "t" ]]; then
  note "text_to_sql_reader cannot select institutions"
fi
if [[ "$(priv "has_table_privilege('text_to_sql_reader', 'user_roles', 'SELECT')")" != "t" ]]; then
  note "text_to_sql_reader cannot select user_roles"
fi
if [[ "$(priv "has_column_privilege('text_to_sql_reader', 'users', 'password_hash', 'SELECT')")" != "f" ]]; then
  note "text_to_sql_reader can read users.password_hash"
fi
if [[ "$(priv "has_table_privilege('text_to_sql_reader', 'question_answer_keys', 'SELECT')")" != "f" ]]; then
  note "text_to_sql_reader can read question_answer_keys"
fi

current="$(psql_at "SELECT version_num FROM alembic_version")"
if [[ "${current}" != "${revision}" ]]; then
  note "alembic version is ${current}, expected ${revision}"
fi

while IFS=, read -r table expected; do
  table="${table%%$'\r'}"
  expected="${expected%%$'\r'}"
  [[ -z "${table}" || "${table}" == \#* ]] && continue
  if [[ ! "${table}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ || ! "${expected}" =~ ^[0-9]+$ ]]; then
    note "bad counts line"
    continue
  fi
  actual="$(psql_at "SELECT count(*) FROM ${table}")"
  echo "${table} expected=${expected} actual=${actual}"
  if [[ "${actual}" != "${expected}" ]]; then
    note "${table} count mismatch"
  fi
done < "${counts}"

if [[ "${failed}" -ne 0 ]]; then
  echo "restore checks failed" >&2
  exit 1
fi
echo "restore checks passed"
