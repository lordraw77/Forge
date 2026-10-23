{{- define "forge.name" -}}forge{{- end -}}

{{- define "forge.fullname" -}}
{{- if .Values.fullnameOverride -}}{{ .Values.fullnameOverride | trunc 50 | trimSuffix "-" }}
{{- else if contains "forge" .Release.Name -}}{{ .Release.Name | trunc 50 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-forge" .Release.Name | trunc 50 | trimSuffix "-" }}
{{- end -}}
{{- end -}}

{{- define "forge.labels" -}}
app.kubernetes.io/name: {{ include "forge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* forge.selector: (dict "root" . "component" "api") */}}
{{- define "forge.selector" -}}
app.kubernetes.io/name: {{ include "forge.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* forge.image: (dict "root" . "image" .Values.images.backend "tag" "") – registry prefix applied */}}
{{- define "forge.image" -}}
{{- $reg := .root.Values.global.imageRegistry -}}
{{- $tag := default .root.Chart.AppVersion .image.tag -}}
{{- $repo := .image.repository -}}
{{- if $reg -}}{{ printf "%s/%s:%s" (trimSuffix "/" $reg) $repo $tag }}{{- else -}}{{ printf "%s:%s" $repo $tag }}{{- end -}}
{{- end -}}

{{/* Third-party images carry their own tag (no appVersion default). */}}
{{- define "forge.thirdPartyImage" -}}
{{- $reg := .root.Values.global.imageRegistry -}}
{{- if $reg -}}{{ printf "%s/%s:%s" (trimSuffix "/" $reg) .image.repository (.image.tag | toString) }}{{- else -}}{{ printf "%s:%s" .image.repository (.image.tag | toString) }}{{- end -}}
{{- end -}}

{{- define "forge.secretName" -}}
{{- default (include "forge.fullname" .) .Values.existingSecret -}}
{{- end -}}

{{- define "forge.pullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Pod security. The fixed id is left out on OpenShift, whose restricted SCC assigns one. */}}
{{- define "forge.podSecurity" -}}
securityContext:
  runAsNonRoot: true
  {{- if not .root.Values.openshift }}
  runAsUser: {{ .uid }}
  runAsGroup: {{ .uid }}
  fsGroup: {{ .uid }}
  {{- end }}
  seccompProfile:
    type: RuntimeDefault
{{- end -}}

{{- define "forge.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: [ALL]
{{- end -}}

{{- define "forge.scheduling" -}}
{{- with .nodeSelector }}
nodeSelector: {{ toYaml . | nindent 2 }}
{{- end }}
{{- with .tolerations }}
tolerations: {{ toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* Service names of the dependencies. */}}
{{- define "forge.pg.host" -}}
{{- if .Values.postgresql.enabled -}}{{ include "forge.fullname" . }}-postgresql{{- else -}}{{ .Values.external.postgresql.host }}{{- end -}}
{{- end -}}
{{- define "forge.pg.port" -}}
{{- if .Values.postgresql.enabled -}}5432{{- else -}}{{ .Values.external.postgresql.port }}{{- end -}}
{{- end -}}
{{- define "forge.valkey.host" -}}
{{- if .Values.valkey.enabled -}}{{ include "forge.fullname" . }}-valkey{{- else -}}{{ .Values.external.valkey.host }}{{- end -}}
{{- end -}}
{{- define "forge.valkey.port" -}}
{{- if .Values.valkey.enabled -}}6379{{- else -}}{{ .Values.external.valkey.port }}{{- end -}}
{{- end -}}
{{- define "forge.s3.endpoint" -}}
{{- if .Values.seaweedfs.enabled -}}http://{{ include "forge.fullname" . }}-seaweedfs:8333{{- else -}}{{ .Values.external.s3.endpoint }}{{- end -}}
{{- end -}}
{{- define "forge.search.url" -}}
{{- if .Values.opensearch.enabled -}}https://{{ include "forge.fullname" . }}-opensearch:9200{{- else -}}{{ .Values.external.opensearch.url }}{{- end -}}
{{- end -}}
{{- define "forge.temporal.address" -}}
{{- if .Values.temporal.enabled -}}{{ include "forge.fullname" . }}-temporal:7233{{- else -}}{{ .Values.external.temporal.address }}{{- end -}}
{{- end -}}

{{/* Environment shared by every Forge process (api, workers). */}}
{{- define "forge.env" -}}
{{- $secret := include "forge.secretName" . -}}
- name: FORGE_DATABASE_URL
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: database-url } }
- name: FORGE_MASTER_KEY
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: master-key } }
- name: FORGE_ADMIN_USERNAME
  value: {{ .Values.config.adminUsername | quote }}
- name: FORGE_ADMIN_EMAIL
  value: {{ .Values.config.adminEmail | quote }}
- name: FORGE_ADMIN_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: admin-password } }
- name: FORGE_PUBLIC_URL
  value: {{ include "forge.publicUrl" . | quote }}
- name: FORGE_REDIS_URL
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: redis-url } }
- name: FORGE_S3_ENDPOINT
  value: {{ include "forge.s3.endpoint" . | quote }}
- name: FORGE_S3_BUCKET
  value: {{ if .Values.seaweedfs.enabled }}forge{{ else }}{{ .Values.external.s3.bucket | quote }}{{ end }}
- name: FORGE_S3_ACCESS_KEY
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: s3-access-key } }
- name: FORGE_S3_SECRET_KEY
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: s3-secret-key } }
- name: FORGE_TEMPORAL_ADDRESS
  value: {{ include "forge.temporal.address" . | quote }}
- name: FORGE_TEMPORAL_NAMESPACE
  value: {{ if .Values.temporal.enabled }}default{{ else }}{{ .Values.external.temporal.namespace | quote }}{{ end }}
- name: FORGE_SEARCH_URL
  value: {{ include "forge.search.url" . | quote }}
- name: FORGE_SEARCH_USERNAME
  value: {{ if .Values.opensearch.enabled }}forge{{ else }}{{ .Values.external.opensearch.username | quote }}{{ end }}
- name: FORGE_SEARCH_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: search-password } }
{{- if or .Values.opensearch.enabled .Values.external.opensearch.caSecret.name }}
- name: FORGE_SEARCH_CA_FILE
  value: /etc/forge/search-ca/ca.pem
{{- end }}
{{- if .Values.config.otlpEndpoint }}
- name: FORGE_OTLP_ENDPOINT
  value: {{ .Values.config.otlpEndpoint | quote }}
{{- end }}
- name: FORGE_METRICS_PORT
  value: "9100"
- name: FORGE_LOG_LEVEL
  value: {{ .Values.config.logLevel | quote }}
- name: FORGE_AGENT_ALLOW_PRIVATE_HOSTS
  value: {{ .Values.config.allowPrivateHosts | quote }}
- name: FORGE_TOOLS_ALLOW_PRIVATE_HOSTS
  value: {{ .Values.config.allowPrivateHosts | quote }}
- name: FORGE_MCP_STDIO_ALLOWED
  value: {{ .Values.config.mcpStdioAllowed | quote }}
- name: FORGE_SOURCE_ROOTS
  value: {{ .Values.config.sourceRoots | quote }}
{{- if .Values.sandbox.url }}
- name: FORGE_SANDBOX_URL
  value: {{ .Values.sandbox.url | quote }}
- name: FORGE_SANDBOX_TOKEN
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: sandbox-token } }
{{- end }}
{{- with .Values.smtp }}
{{- if .host }}
- name: FORGE_SMTP_HOST
  value: {{ .host | quote }}
- name: FORGE_SMTP_PORT
  value: {{ .port | quote }}
- name: FORGE_SMTP_USERNAME
  value: {{ .username | quote }}
- name: FORGE_SMTP_FROM
  value: {{ .from | quote }}
- name: FORGE_SMTP_STARTTLS
  value: {{ .starttls | quote }}
{{- if .passwordKey }}
- name: FORGE_SMTP_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: {{ .passwordKey }} } }
{{- end }}
{{- end }}
{{- end }}
{{- with .Values.proxy }}
{{- if or .http .https }}
- name: HTTP_PROXY
  value: {{ .http | quote }}
- name: HTTPS_PROXY
  value: {{ .https | quote }}
{{- end }}
{{- end }}
- name: NO_PROXY
  value: {{ $f := include "forge.fullname" . }}{{ printf "%s%s-postgresql,%s-valkey,%s-seaweedfs,%s-opensearch,%s-temporal,%s-api,%s-web,.svc,.cluster.local,localhost,127.0.0.1" (ternary (printf "%s," .Values.proxy.noProxy) "" (ne .Values.proxy.noProxy "")) $f $f $f $f $f $f $f | quote }}
{{- if .Values.extraCA.secretName }}
- name: FORGE_EXTRA_CA_FILE
  value: /etc/forge/extra-ca/{{ .Values.extraCA.key }}
{{- end }}
{{- with .Values.config.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "forge.volumes" -}}
- name: tmp
  emptyDir: {}
{{- if .Values.opensearch.enabled }}
- name: search-ca
  secret:
    secretName: {{ include "forge.fullname" . }}-search-tls
    items: [{ key: ca.pem, path: ca.pem }]
{{- else if .Values.external.opensearch.caSecret.name }}
- name: search-ca
  secret:
    secretName: {{ .Values.external.opensearch.caSecret.name }}
    items: [{ key: {{ .Values.external.opensearch.caSecret.key }}, path: ca.pem }]
{{- end }}
{{- if .Values.extraCA.secretName }}
- name: extra-ca
  secret: { secretName: {{ .Values.extraCA.secretName }} }
{{- end }}
{{- end -}}

{{- define "forge.volumeMounts" -}}
- { name: tmp, mountPath: /tmp }
{{- if or .Values.opensearch.enabled .Values.external.opensearch.caSecret.name }}
- { name: search-ca, mountPath: /etc/forge/search-ca, readOnly: true }
{{- end }}
{{- if .Values.extraCA.secretName }}
- { name: extra-ca, mountPath: /etc/forge/extra-ca, readOnly: true }
{{- end }}
{{- end -}}

{{- define "forge.publicUrl" -}}
{{- if .Values.config.publicUrl -}}{{ .Values.config.publicUrl }}
{{- else if .Values.ingress.enabled -}}{{ ternary "https" "http" (not (empty .Values.ingress.tls)) }}://{{ .Values.ingress.host }}
{{- else if .Values.route.enabled -}}https://{{ .Values.route.host }}
{{- else -}}http://localhost:8080
{{- end -}}
{{- end -}}

{{- define "forge.pick" -}}
{{- if .given -}}{{ .given }}{{- else if hasKey .old .key -}}{{ index .old .key | b64dec }}{{- else -}}{{ randAlphaNum (int .len) }}{{- end -}}
{{- end -}}

{{/* WAL-G settings of the PITR containers. */}}
{{- define "forge.pitr.env" -}}
{{- $secret := include "forge.secretName" . -}}
{{- $p := .Values.postgresql.pitr -}}
- { name: WALG_S3_PREFIX, value: {{ printf "s3://%s/pg" $p.s3.bucket | quote }} }
- { name: AWS_ENDPOINT, value: {{ default (include "forge.s3.endpoint" .) $p.s3.endpoint | quote }} }
- { name: AWS_S3_FORCE_PATH_STYLE, value: "true" }
- { name: AWS_REGION, value: us-east-1 }
- name: AWS_ACCESS_KEY_ID
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: {{ ternary "pitr-s3-access-key" "s3-access-key" (ne $p.s3.accessKey "") }} } }
- name: AWS_SECRET_ACCESS_KEY
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: {{ ternary "pitr-s3-secret-key" "s3-secret-key" (ne $p.s3.accessKey "") }} } }
- name: WALG_LIBSODIUM_KEY
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: pitr-encryption-key } }
- { name: WALG_LIBSODIUM_KEY_TRANSFORM, value: hex }
{{- end -}}
