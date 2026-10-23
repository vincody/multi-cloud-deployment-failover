# Đo đạc failover

Đề tài 29 yêu cầu đo failover time, downtime, latency và cost proxy. Tài liệu này mô tả cách lấy từng số liệu và mẫu bảng báo cáo.

## 1. Công cụ

| Công cụ | Cho ra |
|---|---|
| `scripts/probe_failover.py` | CSV mỗi tick (đọc + ghi), downtime, tỷ lệ lỗi, p50/p95, số lần ghi bị mất |
| `.local-replication/<project>/state.json` → `timeline` | Timestamp UTC từng bước của lần chuyển đổi gần nhất |
| `.local-replication/<project>/events.jsonl` | Lịch sử mọi thay đổi trạng thái |
| `/api/database/status` | LSN hiện tại / received / replayed |

## 2. Chạy một lượt đo (local)

```powershell
# Terminal 1: probe 3 phút qua URL chung
.venv/Scripts/python.exe scripts/probe_failover.py --url http://127.0.0.1:8090 --duration 180 --out probe.csv

# Terminal 2: sau ~30 giây, gây sự cố rồi failover
docker compose -p hetinhtoanphanbo -f database/compose.replication.yml kill database
.venv/Scripts/python.exe scripts/local_replication.py failover
```

Probe mỗi giây gọi `GET /version` và `POST /api/ping` (ghi một dòng vào `probe_pings`, đi qua cùng khóa ghi với Issues). Kết quả nằm trong `probe.csv` và `probe.summary.json`. `switchover` thay cho `failover` nếu muốn đo chuyển có kế hoạch.

## 3. Định nghĩa số liệu

| Số liệu | Cách tính |
|---|---|
| **Downtime (ghi / đọc)** | Từ lần gọi thành công cuối trước lỗi đến lần thành công đầu tiên sau lỗi (`outages` trong summary). Độ phân giải bằng chu kỳ probe |
| **Failover time** | `done` − `start` trong `timeline` (thời gian hệ thống xử lý). Detection time (từ lúc sự cố đến `start`) lấy từ CSV: lỗi đầu tiên − thời điểm gây sự cố |
| **Tỷ lệ lỗi** | Số tick khác 200 / tổng số tick |
| **Latency** | p50 và p95 của các lần gọi thành công |
| **Dữ liệu mất (RPO)** | Số `seq` đã được xác nhận 200 nhưng không còn trên writer mới (`lost_writes`). Bằng 0 với `switchover`; có thể > 0 với `failover` khi AWS sập đột ngột |

Các bước trong `timeline`: `start`, `writes-paused-<cloud>`, `replayed-<cloud>`, `azure-received-lsn`, `fenced-<cloud>`, `writes-enabled-<cloud>`, `verified-<cloud>`, `done`. Chênh lệch giữa hai bước liên tiếp cho biết bước nào chiếm thời gian.

## 4. Local và cloud khác nhau ở đâu

Số đo local không dùng làm số đo cloud. Trên cloud, thời gian failover còn gồm:

- Chu kỳ health check Route 53 × số lần lỗi liên tiếp để phát hiện;
- TTL của DNS và cache phía client;
- Thời gian dừng EC2 (fence) và promote PostgreSQL trong K3s.

Báo cáo nên trình bày hai bảng riêng. Khi đo trên cloud, chạy probe từ laptop tới `https://app.<domain>` và ghi lại thời điểm gây sự cố để tính detection time.

## 5. Mẫu bảng kết quả

| Kịch bản | Môi trường | Detection | Failover time | Downtime ghi | Downtime đọc | Lỗi (%) | p95 (ms) | Ghi bị mất |
|---|---|---|---|---|---|---|---|---|
| switchover | local | – | | | | | | |
| failover (kill AWS DB) | local | | | | | | | |
| failover (dừng EC2) | cloud | | | | | | | |

Ghi kèm: phiên bản image (digest), chu kỳ probe, thời điểm UTC, số lần lặp (nên ≥ 3 và báo trung bình lẫn giá trị lớn nhất).

## 6. Cost proxy

Chi phí DR là phần trả thêm để có môi trường dự phòng. Số lượng tài nguyên lấy từ `deploy/terraform` (giá trị mặc định):

| Thành phần | AWS (primary) | Azure (standby) |
|---|---|---|
| VM | `t3.medium` | `Standard_B2s` |
| Disk | gp3 30 GiB | StandardSSD_LRS 30 GiB |
| IP công khai | Elastic IP | Public IP |
| DNS và health check | Route 53 hosted zone + 2 health check | – |
| Lưu lượng ra Internet | thực tế theo lượt đo | thực tế theo lượt đo |

Cách tính, điền đơn giá hiện hành từ trang giá của từng nhà cung cấp tại thời điểm đo, không dùng số nhớ:

```
chi phí tháng = giờ chạy × đơn giá VM + GiB disk × đơn giá disk + đơn giá IP + health check + zone + egress
chi phí DR    = chi phí Azure + health check + egress replication (WireGuard)
overhead DR   = chi phí DR / chi phí AWS
```

Overhead DR cho biết hệ thống tốn thêm bao nhiêu phần trăm để rút ngắn thời gian gián đoạn từ "tự dựng lại" xuống còn số đo ở mục 5.
