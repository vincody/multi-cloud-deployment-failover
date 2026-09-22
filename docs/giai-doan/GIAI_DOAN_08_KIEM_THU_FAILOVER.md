# Giai đoạn 8 — Kiểm thử failover và báo cáo

**Mục tiêu:** chạy thí nghiệm có thể lặp lại, đo được failover time, downtime, latency và trade-off.

## Việc sẽ làm

1. Viết k6 scenario gọi domain chung liên tục từ máy ngoài AWS EC2.
2. Chạy baseline khi AWS healthy; lưu p50/p95, error rate và DNS/HTTP timeline.
3. Bắt đầu tải, sau đó dừng toàn bộ app/EC2 AWS theo runbook.
4. Không restart k6/monitor; chờ Route 53 phát hiện và DNS trả Azure.
5. Ghi downtime, request lỗi, request thành công đầu tiên từ Azure, latency trước/sau.
6. Khôi phục AWS, ghi failback và dọn tài nguyên để kiểm soát chi phí.

## Bằng chứng đạt

- Bảng kết quả, biểu đồ, raw k6 output, ảnh monitoring và timeline UTC.
- Phân tích rõ ràng giới hạn: DNS caching, health-check interval, standby cost, TLS/domain complexity và dữ liệu read-only.
