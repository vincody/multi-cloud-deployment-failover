# Giai đoạn 6 — Route 53 health checks và DNS failover

**Mục tiêu:** khi AWS origin không healthy, DNS của `app.example.com` chuyển sang Azure standby.

## Việc sẽ làm

1. Tạo health check riêng cho AWS origin và Azure origin, gọi `/health/ready` qua HTTPS.
2. Tạo Route 53 Failover record PRIMARY cho AWS và SECONDARY cho Azure, cùng hostname người dùng.
3. Kiểm tra AWS healthy thì DNS trả AWS; Azure chỉ là standby.
4. Mô phỏng AWS mất toàn bộ app (dừng instance/app theo runbook), không dừng load generator/monitor.
5. Ghi các timestamp: lúc gây lỗi, health check nhận lỗi, DNS trả Azure, request đầu tiên thành công từ Azure.
6. Khôi phục AWS và kiểm tra quy tắc failback đã chọn.

## Lưu ý

Route 53 chỉ quyết định DNS. DNS TTL và cache của resolver/browser có thể làm người dùng thấy chuyển chậm hơn thời điểm Route 53 phát hiện lỗi. Cần đo riêng hai thời điểm đó.

## Bằng chứng đạt

- Request mới qua domain chung đổi `environment` từ `aws-primary` sang `azure-standby`.
- Azure vẫn trả version/fingerprint trùng AWS.

## Tiếp theo

Thực hiện [GĐ7 — Observability](GIAI_DOAN_07_OBSERVABILITY.md).
