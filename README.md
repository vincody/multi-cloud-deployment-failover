# DCS29 Multi-Cloud Failover Dashboard

Project #29 runs one application on AWS EC2 (primary) and an Azure Ubuntu VM (standby). Route 53 switches the shared hostname to the healthy origin during failover.

## Current state

This repository contains the app, CI image workflow, and step-by-step guides for AWS EC2 primary, Azure VM standby, HTTPS, Route 53 failover, and laptop-based monitoring. The dashboard shows the serving cloud, region, uptime, and `/api/devices` workload metrics from the last 60 seconds. Every deployment must create its **own** cloud resources and use its **own** domain, IPs, and image digest. Start with the [deployment worksheet](docs/giai-doan/THONG_SO_TRIEN_KHAI.md), then follow the [stages](docs/giai-doan/README.md).

## Project layout and planned artifacts

```text
app/                 Application source, UI, sample data, and metrics
deploy/aws/          EC2, NGINX, and Docker Compose configuration
deploy/azure/        Azure VM configuration or scripts, if added
deploy/dns/          Route 53 configuration notes or infrastructure code
observability/       Prometheus, Blackbox Exporter, and Grafana assets
tests/load/          k6 traffic and failover scenarios
experiments/         Run manifests, raw results, and analysis artifacts
docs/                Project analysis and supporting documentation
.github/workflows/   CI workflows
```

## Documentation

- [Architecture and workflow](images/ARCHITECTURE.md)
- [AWS Console deployment](docs/giai-doan/GIAI_DOAN_03_AWS_CONSOLE.md)
- [Azure Portal deployment and verification](docs/giai-doan/GIAI_DOAN_04_AZURE_PORTAL.md)
- [Implementation stages and current progress](docs/giai-doan/README.md)
- [Original planning guide (contains superseded Container Apps steps)](HUONG_DAN_29_AWS_AZURE_DEPLOY_VA_KIEM_THU.md)

## Run locally

Create the local Python environment and install the test/runtime dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Start the dashboard, then open `http://127.0.0.1:8080` in a browser:

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Run the automated API checks:

```powershell
.\.venv\Scripts\python -m pytest -q
```

Useful endpoints: `/api/status`, `/api/devices`, `/health/live`, `/health/ready`, `/version`, and `/metrics`.

The dashboard polls `/api/status` every five seconds. Request rate, app error rate, and app p95 latency cover `/api/devices` traffic only; an idle app shows no workload error rate or p95 value. Browser-to-API latency is measured separately in the browser.

## Build and publish the container

Build and smoke-test a Linux AMD64 image locally:

```powershell
docker build --platform linux/amd64 -t dcs29-local:check .
docker run --rm -d --name dcs29-check -p 18080:8080 dcs29-local:check
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18080/health/ready
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18080/version
docker stop dcs29-check
```

Pushing a commit to `main` runs [the container workflow](.github/workflows/container.yml): API tests, a Linux AMD64 build, publication to `ghcr.io/<your-owner>/<your-repo>`, and a clean-pull runtime check. The workflow publishes `main` and `sha-<commit>` tags and records the immutable image digest as a run artifact. Fill `GHCR_IMAGE` and `IMAGE_DIGEST` in the deployment worksheet from **your run**, then deploy the **same digest** to AWS and Azure; do not use a mutable tag as evidence that both origins run identical code.

Do not commit `.env` files, cloud credentials, private keys, certificates, or Terraform state. The repository ignores common secret and local-state paths, but review staged files before every push.
