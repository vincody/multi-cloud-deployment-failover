# Giai đoạn 7 — Monitoring và observability

**Mục tiêu:** có bằng chứng độc lập về health, availability và latency; không phụ thuộc máy EC2 AWS khi mô phỏng lỗi.

## Việc sẽ làm

1. Chạy Prometheus, Grafana và Blackbox Exporter ở máy nhóm hoặc môi trường ngoài AWS EC2.
2. Probe riêng AWS origin, Azure origin và domain chung qua HTTPS `/health/ready`.
3. Scrape `/metrics` của app khi phù hợp; không tạo label chứa request ID hoặc thiết bị.
4. Tạo dashboard: up/down, HTTP status, latency p50/p95, request rate, thời điểm failover.
5. Export ảnh/dashboard JSON và lưu URL/cấu hình không chứa secret.

## Bằng chứng đạt

- Khi AWS bị dừng, monitor vẫn chạy và ghi được AWS down/Azure up/domain recovered.

## Tiếp theo

Thực hiện [GĐ8 — kiểm thử failover](GIAI_DOAN_08_KIEM_THU_FAILOVER.md).
