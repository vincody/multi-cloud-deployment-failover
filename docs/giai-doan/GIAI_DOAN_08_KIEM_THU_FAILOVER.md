# Giai đoạn 8 — Kiểm thử failover, phân tích và báo cáo

**Trạng thái:** Chưa thực hiện.
**Mục tiêu:** chạy thí nghiệm lặp lại được và tính detection time, failover time, downtime, error rate, latency và cost proxy.

## 1. Điều kiện trước khi chạy

- AWS/Azure cùng digest, khác APP_ENV.
- Hai origin và shared HTTPS pass.
- Route 53 checks/records healthy.
- Monitoring nằm ngoài AWS.
- Máy k6/probe không sleep, clock đồng bộ.
- Budget và cleanup owner được xác nhận.
- Run manifest ghi commit, digest, TTL, health interval/threshold, replica, client DNS mode và tải.

Không thay đổi image/TTL/replica giữa các lượt so sánh.

## 2. Workload chuẩn

Khởi đầu 10 request/giây trong 10 phút:

- Phút 0–3: baseline AWS.
- Phút 3: gây lỗi.
- Phút 3–7: quan sát failover/Azure.
- Phút 7: khôi phục AWS.
- Phút 7–10: recovery/failback.

Nếu failover dài hơn, kéo dài cửa sổ. Constant-arrival-rate cần theo dõi `dropped_iterations`; nếu thiếu VU, không được nói đã gửi đủ 10 RPS.

Script `tests/load/failover.js` gọi `/api/devices`, timeout 5 giây, không cache, đọc environment từ JSON và đếm response theo origin.

## 3. Hai chế độ DNS/client

### Client mặc định

Giữ DNS/connection settings k6 mặc định, ghi rõ k6 version và resolver. Kết quả phản ánh client đó, không đại diện mọi browser.

### DNS experiment

Chạy thêm cấu hình TTL nội bộ ngắn và noConnectionReuse để quan sát authoritative change rõ hơn. Resolver OS/network vẫn có cache. Không so latency giữa hai cấu hình rồi quy toàn bộ chênh lệch cho cloud.

## 4. Probe song song

Mỗi giây gọi `/version` qua shared domain, lưu CSV:

```text
started_at_utc,completed_at_utc,status,environment,duration_ms,error
```

Song song query recursive resolver và authoritative nameserver. Việc này phân biệt Route 53 đã đổi record nhưng client còn cache.

## 5. Kịch bản phải chạy

| Mã | Kịch bản | Tác động | Mục đích |
|---|---|---|---|
| B0 | Single-cloud baseline | Chỉ AWS | Chứng minh không có standby thì outage kéo dài |
| B1 | Multi-cloud normal | Cả hai healthy, AWS primary | Baseline latency/cost |
| F1 | App process failure | Stop app container AWS | Kiểm tra health/routing |
| F2 | Environment failure chính | Stop EC2 AWS | Đáp ứng yêu cầu đề tài |
| F3 | Recovery/failback | Start EC2 | Đo trở về primary |
| F4 | Failover dưới tải | Tăng tải có kiểm soát rồi stop | Đo capacity/tail latency Azure |
| F5 | Readiness 503 | Process sống nhưng not ready | Kiểm tra decision signal |

F2 là bằng chứng chính; F5 không thay thế F2. Mỗi scenario tối thiểu 3 lượt khởi đầu, đợi hai origin healthy giữa các lượt.

## 6. Quy trình một lượt F2

1. Tạo run ID, thư mục output và manifest.
2. Xác nhận version/digest/fingerprint hai origin.
3. Xác nhận Route 53 và Grafana healthy.
4. Bắt đầu Prometheus/Grafana, probe CSV và k6.
5. Chạy baseline đủ 3 phút.
6. Ghi UTC gửi lệnh stop EC2 (`t_action`).
7. Ghi external origin down (`t0`).
8. Ghi Route 53 unhealthy (`t_detect`).
9. Ghi response Azure đầu tiên qua shared domain (`t_first_B`).
10. Ghi 10 success liên tiếp (`t_stable`).
11. Tiếp tục đo Azure ổn định.
12. Start EC2, quan sát readiness và failback.
13. Dừng tải, export raw data/dashboard/log.
14. Xác nhận resources và chi phí; không dọn trước khi lưu evidence.

Lệnh stop/start chỉ dùng đúng instance lab đã xác minh bằng describe. Gửi stop không đồng nghĩa instance down ngay.

## 7. Định nghĩa metric

| Chỉ số | Công thức/định nghĩa |
|---|---|
| Detection time | `t_detect - t0` |
| First-Azure failover | `t_first_B - t0` |
| Stable recovery | `t_stable - t0` |
| Error rate | failed HTTP/network requests / total requests trong cửa sổ |
| Client downtime | tổng và khoảng lỗi liên tục dài nhất |
| Latency | p50/p95/p99 cho baseline, transition, Azure steady |
| Cost proxy | giờ chạy × vCPU/RAM/storage/IP/query/log/request |

Với probe 1 giây, độ phân giải xấp xỉ 1 giây cộng thời gian request. Không gọi first Azure là phục hồi hoàn toàn nếu request cũ vẫn lỗi.

## 8. Tổ chức output

Mỗi run lưu:

- `manifest.yaml`: scenario/config/digest/TTL.
- `incident.csv`: event và UTC.
- `probe.csv`.
- `k6.json` hoặc summary.
- Prometheus/Grafana export.
- AWS/Azure/Route 53 snapshots.
- `analysis.md` nêu kết quả và anomaly.

Raw data là bất biến; tạo file tổng hợp riêng. Không chỉnh raw CSV để làm kết quả đẹp.

## 9. Phân tích kết quả

Báo từng lượt, trung bình, min/max và outlier. Tách ba cửa sổ: baseline, transition, Azure steady. So B0 với F2 để cho thấy giá trị standby. So latency AWS/Azure nhưng ghi region/network/client. Báo cả request count thực tế và dropped iterations.

Kết luận cần trả lời:

- Failover mất bao lâu ở authoritative DNS và ở client?
- Bao nhiêu request lỗi?
- Azure có chịu được tải?
- Failback có gây lỗi lần hai?
- Chi phí warm standby là gì?
- Những giới hạn nào ngăn kiến trúc trở thành production HA?

## 10. Lỗi/threats to validity

- DNS/OS/browser/k6 cache.
- HTTP keep-alive.
- Clock lệch.
- Chỉ chạy một lượt.
- Monitoring cùng failure domain.
- Cache nóng/lạnh không nhất quán.
- Tải phát không đủ.
- Region/Internet jitter.
- Thay config giữa các lượt.
- Dùng stop container để tuyên bố mất toàn environment.

Ghi các yếu tố này trong report thay vì giấu.

## 11. Cleanup sau thí nghiệm

Export evidence trước. Dừng/xóa đúng resources theo inventory: EC2, EBS nếu không cần, Elastic IP/public IPv4, Route 53 checks/records, Azure Container App/Environment/Resource Group, log retention. Không xóa hosted zone/domain/tài nguyên dùng chung nếu chưa xác nhận.

## 12. Checklist nghiệm thu

- [ ] Có B0, B1, F2, F3; F2 chạy ít nhất 3 lượt.
- [ ] Probe/k6/monitor chạy ngoài AWS.
- [ ] Timeline đủ `t_action/t0/t_detect/t_first_B/t_stable`.
- [ ] Có error rate, downtime, p50/p95/p99.
- [ ] Có raw data và run manifest.
- [ ] Có DNS authoritative vs recursive evidence.
- [ ] Có cost proxy.
- [ ] Phân tích outlier/limitations.
- [ ] Có runbook recovery/cleanup.
- [ ] Có video hoặc dữ liệu dự phòng cho demo.

## 13. Kết quả mong đợi

Sản phẩm cuối không chỉ là website đổi từ AWS sang Azure, mà là bộ bằng chứng định lượng cho thấy failover diễn ra thế nào, mất bao lâu, lỗi bao nhiêu, tốn gì và giới hạn ở đâu.
