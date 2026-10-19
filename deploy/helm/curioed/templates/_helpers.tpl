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
{{- printf "%s:%s" .repository (.tag | default "unreleased") }}
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
{{- end }}
