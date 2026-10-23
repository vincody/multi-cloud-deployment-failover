# Failover tự động AWS → Azure bằng watcher

Một dịch vụ nhỏ (`dcs29-watcher`) chạy trên VM Azure theo dõi origin AWS. Khi có đủ bằng chứng AWS đã sập, nó fence AWS, promote PostgreSQL standby và kiểm tra Azure ghi được; Route 53 tự chuyển DNS khi Azure là origin duy nhất còn khỏe. Mặc định tắt (`enable_auto_failover=false`). Chưa chạy apply cloud hoặc nghiệm thu trên VM thật; logic quyết định và các ca lỗi được kiểm thử offline và bản local được kiểm chứng bằng `scripts/check_local_lifecycle.py`.

## 1. Vì sao chạy trên Azure

Watcher phải sống ngoài thứ nó giám sát. Đặt trên EC2 hoặc trong region AWS thì khi đó sập, watcher cũng mất. Trên VM Azure nó ở cạnh database sẽ được promote và gọi `pg_promote` trực tiếp. Hệ quả cần ghi vào báo cáo: watcher là điểm lỗi duy nhất của luồng tự động; nếu VM Azure sập thì không còn ai theo dõi, nhưng khi đó cũng không còn standby để promote.

## 2. Quyết định failover

Mỗi `watcher_interval_seconds` (mặc định 5) watcher hỏi hai nguồn:

| Nguồn | Câu hỏi | Tính là "AWS sập" khi |
|---|---|---|
| Probe trực tiếp từ Azure | `https://<aws-origin>/health/database` trả 200? | Từ chối kết nối, timeout hoặc HTTP 5xx |
| Route 53 health checker | `GetHealthCheckStatus` của health check AWS | Có quan sát và **không** checker nào báo Success |

Lỗi DNS, TLS hoặc HTTP 4xx của probe, và lỗi gọi API Route 53, **không** tính là AWS sập (chúng nói về VM Azure hoặc cấu hình hơn là về writer). Chỉ khi cả hai nguồn cùng báo sập `watcher_threshold` lần liên tiếp (mặc định 3) mới failover. Một nguồn lỗi còn nguồn kia khỏe chỉ ghi log và tiếp tục theo dõi. Thời gian phát hiện xấp xỉ `threshold × (interval + thời gian chờ mỗi lần kiểm tra)`.

## 3. Các bước failover

1. Kiểm tra lần cuối cả hai nguồn vẫn báo sập; nếu AWS đã trả lời thì dừng.
2. `preflight` trên Azure: PostgreSQL là standby, replay không bị pause, đã replay hết WAL nhận được.
3. **Fence:** `ec2:StopInstances` rồi chờ trạng thái `stopped` (tối đa 4 phút). Trạng thái lạ hoặc quá hạn thì dừng, không promote.
4. `promote`: ghi marker incident, `pg_promote`, mở `writes_enabled` dưới advisory lock.
5. Chờ `/api/database/status` của origin Azure báo `primary` và `writable`.
6. Route 53 thấy Azure là origin duy nhất khỏe và chuyển DNS. Watcher không đụng tới DNS.

Nếu bất kỳ bước nào lỗi, watcher thoát với mã 2 và **không tự thử lại**; `systemd` không restart mã 2 (`RestartPreventExitStatus=2`). Cần người vận hành.

## 4. Những gì watcher không làm

- Không failback và không Start lại EC2. AWS sống lại không có nghĩa nó có dữ liệu mới nhất; dùng quy trình rejoin/failback (mục 9.1).
- Không hành động khi database local đã là primary, khi có file `/opt/dcs29/failover-incident.json` mà database vẫn là standby (incident dở dang), hoặc khi có file `/opt/dcs29/watcher.pause`.
- Không đảm bảo RPO = 0: replication bất đồng bộ nên giao dịch chưa kịp sang Azure có thể mất.

## 5. Quyền AWS

IAM user `dcs29-azure-watcher` (Terraform tạo user và policy; **access key tạo ngoài Terraform** để secret không vào state):

| Quyền | Phạm vi |
|---|---|
| `ec2:DescribeInstances` | `*` |
| `ec2:StopInstances` | chỉ instance primary |
| `route53:GetHealthCheckStatus` | chỉ health check của origin AWS |

Không có quyền Start, không có SSM, không có quyền ghi DNS. Key nằm trong `/etc/dcs29/watcher-aws-credentials` (0600) trên VM Azure.

## 6. Tệp trên VM Azure

| Đường dẫn | Nội dung |
|---|---|
| `/opt/dcs29/watcher/cloud_watcher.py`, `promote.py`, `watcher_core.py` | Vòng lặp, logic promote, lõi quyết định dùng chung với bản local |
| `/etc/dcs29/watcher.json` | Origin, instance, health check, `interval`, `threshold` (không có secret) |
| `/etc/dcs29/watcher-aws-credentials` | Access key của IAM user watcher |
| `/etc/systemd/system/dcs29-watcher.service` | Dịch vụ |
| `/opt/dcs29/failover-incident.json` | Marker của incident đang xử lý |

## 7. Vận hành

```bash
sudo systemctl status dcs29-watcher
sudo journalctl -u dcs29-watcher -f              # xem từng lần kiểm tra và quyết định
sudo touch /opt/dcs29/watcher.pause              # tạm dừng khi bảo trì có kế hoạch
sudo rm /opt/dcs29/watcher.pause                 # bật lại
sudo env AWS_SHARED_CREDENTIALS_FILE=/etc/dcs29/watcher-aws-credentials AWS_PROFILE=default \
  python3 /opt/dcs29/watcher/cloud_watcher.py --check   # tự kiểm tra, không thay đổi gì
```

**Trước khi stop/reboot VM, thử lỗi pod riêng, rejoin hoặc failback:** tạo file `watcher.pause`. Stop EC2 có chủ đích khi watcher đang chạy sẽ được hiểu là sự cố thật.

**Sau một incident:** hoàn tất rejoin và failback để AWS lại là primary, Azure lại là standby streaming, rồi mới `sudo rm /opt/dcs29/failover-incident.json` và `sudo systemctl restart dcs29-watcher`. Làm trước đó thì watcher có thể promote lần nữa trên dữ liệu không đúng.

## 8. Kiểm thử

| Mức | Cách | Chứng minh |
|---|---|---|
| Quyết định | `tests/test_watcher.py` | Cần cả hai nguồn, bộ đếm reset, thao tác có kế hoạch tạm dừng, lỗi fence dừng lại |
| Cloud watcher | `deploy/terraform/watcher/tests` | Phân loại lỗi probe, Route 53, fence trước promote, EC2 chưa `stopped` thì không promote, owner dùng lại giữa các lần chạy |
| Cài đặt | `watcher/tests/test_watcher_install.py` | Chỉ cài trên standby khỏe, kiểm tra trước khi bật, credential đúng dạng |
| Terraform | `terraform test` | Opt-in, điều kiện tiên quyết, quyền IAM đúng danh sách |
| Local end to end | `scripts/check_local_lifecycle.py` | Watcher phát hiện database AWS bị kill, failover, probe thấy outage rồi phục hồi |

Chưa kiểm chứng: Route 53 `GetHealthCheckStatus`, `StopInstances` và `systemd` trên VM thật. Đó là việc của lượt nghiệm thu trên cloud.
