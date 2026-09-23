# Giai đoạn 7 — Monitoring và observability độc lập

**Trạng thái:** Chưa thực hiện.
**Mục tiêu:** thu bằng chứng availability/latency từ bên ngoài AWS để monitoring vẫn sống khi EC2 bị dừng.

## 1. Kiến trúc quan sát

Prometheus + Blackbox Exporter + Grafana chạy trên laptop nhóm hoặc máy thứ ba, không đặt trên EC2 primary. Đồng bộ clock/NTP và tắt sleep trong buổi test.

Ba target bắt buộc:

1. AWS origin riêng.
2. Azure origin riêng.
3. Shared domain người dùng.

Các lớp tín hiệu:

| Nguồn | Dữ liệu | Ý nghĩa |
|---|---|---|
| FastAPI `/metrics` | request count, duration histogram | Hành vi bên trong app |
| Blackbox | probe success, HTTP status, duration | Trải nghiệm từ ngoài |
| Route 53/CloudWatch | health check status | DNS service đánh dấu origin |
| k6/probe CSV | status, env, duration, error | Trải nghiệm client |
| Incident log | UTC, thao tác, resource ID | Ground truth sự kiện |

Prometheus `up` chỉ nói scrape thành công, không đồng nghĩa nghiệp vụ ready.

## 2. Thành phần cần tạo trong repository

- `observability/prometheus.yml`.
- `observability/blackbox.yml`.
- `observability/compose.yaml`.
- `observability/grafana/provisioning/`.
- Dashboard JSON export.
- Runbook start/stop/export.

Không commit Grafana admin password/token. Dùng env/secret local.

## 3. Blackbox Exporter

Module HTTPS cần:

- HTTP GET.
- IPv4 ưu tiên nếu môi trường IPv6 không ổn định.
- Valid HTTP 2xx.
- TLS verification bật.
- Timeout nhỏ hơn scrape interval.
- Không dùng insecure skip verify.

Prometheus dùng `/probe` với module HTTPS và relabel target URL. Tạo job riêng hoặc label rõ `aws-origin`, `azure-origin`, `shared-domain`.

## 4. Prometheus scrape

Scrape interval khởi đầu 5 giây cho thí nghiệm, evaluation phù hợp. Retention đủ cho các lượt chạy. Scrape:

- Blackbox probes.
- Prometheus self metrics.
- App `/metrics` nếu endpoint được bảo vệ/allowlist.

Nếu không mở app metrics qua Internet, external probe vẫn là bằng chứng chính. Không mở `/metrics` toàn Internet chỉ để tiện demo.

Các metric quan trọng:

- `probe_success`.
- `probe_http_status_code`.
- `probe_duration_seconds`.
- `probe_ssl_earliest_cert_expiry`.
- `dcs29_http_requests_total`.
- Histogram `dcs29_http_request_duration_seconds`.

## 5. Grafana dashboard

Panel tối thiểu:

1. Health AWS/Azure/shared theo thời gian.
2. HTTP status từng target.
3. Probe duration p50/p95 hoặc time series.
4. Request rate theo origin.
5. Application latency histogram quantile.
6. Certificate expiry.
7. Error rate.
8. Annotation `t_action`, `t0`, `t_detect`, `t_first_B`, `t_stable`.

Dùng UTC hoặc ghi timezone rõ. Màu không phải tín hiệu duy nhất; panel cần label/value.

## 6. Cách chạy và smoke test

```bash
docker compose -f observability/compose.yaml up -d
docker compose -f observability/compose.yaml ps
```

Kiểm tra Prometheus targets đều UP, Blackbox query trả probe_success=1, Grafana datasource healthy. Sau đó tạm stop app AWS: AWS probe phải 0, Azure vẫn 1, shared có thể gián đoạn rồi trở lại 1.

## 7. Dữ liệu và retention

Mỗi run tạo thư mục/tên riêng ngoài secret:

- manifest: scenario, commit, digest, TTL, load, replica.
- incident timeline UTC.
- Prometheus snapshot/export.
- Grafana screenshots/JSON.
- k6/raw probe output.

Không chỉ giữ screenshot; cần raw data để tính lại. Ghi clock source và sampling interval để biết sai số.

## 8. Lỗi thường gặp

| Hiện tượng | Nguyên nhân/kiểm tra |
|---|---|
| Metrics mất khi AWS chết | Monitoring đặt trên AWS |
| Prometheus up=1 nhưng app lỗi | Scrape endpoint sống, readiness lỗi |
| Probe TLS fail | SNI/certificate/chain |
| Cả ba target giống nhau | Probe origin đang đi qua shared DNS |
| Không thấy gap ngắn | Scrape interval quá dài |
| Disk đầy | Retention/log không giới hạn |
| Dashboard đẹp nhưng không tái tính | Chỉ lưu ảnh, thiếu raw data |

## 9. Artifact cần lưu

- Compose/config/relabel rules.
- Grafana dashboard JSON.
- Target page screenshot.
- Raw samples/snapshot cho ít nhất một dry run.
- UTC annotations và version của Prometheus/Grafana/Blackbox.
- Resource/retention/cost proxy.

## 10. Checklist hoàn thành

- [ ] Monitoring nằm ngoài EC2.
- [ ] Probe riêng AWS, Azure và shared domain.
- [ ] TLS verification bật.
- [ ] Dashboard có health, status, latency, error và annotations.
- [ ] Stop AWS vẫn ghi được dữ liệu.
- [ ] Raw data và dashboard export được.
- [ ] Timezone/sampling/retention được ghi rõ.
- [ ] Metrics endpoint không public vô kiểm soát.

## 11. Kết quả mong đợi và bước tiếp theo

GĐ7 cung cấp timeline khách quan cho GĐ8. Sau khi dry run ghi đủ dữ liệu, chuyển [GĐ8 — kiểm thử failover](GIAI_DOAN_08_KIEM_THU_FAILOVER.md).
