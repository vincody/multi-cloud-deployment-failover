# DCS29 Multi-Cloud Failover Dashboard

Repository scaffold for project #29: the same service will run on AWS as the primary environment and Azure Container Apps as the standby environment. Route 53 will perform active-passive DNS failover.

## Current state

Phase 1 is complete locally: it contains a read-only dashboard, health/status APIs, sample lab-equipment data, Prometheus metrics, automated API tests, and a Dockerfile prepared for the next stage. No cloud resources have been created.

## Planned layout

```text
app/                 Application source, UI, sample data, and metrics
deploy/aws/          EC2, NGINX, and Docker Compose configuration
deploy/azure/        Azure Container Apps configuration and scripts
deploy/dns/          Route 53 configuration notes or infrastructure code
observability/       Prometheus, Blackbox Exporter, and Grafana assets
tests/load/          k6 traffic and failover scenarios
experiments/         Run manifests, raw results, and analysis artifacts
docs/                Project analysis and supporting documentation
.github/workflows/   CI workflows
```

## Documentation

- [Multi-cloud deployment and failover analysis](docs/PHAN_TICH_DE_TAI_29_MULTI_CLOUD_FAILOVER.md)
- [DevOps topic analysis](docs/GOI_Y_CHON_DE_TAI.md)
- [AWS + Azure deployment and verification guide](HUONG_DAN_29_AWS_AZURE_DEPLOY_VA_KIEM_THU.md)
- [Implementation stages and current progress](docs/giai-doan/README.md)

## Run Phase 1 locally

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
