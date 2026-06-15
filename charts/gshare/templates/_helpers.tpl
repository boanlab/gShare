{{/* Common labels / image helpers */}}

{{- define "gshare.labels" -}}
app.kubernetes.io/part-of: gshare
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Component selector labels */}}
{{- define "gshare.selectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/part-of: gshare
{{- end -}}

{{/*
Fully-qualified image ref.
Usage: {{ include "gshare.image" (dict "img" .Values.images.api "root" $) }}
The canonical registry host is registry.gshare.internal.
*/}}
{{- define "gshare.image" -}}
{{- $reg := .root.Values.global.imageRegistry -}}
{{- printf "%s/%s:%s" $reg .img.repository .img.tag -}}
{{- end -}}

{{- define "gshare.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{ toYaml . | indent 2 }}
{{- end }}
{{- end -}}

{{/* PSA restricted securityContext (pod + container) */}}
{{- define "gshare.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 1000
fsGroup: 1000
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "gshare.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}

{{/*
The in-cluster service account token, as a TokenRequest projection.

The controller service account sets automountServiceAccountToken to false, so api and worker receive
their token through an explicit projected volume at the standard in-cluster path. This is what
load_incluster_config() falls back to during the CRD-primary handoff when the control plane is
co-located.

For a remote target cluster, external-secrets projects a kubeconfig instead. The worker performs the
same handoff when it dequeues, so it mounts exactly what the api does.
*/}}
{{- define "gshare.controllerTokenVolumeMount" -}}
- name: sa-token
  mountPath: /var/run/secrets/kubernetes.io/serviceaccount
  readOnly: true
{{- end -}}

{{- define "gshare.controllerTokenVolume" -}}
- name: sa-token
  projected:
    sources:
      - serviceAccountToken:
          path: token
          expirationSeconds: 3600
      - configMap:
          name: kube-root-ca.crt
          items:
            - { key: ca.crt, path: ca.crt }
      - downwardAPI:
          items:
            - path: namespace
              fieldRef: { fieldPath: metadata.namespace }
{{- end -}}
