{{/*
Resource name. Derived from nameOverride rather than the release name so the
name is fixed and predictable: CI, the ingress host and any kubectl selector
all have to agree on it, and a release-derived name would drift if someone ran
`helm install` under a different release name.
*/}}
{{- define "genetrek.fullname" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "genetrek.labels" -}}
app.kubernetes.io/name: {{ include "genetrek.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* Only the labels that select pods: these must never change for a live
Deployment, since spec.selector is immutable. */}}
{{- define "genetrek.selectorLabels" -}}
app.kubernetes.io/name: {{ include "genetrek.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* The visit counter, beside the application and named after it. */}}
{{- define "genetrek.goatcounter.fullname" -}}
{{- printf "%s-goatcounter" (include "genetrek.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "genetrek.goatcounter.labels" -}}
app.kubernetes.io/name: {{ include "genetrek.goatcounter.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: visit-counter
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* A name of its own, so the application's Service never selects it. */}}
{{- define "genetrek.goatcounter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "genetrek.goatcounter.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
