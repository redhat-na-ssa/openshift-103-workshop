# Sample app

The application that runs through Modules 2 to 4 of the OpenShift Cloud
Workshop. It lists the objects in an S3 bucket (ROSA) or a Blob
container (ARO), using the cloud credentials the pod is given, and
exposes Prometheus metrics.

| Image | Use |
|-------|-----|
| `ghcr.io/redhat-na-ssa/openshift-cloud-workshop/sample-app:1.0` | Everywhere. Runs under any user ID. |
| `ghcr.io/redhat-na-ssa/openshift-cloud-workshop/sample-app:1.0-root` | Module 2 only. Expects root, so it fails under OpenShift's default SCC. |

Both images are built from `Containerfile` by the
`Sample App Image` workflow on every push to `main` that changes this
folder. The only difference between them is the last stage of the
Containerfile.

## Interface

- HTTP on port 8080: `/` (JSON object listing), `/healthz`, `/metrics`.
- `sample-app list` and `sample-app whoami` print the listing or the
  cloud identity and exit, for use with `oc exec`.
- One JSON log line per request on stdout.

| Variable | Meaning |
|----------|---------|
| `BUCKET` | S3 bucket to list (AWS) |
| `AWS_REGION` | Region of the bucket (AWS) |
| `STORAGE_ACCOUNT` | Storage account to list (Azure) |
| `CONTAINER` | Blob container to list (Azure, default `sample-app`) |
| `STATE_DIR` | Writable directory for the listing cache |

The image ignores node identities (EC2 instance metadata and Azure
managed identity through IMDS), so it only ever uses credentials given
to the pod: IRSA-style web identity on ROSA, workload identity on ARO.

## Metrics

- `sample_app_requests_total{path,code}`
- `sample_app_request_duration_seconds{path}`
- `sample_app_object_store_errors_total`
