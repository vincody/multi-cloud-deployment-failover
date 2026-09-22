# Hướng dẫn đề tài #29: Multi-Cloud Deployment & Failover trên AWS và Azure

Ngày lập: 22/09/2026.

## 1. Phương án chốt và phạm vi tài liệu

Xây một website tra cứu thiết bị phòng lab, chạy cùng phiên bản trên AWS EC2 và Azure Container Apps. AWS phục vụ chính; Azure chạy sẵn để tiếp quản khi AWS không truy cập được. Route 53 thực hiện DNS failover. Prometheus/Grafana quan sát hệ thống, k6 tạo tải và lưu kết quả thí nghiệm.

Giai đoạn 1 của hướng dẫn này đã được hiện thực và kiểm tra cục bộ: dashboard read-only, các API health/status, dữ liệu thiết bị mẫu, Prometheus metrics và API tests nằm trong `app/` và `tests/`. Các phần triển khai AWS, Azure, Route 53, domain/HTTPS và thí nghiệm failover phía dưới vẫn là kế hoạch có placeholder; chưa có tài nguyên cloud nào được tạo.

Giả định nhóm 3-4 người, biết AWS cơ bản, có 6-8 tuần. Chưa xác định ngân sách nên ưu tiên stack nhỏ và chỉ chạy thí nghiệm trong thời gian đã lên lịch.

**Bản đầu tiên hoàn chỉnh:** ứng dụng read-only, hai cloud thật, một domain chung, HTTPS hợp lệ ở cả hai bên, failover tự động, dữ liệu thí nghiệm lặp lại được. Không cần Kubernetes để triển khai phương án này.

Lưu ý về môn học: PDF liệt kê Kubernetes trong Tools và ghi riêng Terraform optional. Không thể từ đó khẳng định chắc chắn mọi công cụ còn lại đều tùy chọn. Nhóm nên xác nhận với giảng viên việc thay Kubernetes bằng EC2/Docker và Container Apps trước khi đăng ký stack chính thức.

## 2. Đối chiếu yêu cầu đề tài

| Yêu cầu trong PDF | Cách thực hiện | Bằng chứng nộp |
|---|---|---|
| Cùng ứng dụng ở hai môi trường độc lập | AWS EC2 và Azure Container Apps, cùng image digest | Deployment config, kết quả /version |
| Health monitoring và traffic switching | Route 53 kiểm tra riêng từng origin, failover records | Cấu hình DNS, trạng thái health check |
| Mô phỏng một môi trường ngừng hoạt động hoàn toàn | Stop EC2 duy nhất chứa toàn bộ app AWS | Sự kiện stop, origin AWS mất kết nối, Azure vẫn phục vụ |
| Đo failover time, downtime, latency | k6, probe liên tục, timeline UTC | Dữ liệu thô, bảng và biểu đồ |
| Simple cost proxy | Giờ chạy, CPU/RAM, dung lượng, số request | Bảng tài nguyên và chi phí |
| Operational complexity và trade-off | Phân tích DNS, TLS, dữ liệu, billing, deployment | Phần thảo luận trong báo cáo |

Ngoài yêu cầu riêng #29, cần baseline, script chạy lại được, mô tả workload, biểu đồ và giải thích kết quả theo phần deliverables chung trong PDF.

Tắt EC2 là mô phỏng mất toàn bộ môi trường AWS của đồ án, không phải bằng chứng nhóm đã gây hay kiểm thử một sự cố toàn AWS Region.

## 3. Sản phẩm nhóm sẽ trình diễn

Website có danh sách thiết bị, ô tìm kiếm và trang chi tiết. Dữ liệu mẫu khoảng vài trăm thiết bị, hoàn toàn giả lập. Giao diện hiển thị môi trường phục vụ response gần nhất, version và thời gian nhận response.

Frontend dùng HTML/CSS/JavaScript đơn giản và được phục vụ cùng backend. JavaScript gọi URL tương đối như `/api/devices`, không hard-code hostname AWS. Khi failover, frontend và API đều có sẵn ở Azure. Gắn `Cache-Control: no-store` cho response dùng đo đạc, tránh cache hoặc service worker che mất sự cố.

| Endpoint | Mục đích | Kết quả cần có |
|---|---|---|
| GET / | Web UI | HTML cùng phiên bản ở hai cloud |
| GET /health/live | Process có phản hồi không | 200 khi process hoạt động |
| GET /health/ready | Có thể phục vụ nghiệp vụ không | 200 khi dữ liệu đã tải, 503 khi chưa sẵn sàng |
| GET /api/devices | Workload chính | Danh sách thiết bị và thông tin environment |
| GET /version | Kiểm tra release | environment, version, commit_sha, dataset_sha256 |
| GET /metrics | Thu metrics ứng dụng | Counter và histogram Prometheus |

Response API ví dụ:

```json
{
  "environment": "aws-primary",
  "version": "v1.0.0",
  "devices": [{"id": "LAB-001", "name": "PC 01", "status": "available"}]
}
```

Không cần đăng nhập, upload hay ghi database cho bản đầu. Mỗi deployment chứa cùng dataset trong image, nên Azure không cần gọi AWS để trả kết quả.

## 4. Công nghệ và những gì cần deploy

| Thành phần | Lựa chọn | Nơi chạy | Vai trò |
|---|---|---|---|
| Backend | Python FastAPI + Uvicorn | AWS và Azure | API, health, metrics, phục vụ UI |
| Frontend | HTML/CSS/JavaScript | Cùng backend | Demo trực quan |
| Đóng gói | Docker, Linux AMD64 | Local, CI, cloud | Build một lần, deploy hai nơi |
| Registry | GHCR | GitHub | Phân phối image |
| Compute chính | EC2 Ubuntu x86_64 | AWS Singapore | Docker Compose chạy app |
| Reverse proxy | NGINX | EC2 | HTTPS, chuyển request vào app |
| Compute dự phòng | Container Apps Consumption | Azure, region hỗ trợ phù hợp | Chạy container và ingress HTTPS |
| DNS | Route 53 public hosted zone | AWS | Domain và failover records |
| Chứng chỉ | ACME DNS-01, ví dụ Let's Encrypt | Cấp qua DNS, cài ở hai origin | HTTPS cho domain chung |
| Quan sát | Prometheus + Grafana + Blackbox Exporter | Máy nhóm hoặc máy quan sát riêng | Theo dõi origins và domain người dùng |
| Load test | k6 | Máy ngoài EC2 bị tắt | Đo tác động lên người dùng |
| Tự động hóa | GitHub Actions; Terraform tùy chọn | GitHub/local | Build, deploy và tái lập hạ tầng |

Baseline không cần ALB, EKS, AKS, NAT Gateway, managed database hay Azure Container Registry. EC2 nhỏ chỉ chạy app/proxy; không nhồi toàn bộ monitoring vào máy đó.

## 5. Kiến trúc và nguyên tắc độc lập

Route 53 là DNS: client hỏi DNS rồi kết nối trực tiếp tới origin được trả về. HTTP request không đi xuyên qua Route 53.

| Lớp | Cấu hình |
|---|---|
| URL người dùng | https://app.YOUR_DOMAIN |
| Origin AWS | aws-origin.YOUR_DOMAIN, A record tới Elastic IP EC2 |
| Origin Azure | FQDN mặc định của Container App |
| Khi AWS healthy | app.YOUR_DOMAIN phân giải tới origin AWS |
| Khi AWS unhealthy, Azure healthy | DNS mới trả origin Azure |
| Quan sát | Theo dõi riêng AWS, Azure và domain chung |

Azure cần đủ container image, cấu hình, certificate và dữ liệu để phục vụ khi EC2 dừng. Không đặt một database, Redis session hay file server dùng chung chỉ trên EC2 AWS rồi gọi kiến trúc đó là chịu lỗi toàn môi trường.

Monitoring và load generator cũng phải ở ngoài EC2 AWS. Registry là phụ thuộc khi pull/redeploy; thử nghiệm failover dùng Azure đã chạy sẵn để không phải pull image giữa sự cố.

## 6. Chuẩn bị trước khi bắt đầu

1. Tạo repository nhóm và phân công người quản lý cloud, app, monitoring, thí nghiệm.
2. Chuẩn bị AWS account, Azure subscription và quyền tạo tài nguyên cần thiết.
3. Chuẩn bị domain hoặc subdomain được ủy quyền DNS cho nhóm. Không bắt buộc mua domain tại AWS.
4. Đặt budget alert trên cả hai cloud. Alert không phải hard cap tự chặn chi phí.
5. Cài Git, Docker Desktop, AWS CLI, Azure CLI, k6; dùng WSL2 Ubuntu để thống nhất shell.
6. Đặt tên tài nguyên theo tiền tố `dcs29-`, gắn tag project/owner để dễ kiểm kê và dọn.
7. Ghi lại region, resource IDs và người sở hữu; không đưa password, private key, token hoặc certificate private key vào Git.

Các lệnh bên dưới dùng **Bash trong WSL/Ubuntu**, không dán nguyên cú pháp xuống dòng vào PowerShell. Các địa chỉ `YOUR_DOMAIN`, `YOUR_ORG`, IP và digest là placeholder.

## 7. Giai đoạn 1: viết và kiểm tra ứng dụng local

### 7.1 Cấu trúc repository đề xuất

```text
app/                 # FastAPI, UI và dữ liệu mẫu
Dockerfile
requirements.txt     # Pin version sau khi kiểm thử
deploy/aws/          # Compose, NGINX và hướng dẫn EC2
deploy/azure/        # Cấu hình hoặc script Container Apps
deploy/dns/          # Bản mô tả/export Route 53, không chứa secret
observability/       # Prometheus, Blackbox Exporter, Grafana dashboards
tests/load/          # k6 scenarios
experiments/         # run manifest, timeline, dữ liệu kết quả
docs/                # Kiến trúc, runbook, report
.github/workflows/   # Build/test/deploy tùy giai đoạn
README.md
```

### 7.2 Cách làm app

- Bind Uvicorn vào `0.0.0.0:8080` để container ingress truy cập được.
- Đọc `APP_ENV`, `APP_VERSION`, `COMMIT_SHA` từ biến môi trường.
- Tải dataset khi start; readiness chỉ trả 200 sau khi tải thành công.
- Thêm request counter theo method/route/status và histogram thời gian xử lý. Không dùng request ID hoặc ID từng thiết bị làm metric label vì tạo quá nhiều chuỗi dữ liệu.
- Log JSON có UTC timestamp, request ID, environment, route, status, duration.
- Xây cùng một image cho AWS/Azure, thay environment qua cấu hình.

### 7.3 Kiểm tra local

```bash
docker build -t dcs29-app:local .
docker run --rm -p 8080:8080 -e APP_ENV=local dcs29-app:local
```

Ở terminal khác:

```bash
curl -f http://localhost:8080/health/ready
curl -f http://localhost:8080/version
curl -f http://localhost:8080/api/devices
```

**Đạt giai đoạn:** UI dùng được, API đúng dữ liệu, readiness đúng trạng thái, metrics tăng khi có request. Restart container không làm mất dữ liệu mẫu.

## 8. Giai đoạn 2: build image dùng chung

Tạo workflow GitHub Actions chạy test ứng dụng, build Linux AMD64 và push GHCR với tag theo commit. Ghi lại digest `sha256:...`; hai cloud deploy đúng digest này thay vì `latest`.

Nếu build thủ công trên máy nhóm đã login registry:

```bash
docker buildx build --platform linux/amd64 \
  -t ghcr.io/YOUR_ORG/dcs29-app:v1.0.0 --push .
```

Chọn EC2 x86_64 để cùng kiến trúc với image đề xuất. Image public chỉ phù hợp nếu không chứa dữ liệu hoặc mã nguồn riêng tư; private registry cần cấu hình credential pull riêng ở cả hai cloud.

**Đạt giai đoạn:** máy sạch pull được image theo quyền đã thiết lập; AWS và Azure sẽ dùng cùng digest/dataset.

## 9. Giai đoạn 3: deploy AWS EC2

### 9.1 Tạo hạ tầng

1. Chọn AWS Singapore (`ap-southeast-1`).
2. Tạo EC2 Ubuntu x86_64. Bắt đầu cấu hình nhỏ và đo RAM/CPU; nếu thiếu tài nguyên mới tăng, không dùng instance quá yếu khiến benchmark chỉ phản ánh nghẽn máy.
3. Đặt EC2 trong public subnet có route ra Internet Gateway.
4. Gán Elastic IP ổn định để stop/start EC2 không đổi địa chỉ origin đã dùng cho DNS.
5. Security Group: 443 phục vụ ứng dụng; SSH 22 chỉ từ IP quản trị nếu dùng SSH. Không mở trực tiếp port backend 8080 ra Internet.
6. Cài Docker Engine và Compose plugin theo tài liệu Docker cho Ubuntu. Cấu hình Docker tự khởi động cùng máy.

### 9.2 Deploy container

Tạo Compose gồm `app` và `nginx` chung network nội bộ. App expose 8080 nội bộ; NGINX publish 443, proxy tới `app:8080`, hỗ trợ `server_name app.YOUR_DOMAIN aws-origin.YOUR_DOMAIN`.

Đặt app và NGINX `restart: unless-stopped`. Mount config/certificate read-only. Bảo vệ `/metrics` bằng auth hoặc allowlist IP máy quan sát; không yêu cầu login trên readiness dùng cho health check.

```bash
# Chạy tại thư mục chứa compose.yaml trên EC2
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail 100 app
```

Certificate và domain thiết lập ở mục 11. Trong lúc chưa có HTTPS, xác minh backend bằng curl trong VM, chưa coi deployment đã hoàn tất.

**Đạt giai đoạn:** origin AWS trả 200 qua HTTPS, có `environment=aws-primary`, không cần truy cập Azure để phục vụ.

## 10. Giai đoạn 4: deploy Azure Container Apps

Azure Container Apps chạy image, cấu hình ingress và replica qua dịch vụ quản lý; nhóm không cần dựng Kubernetes cluster.

### 10.1 Các tài nguyên cần tạo

- Resource Group: `dcs29-rg`.
- Container Apps Environment: `dcs29-env`.
- Container App: `dcs29-standby` dùng Consumption workload profile.
- Logging theo nhu cầu; ghi lại cấu hình retention và phí phát sinh.
- Public ingress tới container port 8080.

Lệnh minh họa; region cần kiểm tra availability/quota của subscription trước:

```bash
az login
az account set --subscription YOUR_SUBSCRIPTION_ID
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights

az group create --name dcs29-rg --location southeastasia
az containerapp env create --name dcs29-env \
  --resource-group dcs29-rg --location southeastasia

az containerapp create --name dcs29-standby \
  --resource-group dcs29-rg --environment dcs29-env \
  --image ghcr.io/YOUR_ORG/dcs29-app@sha256:YOUR_DIGEST \
  --workload-profile-name Consumption \
  --ingress external --target-port 8080 \
  --cpu 0.25 --memory 0.5Gi --min-replicas 1 --max-replicas 1 \
  --env-vars APP_ENV=azure-standby APP_VERSION=v1.0.0 COMMIT_SHA=YOUR_SHA

az containerapp show --name dcs29-standby --resource-group dcs29-rg \
  --query properties.configuration.ingress.fqdn --output tsv
```

Đợi provider registration hoàn tất trước khi tạo môi trường. Nếu region/CLI/account không chấp nhận cấu hình, kiểm tra thông báo và tạo qua Portal với cùng các thuộc tính; không tự chọn Dedicated profile để vượt lỗi quota. Private GHCR image cần bổ sung registry credentials, ví dụ qua Portal secrets.

Tham khảo cú pháp hiện hành: [Azure Container Apps CLI](https://learn.microsoft.com/en-us/cli/azure/containerapp?view=azure-cli-latest).

### 10.2 Kiểm tra Azure riêng

Truy cập FQDN Azure vừa trả về: `/health/ready`, `/version`, `/api/devices`. So version và hash dataset với AWS. Cấu hình readiness/liveness probes cho container theo endpoint ứng dụng.

Giữ **minReplicas=1, maxReplicas=1** cho baseline để standby sẵn và không trộn autoscaling vào kết quả. Đây không phải cam kết miễn phí. Scale-to-zero là thí nghiệm riêng; health check HTTP liên tục có thể làm app thức hoặc khó scale về 0.

**Đạt giai đoạn:** Azure phục vụ độc lập khi tạm ngừng ứng dụng AWS, kể cả qua URL Azure trực tiếp.

## 11. Giai đoạn 5: domain và HTTPS ở cả hai cloud

### 11.1 Vì sao không chỉ tạo DNS record là đủ?

Client gọi `https://app.YOUR_DOMAIN` thì Host header và TLS SNI vẫn là `app.YOUR_DOMAIN`, kể cả DNS trỏ sang FQDN Azure. Vì thế Azure cần đăng ký custom domain đó và có certificate hợp lệ. AWS cũng phải phục vụ HTTPS cho cùng hostname.

### 11.2 Cách triển khai

1. Tạo Route 53 public hosted zone cho domain nhóm sở hữu; cập nhật nameserver hoặc ủy quyền subdomain phù hợp.
2. Tạo `aws-origin.YOUR_DOMAIN` là A record tới Elastic IP.
3. Cấp certificate cho `app.YOUR_DOMAIN` bằng ACME DNS-01; có thể cấp riêng từng origin. AWS cần thêm certificate cho hostname origin dùng kiểm tra.
4. Cài certificate/key và full chain cho NGINX AWS. Không dùng certificate self-signed trong thí nghiệm HTTPS người dùng.
5. Với Azure, chọn Custom domains -> Bring your own certificate, import PFX và bind hostname `app.YOUR_DOMAIN`.
6. Tạo TXT `asuid.app` với mã xác minh Azure cung cấp. Trong giai đoạn xác minh, có thể tạm dùng CNAME trực tiếp `app` tới FQDN Azure; khi binding xong mới thay bằng failover records.
7. Giữ TXT xác minh; bảo vệ private key; ghi lại ngày hết hạn và quy trình gia hạn/cập nhật cả hai origin.

Azure yêu cầu custom domain gắn certificate; hướng dẫn import/bind chính thức: [Custom domains và certificate tự cung cấp](https://learn.microsoft.com/en-us/azure/container-apps/custom-domains-certificates). Không mặc định certificate Azure tự quản sẽ gia hạn được nếu DNS thường xuyên ưu tiên AWS; cần kiểm tra điều kiện xác minh trong [tài liệu managed certificates](https://learn.microsoft.com/en-us/azure/container-apps/custom-domains-managed-certificates).

### 11.3 Kiểm tra trước khi bật failover

```bash
curl --resolve app.YOUR_DOMAIN:443:AWS_ELASTIC_IP \
  https://app.YOUR_DOMAIN/version

curl --connect-to app.YOUR_DOMAIN:443:AZURE_APP_FQDN:443 \
  https://app.YOUR_DOMAIN/version
```

Hai lệnh phải thành công **không dùng `-k`**, trả đúng environment tương ứng. Chúng ép kết nối origin để kiểm tra Host/SNI, không dùng làm bài đo failover DNS.

**Đạt giai đoạn:** cùng hostname HTTPS phục vụ đúng trên cả hai cloud.

## 12. Giai đoạn 6: Route 53 health checks và failover

### 12.1 Health check từng origin

| Thuộc tính | AWS | Azure |
|---|---|---|
| Target | aws-origin.YOUR_DOMAIN | FQDN mặc định của Container App |
| Protocol/port | HTTPS/443 | HTTPS/443 |
| Path | /health/ready | /health/ready |
| Interval khởi đầu | 30 giây | 30 giây |
| Failure threshold khởi đầu | 3 | 3 |
| TLS | Cấu hình SNI nếu cần | Bật SNI phù hợp hostname Azure |

Không health-check `app.YOUR_DOMAIN` cho cả hai origin: tên đó tự đổi đích và không cho biết riêng AWS hay Azure khỏe. Readiness cần phản ánh phục vụ nghiệp vụ, không chỉ trả hằng số 200 bất kể app lỗi.

Route 53 tổng hợp kết quả từ nhiều checker. Không hứa failover đúng 30 × 3 = 90 giây; phải đo. [Cấu hình health check](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-creating-values.html).

### 12.2 Hai record cùng tên, cùng loại

Sử dụng subdomain `app`, không dùng CNAME tại apex domain.

| Thuộc tính | Record chính | Record dự phòng |
|---|---|---|
| Name | app.YOUR_DOMAIN | app.YOUR_DOMAIN |
| Type | CNAME | CNAME |
| Routing policy | Failover | Failover |
| Role | Primary | Secondary |
| Value | aws-origin.YOUR_DOMAIN | AZURE_APP_FQDN |
| Health check | AWS check | Azure check |
| Set identifier | aws-primary | azure-secondary |
| TTL khởi đầu | 30 giây | 30 giây |

Xóa/thay CNAME simple dùng khi xác minh để không xung đột với failover records. Không trộn một record A primary với một CNAME secondary cho cùng nhóm failover.

DNS failover thay đổi câu trả lời DNS, không chuyển một TCP connection đang mở sang cloud khác. Primary healthy trở lại có thể được DNS chọn lại tự động; cần quan sát failback. [Route 53 active-passive](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-types.html).

**Đạt giai đoạn:** bình thường domain chung trả AWS; AWS app ngừng phục vụ thì sau khoảng phát hiện/cache, client tạo kết nối mới nhận Azure.

## 13. Giai đoạn 7: monitoring và dữ liệu quan sát

Chạy monitoring trên laptop nhóm trong buổi test hoặc một máy độc lập. Đảm bảo máy không sleep và đồng bộ thời gian. Nếu scrape `/metrics` qua Internet, có auth/allowlist và cấu hình tương ứng trong Prometheus.

| Nguồn | Metric/dữ liệu | Mục đích |
|---|---|---|
| FastAPI | Request count, histogram duration | Tải và latency ở origin |
| Blackbox Exporter | Probe success, duration, HTTP status | Kiểm tra bên ngoài cho AWS, Azure, domain chung |
| Route 53/CloudWatch | Health check status | Thời điểm DNS service đánh dấu lỗi |
| k6 | Request status, duration, environment | Trải nghiệm từ phía người dùng |
| Script sự cố | UTC timestamp, resource ID, thao tác | Biết chính xác tác động nào đã thực hiện |

Prometheus local không tự có metric Route 53. Nếu chưa tích hợp CloudWatch exporter, lấy health status bằng Console/API và lưu timeline riêng.

Grafana nên có: health A/B, traffic theo environment, latency p95/p99, error rate, annotation thời điểm gây lỗi/khôi phục. EC2 chết thì app AWS không xuất được metrics; dữ liệu k6 và external probe mới ghi lại đầy đủ request thất bại.

Lưu ý `up` của Prometheus là khả năng scrape; không đồng nghĩa ứng dụng sẵn sàng phục vụ nghiệp vụ. Readiness, scrape và synthetic probe là các tín hiệu khác nhau.

## 14. Giai đoạn 8: load test bằng k6

### 14.1 Workload cơ bản

10 request/giây trong 10 phút: phút 0-3 baseline, phút 3 gây lỗi, phút 3-7 quan sát failover, phút 7 khôi phục AWS, phút 7-10 quan sát recovery/failback. Nếu DNS/khởi động lâu hơn thì kéo dài test.

Ví dụ script `tests/load/failover.js` để nhóm tạo trong repo triển khai:

```javascript
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const served = new Counter('responses_by_environment');
const dnsExperiment = __ENV.DNS_EXPERIMENT === '1';

export const options = {
  scenarios: {
    requests: {
      executor: 'constant-arrival-rate',
      rate: 10,
      timeUnit: '1s',
      duration: '10m',
      preAllocatedVUs: 20,
      maxVUs: 100,
    },
  },
  ...(dnsExperiment
    ? { dns: { ttl: '5s', select: 'first', policy: 'preferIPv4' },
        noConnectionReuse: true }
    : {}),
};

export default function () {
  const res = http.get(`${__ENV.BASE_URL}/api/devices`, {
    timeout: '5s',
    headers: { 'Cache-Control': 'no-cache' },
    tags: { endpoint: 'devices' },
  });
  let environment = 'unknown';
  try { environment = res.json('environment') || 'unknown'; } catch (_) {}
  const ok = check(res, {
    'successful response from known origin': (r) =>
      r.status === 200 && ['aws-primary', 'azure-standby'].includes(environment),
  });
  served.add(1, { environment, success: String(ok) });
}
```

Chạy mỗi lần vào output có tên riêng:

```bash
k6 run -e BASE_URL=https://app.YOUR_DOMAIN \
  --out json=experiments/run-001-k6.json tests/load/failover.js
```

20 VU là mức cấp phát ban đầu, không phải bảo đảm đủ dưới mọi độ trễ. Ghi `dropped_iterations`: nếu k6 không phát đủ tải vì hết VU, phải báo riêng, không coi throughput đo được là tải đã gửi đầy đủ.

### 14.2 Hai kiểu đo DNS

- **Hành vi client mặc định:** để k6 dùng cấu hình kết nối/DNS mặc định, ghi rõ version và resolver. Đây là hành vi k6, không đại diện mọi trình duyệt.
- **Thí nghiệm DNS có kiểm soát:** chạy thêm `-e DNS_EXPERIMENT=1` để giảm cache nội bộ k6 và tạo kết nối mới. Resolver hệ điều hành/mạng vẫn có cache riêng; không thể ép mọi client chuyển ngay bằng tùy chọn này.

Không so sánh hai bài có connection reuse khác nhau rồi quy toàn bộ chênh lệch latency cho cloud. So baseline/failover trong cùng một cấu hình client. [k6 options: DNS và connection reuse](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/).

### 14.3 Probe người dùng và DNS song song

Viết probe mỗi giây gọi `/version` qua domain chung, lưu CSV: `started_at_utc, completed_at_utc, status, environment, duration_ms, error`. Không thêm query ID vào metric label; để ID trong log/CSV nếu cần.

Chạy `dig app.YOUR_DOMAIN CNAME` để theo dõi resolver; truy vấn trực tiếp authoritative nameserver riêng để phân biệt record AWS đã đổi nhưng resolver còn cache. Ghi lại cả hai nguồn và TTL thấy được.

## 15. Các kịch bản bắt buộc và thứ tự chạy

| Mã | Kịch bản | Cách làm | Kết quả cần phân tích |
|---|---|---|---|
| B0 | Single-cloud baseline | Domain test riêng chỉ trỏ AWS, cùng tải | Khi AWS chết không có nơi phục vụ |
| B1 | Multi-cloud bình thường | A/B sẵn sàng, traffic chính AWS | Latency, tài nguyên dự phòng |
| F1 | Lỗi process ứng dụng | Stop container app AWS qua SSH | DNS phát hiện và chuyển sang Azure |
| F2 | Mất toàn environment AWS | Stop EC2 chứa app/proxy | AWS unreachable, Azure tiếp tục phục vụ |
| F3 | Recovery/failback | Start EC2, đợi app ready | Traffic về AWS sau bao lâu, lỗi có tái diễn không |
| F4 | Tải cao khi failover | Tăng tải có kiểm soát rồi stop EC2 | Azure đủ công suất không, tail latency/error |
| F5 | Readiness lỗi nhưng process còn | Cấu hình thí nghiệm khiến readiness 503 | Phân biệt lỗi health với sự cố môi trường |

F2 là bài chính đáp ứng mô phỏng môi trường chết. F5 chỉ kiểm tra quyết định routing, không thay thế F2.

Chạy mỗi kịch bản ít nhất 3 lần như mức khởi đầu của nhóm; tăng số lần nếu kết quả dao động. Giữ image, tải, TTL và replica nhất quán, đợi cả hai origin healthy giữa các lượt. Ghi rõ cache nóng/lạnh; tránh chỉ xóa cache ở lượt bất lợi.

Thao tác F2/F3 mẫu, chỉ dùng với instance lab đã xác minh:

```bash
aws ec2 describe-instances --region ap-southeast-1 \
  --instance-ids YOUR_LAB_INSTANCE_ID
aws ec2 stop-instances --region ap-southeast-1 \
  --instance-ids YOUR_LAB_INSTANCE_ID

# Sau khi hoàn tất giai đoạn quan sát sự cố
aws ec2 start-instances --region ap-southeast-1 \
  --instance-ids YOUR_LAB_INSTANCE_ID
```

Ghi thời điểm gửi lệnh và thời điểm external probe xác nhận AWS mất phục vụ riêng biệt. Gửi stop không có nghĩa instance chết ngay tại thời điểm đó.

## 16. Cách tính và báo cáo kết quả

| Chỉ số | Định nghĩa dùng trong đồ án |
|---|---|
| t0 | Thời điểm lần đầu external probe xác nhận AWS không phục vụ sau tác động |
| t_detect | Thời điểm quan sát Route 53 đánh dấu primary unhealthy |
| t_first_B | Response thành công đầu tiên qua domain chung đến từ Azure |
| t_stable | Bắt đầu một chuỗi thành công ổn định, ví dụ 10 probe liên tiếp |
| Detection time | t_detect - t0; kèm sai số polling |
| Failover time | t_first_B - t0; dùng cùng định nghĩa cho mọi lượt |
| Stable recovery time | t_stable - t0 |
| Tỉ lệ lỗi | Số HTTP/network timeout/error / số request đã gửi, trong cửa sổ ghi rõ |
| Client downtime | Các khoảng client không đạt tiêu chí thành công; báo tổng và khoảng dài nhất |
| Latency | p50/p95/p99 theo baseline, sự cố, sau failover |
| Cost proxy | Giờ chạy, vCPU/RAM cấp phát, storage, traffic, số request |

Không đồng nhất response Azure đầu tiên với phục hồi hoàn toàn: request cũ vẫn có thể lỗi ở AWS. Với probe một giây, độ phân giải quan sát xấp xỉ chu kỳ probe; Route 53 metric/polling cũng có độ trễ riêng.

Mẫu bảng kết quả:

| Run | Scenario | Detection (s) | First Azure (s) | Stable recovery (s) | Errors/requests | p95 sau failover (ms) |
|---|---|---:|---:|---:|---|---:|
| 001 | F2 | Điền số đo | Điền số đo | Điền số đo | Điền số đo | Điền số đo |

Lưu dữ liệu thô trước khi tổng hợp. Báo từng lượt, trung bình và min/max; giải thích outlier. Không đặt kết quả mong muốn như “zero downtime” thành kết luận trước khi đo.

## 17. Chiến lược dữ liệu và hướng nâng cấp

### Mức 1: read-only, khuyến nghị để hoàn thành bản chính

Hai origin mang cùng dataset theo image digest. Đây là ứng dụng phân tán đa cloud có failover thật ở tầng phục vụ. Giới hạn được nêu rõ: chưa giải quyết phục hồi giao dịch ghi, không có số đo RPO cho dữ liệu người dùng.

### Mức 2: CI/CD và hạ tầng tái lập

Terraform tạo EC2/network/DNS và tài nguyên Azure; Ansible/cloud-init cài Docker/config. GitHub Actions build/test, deploy Azure trước, smoke-test, rồi deploy AWS. Dùng cùng version và có rollback về image digest cũ. State Terraform chứa thông tin nhạy cảm phải được lưu có kiểm soát và khóa cập nhật.

### Mức 3: cold standby so với warm standby

So sánh `minReplicas=0` với `1`: chi phí, thời gian khởi động, failover và health-check behavior. Muốn đo cold start phải chứng minh Azure đã về 0 trước test; probe định kỳ có thể ngăn điều đó. Không mặc định cấu hình 0 đồng nghĩa thực tế luôn có 0 replica.

### Mức 4: PostgreSQL có ghi dữ liệu

Đây là một nhánh nâng cấp riêng, cần thiết kế thêm chứ không chỉ thêm container DB. Container Apps là tầng app; đặt database có lưu trữ bền vững trên VM hoặc dịch vụ DB thích hợp.

Một hướng là PostgreSQL primary AWS và replica Azure qua kết nối riêng/TLS. Replica thông thường chỉ đọc; DNS chuyển app không làm replica tự thành writer. Nhóm cần cơ chế cô lập primary cũ (fencing), đánh giá replication lag, promote replica, đổi DB endpoint, cập nhật readiness rồi mới chuyển traffic ghi. Không tự failback khi primary cũ chưa đồng bộ lại.

Tự động promotion chỉ từ tín hiệu mất kết nối rất dễ tạo hai writer khi network partition. Nếu chưa giải quyết được, chọn promote có xác nhận và báo rõ mức tự động hóa. Dùng request idempotency cho thao tác ghi được retry, backup độc lập và đo RPO thực tế.

### Mức 5: Kubernetes nếu giảng viên yêu cầu hoặc nhóm muốn học

Chuyển deployment sang hai cluster, dùng manifests/Helm và ingress. DNS, TLS, health, dữ liệu và bài đo vẫn cần giữ nguyên. Đây là nhánh hạ tầng nặng hơn; cần budget và thời gian riêng, không suy rằng Kubernetes tự giải quyết failover giữa hai cloud.

## 18. Chi phí và vận hành

Không chốt tổng tiền khi chưa biết tài khoản, region, giờ chạy và tải. Lập bảng trước khi deploy:

| Khoản | Cách tính/kiểm tra |
|---|---|
| EC2 | Loại instance × giờ chạy, có thể thêm CPU credit nếu dùng burstable |
| EBS | Dung lượng/tháng; stop EC2 không xóa disk |
| Public IPv4/Elastic IP | Kiểm tra phí theo giờ, kể cả địa chỉ giữ lại khi dừng VM |
| Azure Container Apps | Active/idle CPU, RAM, request và free grant áp dụng |
| Route 53 | Hosted zone, DNS query, health check ngoài AWS và tùy chọn HTTPS |
| Domain | Phí đăng ký/gia hạn riêng |
| Log/monitoring | Dung lượng ingest/retention và máy chạy monitoring |
| Network | Internet egress, inter-cloud replication nếu thêm DB |

Tra trước mỗi đợt test: [EC2 pricing](https://aws.amazon.com/ec2/pricing/on-demand/), [VPC/IP pricing](https://aws.amazon.com/vpc/pricing/), [Route 53 pricing](https://aws.amazon.com/route53/pricing/), [Azure Container Apps pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/).

Free credit không chứng minh hệ thống không có chi phí: báo cả mức sử dụng tài nguyên và khoản credit bù. `minReplicas=1` để benchmark ổn định; sau buổi test dọn tài nguyên không cần thiết. Đưa Azure về 0 nhưng để health check HTTP chạy liên tục không bảo đảm tiết kiệm như dự tính.

## 19. Lộ trình 6-8 tuần và phân công

| Giai đoạn | Việc | Điều kiện hoàn thành |
|---|---|---|
| Tuần 1 | Chốt scope với giảng viên, tài khoản, domain, thiết kế | Có hai cloud sử dụng được và ngân sách |
| Tuần 2 | App local, Docker image, metrics | Image chạy độc lập, dataset thống nhất |
| Tuần 3 | EC2 và Azure deploy riêng | Cả hai origin có HTTPS và /version đúng |
| Tuần 4 | Shared hostname, Route 53 failover | Tắt AWS, domain chung thực sự trả Azure |
| Tuần 5 | Monitoring, k6, timeline | Một lượt end-to-end có dữ liệu thô |
| Tuần 6 | Lặp thí nghiệm, phân tích | Có baseline, F2/F3 và cost proxy |
| Tuần 7-8 | Một mở rộng, báo cáo, rehearsal | Chạy lại từ README, demo có phương án dự phòng |

| Người | Trách nhiệm chính |
|---|---|
| 1 | App, UI, dataset, metric instrumentation |
| 2 | AWS, NGINX, DNS/TLS và test failover |
| 3 | Azure, registry, CI/CD và quản lý phiên bản |
| 4 | Monitoring, k6, phân tích dữ liệu và báo cáo |

Phần domain/TLS và thí nghiệm cần phối hợp cả nhóm. Không để chỉ một người có quyền hoặc hiểu cách khôi phục môi trường.

## 20. Troubleshooting thường gặp

| Hiện tượng | Kiểm tra trước |
|---|---|
| DNS đổi sang Azure nhưng 404/TLS lỗi | Custom domain binding, Host/SNI, certificate và chain |
| AWS down nhưng client vẫn gọi AWS | DNS TTL, resolver cache, k6 DNS cache, keep-alive |
| Azure healthy qua URL mặc định, domain chung lỗi | Kiểm tra riêng bằng curl --connect-to; không kết luận ingress đã đúng từ URL mặc định |
| Azure không scale về 0 | Probe/health check đang tạo HTTP traffic, cấu hình scaling |
| Health check lỗi cả khi mở web được | Firewall, SNI, path, port, auth, redirect, code trả về |
| AWS restart mà app không lên | Docker service, Compose restart policy, certificate mount |
| Metrics mất khi AWS down | Monitoring nằm trên AWS hoặc chỉ scrape app, thiếu external probe |
| Kết quả đẹp bất thường | Cache, không tạo đủ tải, test đi thẳng origin thay vì domain chung |
| Hai cloud cùng lỗi | DNS không tạo ra backend khỏe; phải báo sự cố, không hứa tự cứu được |

## 21. Checklist nghiệm thu và demo

- [ ] AWS và Azure dùng cùng image digest, khác APP_ENV.
- [ ] Azure có đủ dữ liệu/config và hoạt động khi EC2 AWS dừng.
- [ ] Domain chung được bind đúng, HTTPS hợp lệ ở cả hai cloud.
- [ ] Health check riêng origin và Route 53 failover records được export/lưu mô tả.
- [ ] Stop EC2 làm môi trường AWS không phục vụ; domain chung chuyển được Azure.
- [ ] Có baseline single-cloud và ít nhất 3 lượt F2.
- [ ] Timeline phân biệt lệnh stop, origin down, health unhealthy, response Azure đầu tiên và phục hồi ổn định.
- [ ] Có DNS/cache/connection settings, error rate, latency, cost proxy và giới hạn.
- [ ] Có runbook start/stop/recovery, cleanup và rollback deployment.
- [ ] Không đưa secrets/private keys vào repo hoặc screenshot.

Demo: cho thấy version A/B -> mở Grafana -> chạy k6 -> stop EC2 -> quan sát lỗi và chuyển Azure -> trình bày biểu đồ -> start EC2 và quan sát failback. Chuẩn bị video và dữ liệu của lượt chạy trước nếu mạng phòng học không ổn định, ghi rõ đó là kết quả đã ghi lại.

Trước khi dọn cloud, export dashboard/config/kết quả. Xóa đúng tài nguyên project đã kiểm kê; kiểm tra cả disk, IP, health check, logs và resource group còn tính phí. Không xóa tài nguyên dùng chung với việc khác của thành viên.

## 22. Nguồn và giới hạn hướng dẫn

- Yêu cầu môn học: [Distributed Computing Systems Project List](./Distributed_Computing_Systems_Project_List.pdf), đề tài #29 tại trang 14, rubric và deliverables trang 14-15.
- [Route 53 active-passive routing](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-types.html).
- [Route 53 health check configuration](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-creating-values.html).
- [Azure Container Apps CLI](https://learn.microsoft.com/en-us/cli/azure/containerapp?view=azure-cli-latest).
- [Azure ingress](https://learn.microsoft.com/en-us/azure/container-apps/ingress-how-to).
- [Azure custom domain và certificate](https://learn.microsoft.com/en-us/azure/container-apps/custom-domains-certificates).
- [Azure scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app).
- [k6 options reference](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/).
- [Docker Engine trên Ubuntu](https://docs.docker.com/engine/install/ubuntu/).

Quy trình triển khai, lựa chọn scope, phân công và tiêu chí đo là đề xuất thiết kế của tài liệu. Lệnh mẫu và khả năng account/region cần được nhóm kiểm chứng khi triển khai. Bản cơ bản chứng minh failover của ứng dụng read-only giữa hai provider; chưa đại diện cho HA production toàn diện hoặc phục hồi giao dịch có ghi.
