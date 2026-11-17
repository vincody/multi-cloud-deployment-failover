# Failover tự động AWS → Azure bằng watcher

Một dịch vụ nhỏ (`dcs29-watcher`) chạy trên VM Azure theo dõi origin AWS. Khi có đủ bằng chứng AWS đã sập, nó fence AWS, promote PostgreSQL standby và kiểm tra Azure ghi được; Route 53 tự chuyển DNS khi Azure là origin duy nhất còn khỏe. Mặc định tắt (`enable_auto_failover=false`). Chưa chạy apply cloud hoặc nghiệm thu trên VM thật; logic quyết định và các ca lỗi được kiểm thử offline và bản local được kiểm chứng bằng `scripts/check_local_lifecycle.py`.

## 1. Vì sao chạy trên Azure

Watcher phải sống ngoài thứ nó giám sát. Đặt trên EC2 hoặc trong region AWS thì khi đó sập, watcher cũng mất. Trên VM Azure nó ở cạnh database sẽ được promote và gọi `pg_promote` trực tiếp. Hệ quả cần ghi vào báo cáo: watcher là điểm lỗi duy nhất của luồng tự động; nếu VM Azure sập thì không còn ai theo dõi, nhưng khi đó cũng không còn standby để promote.

## 2. Quyết định failover

Mỗi `watcher_interval_seconds` (mặc định 5) watcher hỏi hai nguồn:

| Nguồn | Câu hỏi | Tính là "AWS sập" khi |
|---|---|---|
| Probe trực tiếp từ Azure | `https://<aws>/health/database` trả 200? | Từ chối kết nối, timeout hoặc HTTP 5xx |
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

- Watcher **không bao giờ Start EC2**: bật lại AWS luôn là quyết định của người vận hành. Mặc định (`enable_auto_recovery=false`) nó cũng không rejoin hay failback. AWS sống lại không có nghĩa nó có dữ liệu mới nhất; dùng quy trình rejoin/failback thủ công ([Phụ lục A](TRIEN_KHAI_BANG_TERRAFORM.md#phụ-lục-a--runbook-failover-rejoin-và-failback-thủ-công)) hoặc bật recovery tự động ở [mục 9](#9-recovery-tự-động-rejoin-và-failback-sau-khi-bạn-bật-lại-ec2).
- Không hành động khi database local đã là primary, khi có file `/opt/dcs29/failover-incident.json` mà database vẫn là standby (incident dở dang), hoặc khi có file `/opt/dcs29/watcher.pause`.
- Không đảm bảo RPO = 0: replication bất đồng bộ nên giao dịch chưa kịp sang Azure có thể mất.

## 5. Quyền AWS

IAM user `dcs29-azure-watcher` (Terraform tạo user và policy; **access key tạo ngoài Terraform** để secret không vào state):

| Quyền | Phạm vi |
|---|---|
| `ec2:DescribeInstances` | `*` |
| `ec2:StopInstances` | chỉ instance primary |
| `route53:GetHealthCheckStatus` | chỉ health check của origin AWS |

Recovery không thêm quyền AWS nào (không có `ec2:StartInstances`). Không có SSM, không có quyền ghi DNS. Key nằm trong `/etc/dcs29/watcher-aws-credentials` (0600) trên VM Azure.

## 6. Tệp trên VM Azure

| Đường dẫn | Nội dung |
|---|---|
| `/opt/dcs29/watcher/cloud_watcher.py`, `promote.py`, `watcher_core.py` | Vòng lặp, logic promote, lõi quyết định dùng chung với bản local |
| `/etc/dcs29/watcher.json` | Origin, instance, health check, `interval`, `threshold` (không có secret) |
| `/etc/dcs29/watcher-aws-credentials` | Access key của IAM user watcher |
| `/etc/systemd/system/dcs29-watcher.service` | Dịch vụ |
| `/opt/dcs29/failover-incident.json` | Marker của incident đang xử lý |
| `/opt/dcs29/watcher/recovery.py`, `recovery_ops.py`, `rpc.py`, `templates/postgres-standby.yaml.tmpl` | Recovery (mục 9); chỉ chạy khi `recovery.enabled` trong `watcher.json` |
| `/opt/dcs29/recovery-state.json` | Phase recovery đang chạy (xóa khi rearm) |
| `/opt/dcs29/watcher/unguard.py`, `/etc/systemd/system/dcs29-unguard.service` | Gỡ guard K3s sau reboot Azure (mục 10); chỉ khi `enable_azure_auto_start=true` |
| `/etc/dcs29/recovery_ed25519` | Key riêng sinh trên Azure để gọi RPC trên AWS; không rời VM |

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
| Recovery | `watcher/tests/test_recovery.py` | Thứ tự start → rejoin → khóa ghi → fence Azure → promote AWS → dựng lại Azure; AWS là primary thứ hai thì fence và dừng; lag thì mở lại ghi Azure; promote lỗi thì khôi phục Azure; chạy lại đúng phase và dùng lại PVC |
| Gỡ guard Azure | `watcher/tests/test_unguard.py` | Ma trận quyết định, đọc role từ thư mục dữ liệu, peer không rõ thì không bao giờ khởi động, `watcher.pause` là quyền phủ quyết, marker ghi trước khi start |
| Thao tác trên VM | `watcher/tests/test_recovery_ops.py` | Gate, fence cần bằng chứng, rejoin không bao giờ chạy primary cũ, LSN được kiểm tra trước khi vào SQL, RPC chỉ gọi tên trong danh sách |

Chưa kiểm chứng: Route 53 `GetHealthCheckStatus`, `StopInstances` và `systemd` trên VM thật; SSH qua WireGuard với forced command; K3s `disable-agent` trên release đã pin; `pg_basebackup` qua tunnel; việc ứng dụng tự đổi role mà không restart pod sau promote. Đó là việc của lượt nghiệm thu trên cloud.

## 9. Recovery tự động: rejoin và failback sau khi bạn bật lại EC2

Mặc định tắt. `enable_auto_recovery=true` (cần `enable_auto_failover`) cho watcher làm tiếp sau failover, **sau khi bạn Start lại EC2 bằng tay** (Console hoặc CLI; watcher không có quyền và không bao giờ làm việc đó); `enable_auto_failback=true` (cần `enable_auto_recovery`) cho nó đưa writer về AWS. Cả hai chỉ có ý nghĩa khi đã nghiệm thu failover một chiều.

| Phase | Việc làm | Điều kiện dừng (thoát mã 2) |
|---|---|---|
| `wait-aws` | Kiểm tra Azure là writer đang mở ghi; chờ **không giới hạn thời gian** tới khi bạn đã Start EC2 và RPC trên AWS trả lời (log `Waiting for an operator to start AWS`) | Azure không phải writer; EC2 ở trạng thái lạ (ví dụ `terminated`) |
| `rejoin-aws` | Khởi động K3s **không agent** (guard chỉ được gỡ ở bước này), tạo PVC mới, `pg_basebackup` từ Azure `10.200.29.2` qua WireGuard, chờ streaming và replay kịp, bật app | AWS database là primary thứ hai (stop EC2 rồi dừng); clone/replay quá `catchup_timeout` |
| `complete` | Nếu không bật failback: AWS standby, Azure giữ writer. Kết thúc | |
| `failback-lock` | AWS đã kịp Azure; khóa ghi Azure (advisory lock + `writes_enabled=false`), lấy LSN cuối, chờ AWS replay tới đó | AWS không tới LSN cuối: mở lại ghi Azure rồi dừng |
| `failback-fence` | Scale database Azure về 0 và chứng minh: không còn pod, không còn gì nghe cổng 5432 | Không chứng minh được |
| `failback-promote` | `pg_promote` AWS với `min_lsn`, mở gate, chờ `/api/database/status` của AWS là primary + writable | Lỗi mà AWS còn standby: bật lại database Azure và mở ghi rồi dừng. Cả hai không xác định: dừng, không động vào gì thêm |
| `rebuild-azure` | PVC mới trên Azure, clone từ AWS `10.200.29.1`, chờ streaming và replay kịp | Quá `catchup_timeout` |
| `rearm` | Xóa `failover-incident.json` và `recovery-state.json`; watcher quay lại theo dõi | |

Mỗi phase kiểm tra trạng thái thật trước khi làm và được lưu vào `recovery-state.json` trước khi sang phase sau, nên `systemctl restart dcs29-watcher` chạy tiếp đúng chỗ dừng và dùng lại cùng PVC. `watcher.pause` giữ giữa các phase (không ngắt một phase đang chạy dở). PVC cũ không bao giờ bị xóa.

**Cách Azure gọi AWS.** Installer (từ laptop, SSH sẵn có) sinh key trên Azure rồi đưa public key vào `authorized_keys` của AWS với `from="10.200.29.2"`, một forced command duy nhất (`rpc.py`), không forwarding/pty. Key đó không chạy được lệnh nào khác; key của laptop không được sao sang Azure.

**Giới hạn cần ghi vào báo cáo:**

- Fence Azure ở mức database (scale 0 + cổng 5432 đóng), yếu hơn Stop VM. Có thể scale lại bằng tay trong lúc failback. Watcher không có credential Azure nên không Stop VM được.
- Failback thêm một lần gián đoạn ghi. Nếu AWS promote lỗi sau khi Azure đã bị fence, có khoảng thời gian cả hai không ghi được cho tới khi watcher khôi phục Azure hoặc có người xử lý.
- Watcher chỉ chờ bạn bật EC2, nên có thể để EC2 tắt bao lâu tùy ý; Azure phục vụ trong thời gian đó. Dừng EC2 có chủ đích để kích hoạt failover cũng chính là cách demo: watcher hiểu đó là sự cố thật.
- Chưa nghiệm thu trên cloud (xem mục 8).

**Sau `complete` (không failback):** watcher dừng ở trạng thái Azure writer, AWS standby. Muốn failback sau đó, làm tay theo Phụ lục A. Đặt `enable_auto_failback=true` sau khi đã `complete` không tự chạy tiếp: phase `complete` là điểm kết thúc.

## 10. Azure tự khởi động K3s sau reboot

Guard K3s (`ExecStartPre=test -f /run/issues-k3s-start-approved`) chặn mọi VM sau reboot để database cũ không tự chạy. Với Azure điều này làm vòng tự động đứng im: K3s không lên nên watcher chỉ thấy "busy". `enable_azure_auto_start=true` (cần `enable_auto_failover`) cài dịch vụ `dcs29-unguard` trên **Azure** (không bao giờ trên AWS: AWS reboot được watcher xử lý như một failover).

Dịch vụ chạy lúc boot, thoát ngay nếu K3s đã chạy, và chỉ ghi marker rồi `systemctl start k3s` khi **cả hai** bằng chứng độc lập cho phép:

1. **Role của thư mục dữ liệu local**, đọc từ đĩa vì K3s chưa chạy: PVC lấy từ `/opt/dcs29/postgres.yaml`, tìm `/var/lib/rancher/k3s/storage/pvc-*_issues_<claim>/pgdata`; có `standby.signal` là standby, không có là primary. Không xác định được chắc chắn (thiếu, trùng, rỗng) thì từ chối.
2. **Trạng thái AWS** (chỉ cần khi local là primary): EC2 `stopped` nghĩa là đã fence; nếu đang chạy thì RPC recovery hoặc `/api/database/status` của AWS phải báo `standby` hoặc `down`.

| Local | AWS | Kết quả |
|---|---|---|
| standby | bất kỳ | khởi động (standby không ghi được) |
| primary | fenced, standby, down | khởi động |
| primary | primary | **từ chối** (có thể hai writer) |
| primary | không rõ | **từ chối** |
| rỗng/không rõ | bất kỳ | **từ chối** |

Từ chối thì dịch vụ chờ và kiểm tra lại mỗi 30 giây, chỉ ghi log khi lý do đổi (`journalctl -u dcs29-unguard`). `watcher.pause` là quyền phủ quyết. Khi K3s lên, watcher và recovery tự chạy tiếp từ phase đã lưu.

Giới hạn: nếu reboot đúng giữa lúc `rebuild-azure` (đã apply PVC mới nhưng chưa ghi lại `postgres.yaml`), role đọc ra là primary cũ và AWS là primary nên dịch vụ từ chối; cần người xử lý. Đây là lỗi theo hướng an toàn. Chưa kiểm chứng trên VM thật: đường dẫn lưu trữ của local-path và hành vi khởi động của `k3s.service` sau khi marker được ghi.
