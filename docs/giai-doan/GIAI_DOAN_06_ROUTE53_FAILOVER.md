# Giai đoạn 6 — Route 53 health checks và DNS failover

**Trạng thái:** Chưa thực hiện.
**Mục tiêu:** domain chung ưu tiên AWS khi healthy và trả Azure cho truy vấn mới khi AWS origin unhealthy.

## 1. Nguyên lý phải hiểu

Route 53 là authoritative DNS, không phải reverse proxy. Sau DNS lookup, client kết nối trực tiếp origin. Nó không chuyển TCP connection đang mở. Thời gian người dùng cảm nhận gồm health detection, authoritative answer, TTL/resolver cache và connection reuse.

## 2. Đầu vào

- GĐ3/GĐ4 origins independently healthy.
- GĐ5 TLS/Host/SNI pass ở cả hai.
- `aws-origin.example.com` → Elastic IP.
- Azure default FQDN.
- Shared `app.example.com`.
- Quyền Route 53 và ngân sách health checks.

## 3. Health check riêng origin

| Thuộc tính | AWS | Azure |
|---|---|---|
| Target | AWS origin hostname | Azure default FQDN |
| Protocol/port | HTTPS/443 | HTTPS/443 |
| Path | `/health/ready` | `/health/ready` |
| Interval ban đầu | 30 giây | 30 giây |
| Failure threshold | 3 | 3 |
| SNI | hostname phù hợp | Azure FQDN/shared host theo config |

Không tạo hai check cùng gọi `app.example.com` vì hostname này tự đổi đích, không chứng minh riêng từng origin. Readiness phải phản ánh dataset/app sẵn sàng.

## 4. Kiểm tra health trước routing

1. Cả hai health check phải healthy liên tục.
2. Dừng riêng app container AWS và xác nhận AWS check unhealthy, Azure vẫn healthy.
3. Khôi phục AWS, đợi check healthy.
4. Lặp lại phía Azure.
5. Ghi UTC của thao tác và trạng thái Route 53/CloudWatch.

Không suy detection = interval × threshold; Route 53 có nhiều checker và quy tắc tổng hợp. Phải đo.

## 5. Tạo Failover records

Tại subdomain `app`, tạo hai record cùng name/type:

| Trường | Primary | Secondary |
|---|---|---|
| Name | `app.example.com` | giống Primary |
| Type | CNAME | CNAME |
| Routing | Failover | Failover |
| Role | Primary | Secondary |
| Value | `aws-origin.example.com` | Azure FQDN |
| Health check | AWS check | Azure check |
| Identifier | `aws-primary` | `azure-secondary` |
| TTL ban đầu | 30 giây | 30 giây |

Xóa CNAME simple dùng khi Azure validation nếu xung đột. Không trộn A primary với CNAME secondary trong cùng set. Không dùng CNAME tại zone apex.

## 6. Xác minh trạng thái bình thường

- Query authoritative NS và resolver hệ điều hành.
- Gọi `https://app.example.com/version` nhiều lần.
- Kỳ vọng `environment=aws-primary`.
- Azure health check vẫn healthy dù không nhận user traffic.
- Ghi TTL và connection reuse của client.

Lưu output `Resolve-DnsName`/`dig`, response headers và Route 53 health status.

## 7. Dry run failover

1. Đảm bảo monitoring/load generator không chạy trên EC2 sắp dừng.
2. Bắt đầu probe mỗi giây tới shared domain.
3. Ghi `t_action` khi stop app AWS.
4. Ghi `t0` khi external probe đầu tiên xác nhận AWS không phục vụ.
5. Ghi `t_detect` khi Route 53 đánh primary unhealthy.
6. Query authoritative NS và recursive resolver song song.
7. Ghi `t_first_B` khi response đầu tiên từ `azure-standby`.
8. Ghi `t_stable` khi có 10 response thành công liên tiếp.
9. Khôi phục AWS, quan sát health và failback.

Dry run stop process chỉ kiểm tra routing. Bài chính GĐ8 phải stop toàn EC2 để mô phỏng environment failure.

## 8. Kỳ vọng và cách diễn giải

Primary healthy: shared domain trả AWS. Primary unhealthy + Secondary healthy: DNS mới trả Azure. Cả hai unhealthy: DNS không tạo ra backend khỏe; phải báo sự cố. AWS healthy lại có thể được chọn sau cache/failback.

Không hứa zero downtime. Client còn cache AWS hoặc giữ keep-alive có thể lỗi sau khi authoritative DNS đã đổi.

## 9. Rollback

Nếu failover record sai, lưu/export cấu hình trước rồi khôi phục simple record hoặc sửa target. Nếu health check false-negative, không giảm threshold tùy tiện; kiểm tra SNI, firewall, path, redirect, auth và certificate.

## 10. Artifact cần lưu

- Health check IDs và cấu hình.
- Export/mô tả hai records.
- TTL, authoritative NS.
- Timeline UTC dry run.
- DNS query trước/trong/sau lỗi.
- Response `/version` chứng minh AWS → Azure.
- Chi phí hosted zone/query/health check.

## 11. Checklist hoàn thành

- [ ] Health check riêng từng origin.
- [ ] Cả hai check healthy khi origin tốt.
- [ ] Primary/Secondary records cùng name/type.
- [ ] Bình thường domain trả AWS.
- [ ] AWS app lỗi thì truy vấn mới trả Azure.
- [ ] Version/fingerprint không đổi.
- [ ] Đo detection, first Azure và stable recovery.
- [ ] Quan sát failback.
- [ ] Evidence/cost đã lưu.

## 12. Kết quả mong đợi và bước tiếp theo

Sau GĐ6 hệ thống có automated DNS failover nhưng dữ liệu quan sát còn rời rạc. GĐ7 triển khai monitoring độc lập. Xem [GĐ7](GIAI_DOAN_07_OBSERVABILITY.md).
