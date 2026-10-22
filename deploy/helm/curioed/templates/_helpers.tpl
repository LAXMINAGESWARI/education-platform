{{- define "curioed.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "curioed.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "curioed.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: curioed
{{- end }}

{{- define "curioed.selectorLabels" -}}
app.kubernetes.io/name: {{ include "curioed.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "curioed.apiName" -}}
{{- printf "%s-api" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.webName" -}}
{{- printf "%s-web" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.workerDoclingName" -}}
{{- printf "%s-worker-docling" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.workerLlmName" -}}
{{- printf "%s-worker-llm" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.cloudflaredName" -}}
{{- printf "%s-cloudflared" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.migrateName" -}}
{{- printf "%s-migrate" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.seedName" -}}
{{- printf "%s-seed" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.clusterName" -}}
{{- $base := include "curioed.fullname" . | trunc 40 | trimSuffix "-" }}
{{- printf "%s-postgres" $base }}
{{- end }}

{{- define "curioed.postgresHost" -}}
{{- $cluster := include "curioed.clusterName" . }}
{{- printf "%s-rw.%s.svc.%s" $cluster .Release.Namespace .Values.clusterDomain }}
{{- end }}

{{- define "curioed.uploadsClaim" -}}
{{- printf "%s-uploads" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.hfClaim" -}}
{{- printf "%s-hf-cache" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.inductorClaim" -}}
{{- printf "%s-inductor-cache" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.backupClaim" -}}
{{- printf "%s-backups" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.imageRef" -}}
{{- $tag := required (printf "image.tag is required for %s. Pass a commit SHA or pinned tag with --set. ci is only a test tag, not a published image. Do not use latest or unreleased." .repository) .tag -}}
{{- printf "%s:%s" .repository $tag -}}
{{- end }}

{{- define "curioed.validate" -}}
{{- if not (regexMatch "^[A-Za-z0-9.-]+$" .Values.domain) }}
{{- fail (printf "domain must be a hostname, got %q" .Values.domain) }}
{{- end }}
{{- if not (regexMatch "^[A-Za-z0-9.-]+$" .Values.clusterDomain) }}
{{- fail "clusterDomain must be a DNS name" }}
{{- end }}
{{- range $field := list "owner" "database" "readerRole" }}
{{- $value := index $.Values.cnpg $field }}
{{- if not (regexMatch "^[A-Za-z_][A-Za-z0-9_]*$" $value) }}
{{- fail (printf "cnpg.%s must be a plain SQL identifier" $field) }}
{{- end }}
{{- end }}
{{- if not .Values.secrets.app.existingSecret }}
{{- fail "secrets.app.existingSecret is required" }}
{{- end }}
{{- if not .Values.secrets.database.existingSecret }}
{{- fail "secrets.database.existingSecret is required" }}
{{- end }}
{{- if not .Values.secrets.textToSql.existingSecret }}
{{- fail "secrets.textToSql.existingSecret is required" }}
{{- end }}
{{- if not .Values.secrets.tunnel.existingSecret }}
{{- fail "secrets.tunnel.existingSecret is required" }}
{{- end }}
{{- if .Values.cnpg.backup.enabled }}
{{- fail "cnpg.backup.enabled is not supported yet. Off-site Barman needs object storage. The migrate hook pg_dump is the backup that runs before Alembic." }}
{{- end }}
{{- range $image := list .Values.api.image .Values.web.image .Values.cloudflared.image }}
{{- if eq ($image.tag | default "") "latest" }}
{{- fail (printf "image tag for %s must be a digest or pinned tag, not latest" $image.repository) }}
{{- end }}
{{- end }}
{{- if contains ":latest" .Values.cnpg.image }}
{{- fail "cnpg.image must not use the latest tag" }}
{{- end }}
{{- if lt (int .Values.api.limitConcurrency) 1 }}
{{- fail "api.limitConcurrency must be at least 1" }}
{{- end }}
{{- if and .Values.networkPolicy.enabled (eq .Values.networkPolicy.postgres.mode "cidr") (not .Values.networkPolicy.postgres.cidr) }}
{{- fail "networkPolicy.postgres.cidr is required when postgres.mode is cidr" }}
{{- end }}
{{- if and .Values.keda.api.enabled (not .Values.keda.api.serverAddress) }}
{{- fail "keda.api.serverAddress is required when the API Prometheus trigger is enabled" }}
{{- end }}
{{- if and .Values.keda.llm.enabled (not .Values.workers.llm.enabled) }}
{{- fail "keda.llm.enabled requires workers.llm.enabled, and both stay off until WORKER_QUEUES exists" }}
{{- end }}
{{- if .Values.networkPolicy.enabled }}
{{- $mode := .Values.networkPolicy.postgres.mode }}
{{- if not (or (eq $mode "cnpg") (eq $mode "cidr")) }}
{{- fail "networkPolicy.postgres.mode must be cnpg or cidr" }}
{{- end }}
{{- end }}
{{- end }}

{{/* .security is the workload identity. Omit user, group, and fsGroup when null. */}}
{{- define "curioed.podSecurityContext" -}}
securityContext:
  runAsNonRoot: {{ .security.runAsNonRoot }}
  {{- with .security.runAsUser }}
  runAsUser: {{ . | int64 }}
  {{- end }}
  {{- with .security.runAsGroup }}
  runAsGroup: {{ . | int64 }}
  {{- end }}
  {{- with .security.fsGroup }}
  fsGroup: {{ . | int64 }}
  {{- end }}
  seccompProfile:
    type: {{ .root.Values.podSecurity.seccompProfile }}
{{- end }}

{{/* Per-workload bool wins. Otherwise the chart-level podSecurity flag. */}}
{{- define "curioed.readOnlyRootFilesystem" -}}
{{- if kindIs "bool" .security.readOnlyRootFilesystem -}}
{{- .security.readOnlyRootFilesystem -}}
{{- else -}}
{{- .root.Values.podSecurity.readOnlyRootFilesystem -}}
{{- end -}}
{{- end }}

{{/* Writable dirs for a read-only root. Nginx also needs its cache and pid dir. */}}
{{- define "curioed.tmpVolumeMounts" -}}
{{- if eq (include "curioed.readOnlyRootFilesystem" .) "true" }}
- name: tmp
  mountPath: /tmp
{{- if .nginx }}
- name: nginx-cache
  mountPath: /var/cache/nginx
- name: nginx-run
  mountPath: /var/run
{{- end }}
{{- end }}
{{- end }}

{{- define "curioed.tmpVolumes" -}}
{{- if eq (include "curioed.readOnlyRootFilesystem" .) "true" }}
- name: tmp
  emptyDir: {}
{{- if .nginx }}
- name: nginx-cache
  emptyDir: {}
- name: nginx-run
  emptyDir: {}
{{- end }}
{{- end }}
{{- end }}

{{- define "curioed.containerSecurityContext" -}}
securityContext:
  allowPrivilegeEscalation: {{ .root.Values.podSecurity.allowPrivilegeEscalation }}
  runAsNonRoot: {{ .security.runAsNonRoot }}
  {{- with .security.runAsUser }}
  runAsUser: {{ . | int64 }}
  {{- end }}
  {{- with .security.runAsGroup }}
  runAsGroup: {{ . | int64 }}
  {{- end }}
  readOnlyRootFilesystem: {{ include "curioed.readOnlyRootFilesystem" . | trim }}
  capabilities:
    drop:
      {{- toYaml .root.Values.podSecurity.dropCapabilities | nindent 6 }}
    {{- with .add }}
    add:
      {{- toYaml . | nindent 6 }}
    {{- end }}
{{- end }}

{{- define "curioed.priorityClassName" -}}
{{- if .root.Values.priorityClass.enabled -}}
{{- printf "%s-%s" (include "curioed.fullname" .root) .tier -}}
{{- end -}}
{{- end }}

{{- define "curioed.keepAnnotation" -}}
helm.sh/resource-policy: keep
{{- end }}

{{- define "curioed.platformConfigName" -}}
{{- printf "%s-platform" (include "curioed.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "curioed.platformEnvData" -}}
{{- $platform := dict }}
{{- range $key, $value := .Values.config.platformEnv }}
{{- if not (regexMatch "^[A-Za-z_][A-Za-z0-9_]*$" $key) }}
{{- fail (printf "config.platformEnv key %q is not an env name" $key) }}
{{- end }}
{{- $_ := set $platform $key (toString $value) }}
{{- end }}
{{- range $key, $value := .Values.config.extraEnv }}
{{- if not (regexMatch "^[A-Za-z_][A-Za-z0-9_]*$" $key) }}
{{- fail (printf "config.extraEnv key %q is not an env name" $key) }}
{{- end }}
{{- $_ := set $platform $key (toString $value) }}
{{- end }}
{{- range $key, $value := $platform }}
{{ $key }}: {{ $value | quote }}
{{- end }}
{{- end }}

{{- define "curioed.envFrom" -}}
envFrom:
  - configMapRef:
      name: {{ include "curioed.platformConfigName" . }}
{{- end }}

{{- define "curioed.dbPoolEnv" -}}
- name: DB_POOL_SIZE
  value: {{ .pool.size | quote }}
- name: DB_MAX_OVERFLOW
  value: {{ .pool.maxOverflow | quote }}
- name: DB_POOL_TIMEOUT_SECONDS
  value: {{ .pool.timeoutSeconds | quote }}
- name: DB_POOL_RECYCLE_SECONDS
  value: {{ .pool.recycleSeconds | quote }}
{{- if .textToSql }}
- name: TEXT_TO_SQL_DB_POOL_SIZE
  value: {{ .pool.textToSqlSize | quote }}
- name: TEXT_TO_SQL_DB_MAX_OVERFLOW
  value: {{ .pool.textToSqlMaxOverflow | quote }}
{{- end }}
{{- if .worker }}
- name: WORKER_SHUTDOWN_GRACE_SECONDS
  value: {{ (.shutdownGrace | default .pool.shutdownGraceSeconds) | quote }}
{{- end }}
{{- end }}

{{- define "curioed.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets.existingSecret }}
imagePullSecrets:
  - name: {{ . }}
{{- end }}
{{- end }}

{{- define "curioed.scheduling" -}}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "curioed.uploadsAffinity" -}}
podAffinity:
  requiredDuringSchedulingIgnoredDuringExecution:
    - labelSelector:
        matchLabels:
          app.kubernetes.io/instance: {{ .Release.Name }}
          curioed.io/uploads: "true"
      topologyKey: kubernetes.io/hostname
{{- end }}

{{- define "curioed.dataVolumeMounts" -}}
- name: uploads
  mountPath: /data/uploads
- name: hf-cache
  mountPath: /data/hf
- name: inductor-cache
  mountPath: /data/torchinductor
{{- end }}

{{- define "curioed.dataVolumes" -}}
- name: uploads
  persistentVolumeClaim:
    claimName: {{ include "curioed.uploadsClaim" . }}
- name: hf-cache
  persistentVolumeClaim:
    claimName: {{ include "curioed.hfClaim" . }}
- name: inductor-cache
  persistentVolumeClaim:
    claimName: {{ include "curioed.inductorClaim" . }}
{{- end }}

{{- define "curioed.appEnv" -}}
- name: ENVIRONMENT
  value: {{ .Values.config.environment | quote }}
- name: CORS_ORIGINS
  value: {{ .Values.config.corsOrigins | quote }}
- name: CORS_ORIGIN_REGEX
  value: {{ .Values.config.corsOriginRegex | quote }}
- name: OPENROUTER_BASE_URL
  value: {{ .Values.config.openRouterBaseUrl | quote }}
- name: OPENROUTER_MODEL
  value: {{ .Values.config.openRouterModel | quote }}
- name: ADK_MODEL
  value: {{ .Values.config.adkModel | quote }}
- name: BLIND_SOLVER_MODEL
  value: {{ .Values.config.blindSolverModel | quote }}
- name: TEXT_TO_SQL_DB_USER
  value: {{ .Values.config.textToSqlUser | quote }}
- name: MATERIALS_DIR
  value: /app/docs/curriculum
- name: UPLOAD_DIR
  value: /data/uploads
- name: HF_HOME
  value: /data/hf
- name: TRANSFORMERS_CACHE
  value: /data/hf
- name: HF_HUB_DISABLE_TELEMETRY
  value: "1"
- name: TOKENIZERS_PARALLELISM
  value: "false"
- name: DOCLING_ARTIFACTS_PATH
  value: /opt/docling-models
- name: TORCHINDUCTOR_CACHE_DIR
  value: /data/torchinductor
- name: POSTGRES_USER
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.database.existingSecret }}
      key: {{ .Values.secrets.database.usernameKey }}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.database.existingSecret }}
      key: {{ .Values.secrets.database.passwordKey }}
- name: POSTGRES_DB
  value: {{ .Values.cnpg.database | quote }}
- name: POSTGRES_HOST
  value: {{ include "curioed.postgresHost" . | quote }}
- name: DATABASE_URL
  value: {{ printf "postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@$(POSTGRES_HOST):5432/$(POSTGRES_DB)" | quote }}
- name: JWT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.app.existingSecret }}
      key: {{ .Values.secrets.app.jwtSecretKey }}
- name: TEXT_TO_SQL_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.app.existingSecret }}
      key: {{ .Values.secrets.app.textToSqlPasswordKey }}
- name: OPENROUTER_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.app.existingSecret }}
      key: {{ .Values.secrets.app.openRouterApiKeyKey }}
{{- range .Values.config.extraSecretEnv }}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: {{ .key }}
{{- end }}
{{- end }}
