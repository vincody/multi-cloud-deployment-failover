# Giai đoạn 8 — Kiểm thử failover, phân tích và báo cáo

**Đầu vào:** hoàn thành GĐ1–GĐ7 với thông số của bạn trong [bảng triển khai](THONG_SO_TRIEN_KHAI.md). Chỉ dùng số đo, domain, digest và IP của chính lần thử này.
**Mục tiêu:** chạy thí nghiệm lặp lại được và tính detection time, failover time, downtime, error rate, latency và cost proxy.

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Chạy các kịch bản lỗi, lưu evidence và phân tích RTO/RPO. |
| Chuẩn bị | Baseline đạt, monitoring chạy; chọn rõ chế độ thủ công hoặc tự động. |
| Hoàn tất khi | Có request/ACK, timeline và số đo của chính lượt thử. |

**Luồng thao tác:** [1. Điều kiện trước khi chạy](#1-điều-kiện-trước-khi-chạy) → [2. Workload Issues và kiểm chứng dữ liệu](#2-workload-issues-và-kiểm-chứng-dữ-liệu) → [15. Probe CSV có thể tự chạy trên laptop](#15-probe-csv-có-thể-tự-chạy-trên-laptop) → [16. Workload ghi Issues nhẹ, có lưu request và ACK](#16-workload-ghi-issues-nhẹ-có-lưu-request-và-ack) → [4. Probe song song](#4-probe-song-song) → [5. Kịch bản phải chạy](#5-kịch-bản-phải-chạy) → [6. Quy trình một lượt F2](#6-quy-trình-một-lượt-f2) → [8. Tổ chức output](#8-tổ-chức-output) → [11. Cleanup sau thí nghiệm](#11-cleanup-sau-thí-nghiệm) → [12. Checklist nghiệm thu](#12-checklist-nghiệm-thu).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

## 1. Điều kiện trước khi chạy

- AWS/Azure cùng digest, khác APP_ENV.
- Hai origin và shared HTTPS pass.
- Writer check AWS healthy, Azure standby check unhealthy đúng role; process/TLS probe hai origin healthy.
- Monitoring nằm ngoài AWS.
- Máy k6/probe không sleep, clock đồng bộ.
- Budget và cleanup owner được xác nhận.
- Run manifest ghi commit, digest, TTL, health interval/threshold, replica, client DNS mode và tải; ghi chế độ **manual GĐ6A** hoặc **automatic GĐ6B**. Không trộn hai chế độ trong cùng nhóm số đo.

Không thay đổi image/TTL/replica giữa các lượt so sánh. Trong run manual/B0/F1 hoặc khi bảo trì, tạo `watcher.pause` trên VM Azure nếu watcher đang chạy. Trong run automatic F2/F4, kiểm tra `systemctl status dcs29-watcher` active, baseline AWS primary/Azure standby streaming, đã xóa `failover-incident.json` của lượt trước; không promote tay song song. Khi kết thúc lỗi, tạo lại `watcher.pause` trước rejoin/failback, rồi rearm cho lượt tiếp theo.

## 2. Workload Issues và kiểm chứng dữ liệu

### 2.1. Tạo issue/bình luận qua API — PowerShell laptop

Dùng dữ liệu demo và SHARED_HOST thật. Chạy khi DB đang là primary đã nghiệm thu:

```powershell
$BaseUrl = 'https://app.YOUR_DOMAIN'
$CreateRequestId = [guid]::NewGuid().ToString()
$CreateBody = @{request_id=$CreateRequestId; author='Demo'; title='Kiem thu AWS Azure'; description='Noi dung truoc failover'; status='open'; version=0} | ConvertTo-Json
$Issue = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/issues" -ContentType 'application/json' -Body $CreateBody
$IssueId = $Issue.id
$CommentRequestId = [guid]::NewGuid().ToString()
$CommentBody = @{request_id=$CommentRequestId; author='Demo'; body='Binh luan truoc failover'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/issues/$IssueId/comments" -ContentType 'application/json' -Body $CommentBody
Invoke-RestMethod "$BaseUrl/api/issues/$IssueId" | ConvertTo-Json -Depth 20
```

Ghi IssueId, request_id, version, response và UTC. Retry cùng thao tác dùng lại body/request_id; giao dịch mới phải có mã mới. Sửa issue dùng PUT /api/issues/{id} cùng version hiện tại, không đổi trạng thái bằng dữ liệu version cũ.

### 2.2. Dữ liệu trong vòng chuyển đổi

1. Trước lỗi: đợi Azure replay và so đầy đủ issue/bình luận/lịch sử.
2. Gây lỗi VM, dùng runbook GĐ6A hoặc quan sát controller GĐ6B theo chế độ đã chọn; ghi request đã ACK và request kết quả chưa rõ.
3. Trên Azure mới: bình luận/sửa/đóng issue, retry kiểm tra không trùng.
4. AWS rejoin: dữ liệu mới Azure phải có trên AWS trước failback.
5. Sau failback: so lại ID/version/comments/history/receipts và thử giao dịch mới trên AWS.
6. Giữ bản nháp chưa gửi trong browser cùng URL để kiểm tra localStorage riêng với transaction database.

### 2.3. Tải và script cũ

Script tests/load/failover.js hiện gọi /api/devices, chỉ kiểm chứng workload dashboard/read-only. Có thể giữ phép đo HTTP 10 request/giây trong 10 phút để tham khảo, nhưng không lấy đó làm bằng chứng replication giao dịch Issues.

Mục 16 có workload tạo Issues tuần tự, UUID riêng và log before_send/ACK để demo; bài stress-test ghi đồng thời có version/receipt vẫn cần thiết kế riêng nếu đo capacity. Nếu không viết load script, vẫn làm đủ vòng nghiệp vụ/role/WAL phía trên và ghi phạm vi phép đo. Khi tăng tải theo dõi dropped_iterations, CPU credit và RAM 4 GiB.

## 3. Hai chế độ DNS/client

### Client mặc định

Giữ DNS/connection settings k6 mặc định, ghi rõ k6 version và resolver. Kết quả phản ánh client đó, không đại diện mọi browser.

### DNS experiment

Chạy thêm cấu hình TTL nội bộ ngắn và noConnectionReuse để quan sát authoritative change rõ hơn. Resolver OS/network vẫn có cache. Không so latency giữa hai cấu hình rồi quy toàn bộ chênh lệch cho cloud.

## 15. Probe CSV có thể tự chạy trên laptop

PowerShell laptop, tạo file `probe.ps1` ngoài repo hoặc trong thư mục run đã gitignore. Dán script, thay domain; chạy trong terminal riêng suốt demo:

```powershell
$BaseUrl = 'https://app.YOUR_DOMAIN'
$OutputFile = Join-Path $PWD 'probe.csv'
if (Test-Path $OutputFile) { throw 'Chon file output moi, khong ghi de raw data.' }
$EndTime = (Get-Date).AddMinutes(15)
while ((Get-Date) -lt $EndTime) {
    $Started = (Get-Date).ToUniversalTime().ToString('o')
    $Watch = [Diagnostics.Stopwatch]::StartNew()
    $Status = 0; $Environment = ''; $Problem = ''
    try {
        $Response = Invoke-WebRequest -Uri "$BaseUrl/version" -TimeoutSec 5 -UseBasicParsing
        $Status = [int]$Response.StatusCode
        $Environment = ($Response.Content | ConvertFrom-Json).environment
    } catch {
        $Problem = $_.Exception.Message
        if ($_.Exception.Response) { $Status = [int]$_.Exception.Response.StatusCode }
    }
    $Watch.Stop()
    [pscustomobject]@{started_at_utc=$Started; completed_at_utc=(Get-Date).ToUniversalTime().ToString('o'); status=$Status; environment=$Environment; duration_ms=$Watch.ElapsedMilliseconds; error=$Problem} | Export-Csv -Path $OutputFile -Append -NoTypeInformation -Encoding UTF8
    Start-Sleep -Milliseconds ([Math]::Max(0,1000-$Watch.ElapsedMilliseconds))
}
```

Request timeout có thể dài 5 giây nên không đảm bảo mỗi giây một sample khi lỗi. Probe này chỉ đọc /version; RTO ghi cần thêm giao dịch Issues theo mục 2. Mỗi thao tác ghi tạo UUID mới và giữ UUID/body khi kết quả chưa rõ. Không xem code k6 /api/devices là workload Issues.

### Workload Issues thủ công đủ kiểm chứng replication

Giữ một issue đã tạo ở mục 2 và IssueId, lặp: đọc version hiện tại → PUT sửa với request_id mới/version hiện tại → POST comment UUID mới → đọc detail ở Azure. Lưu cả request_id/body trước khi gửi và response ACK. Sau failover, retry cùng request_id/body nếu chưa biết kết quả; thao tác mới dùng UUID mới. HTTP 409 do version cũ thì đọc lại, không sửa version rồi gửi như một retry của request cũ.

Nếu chưa xây load generator Issues, ghi rõ báo cáo chỉ đo probe/read workload và chuỗi giao dịch thủ công; không tuyên bố đã stress-test ghi liên tục. Đây là phần mở rộng hiệu năng, không phải bước chặn cấu hình hệ thống.


## 16. Workload ghi Issues nhẹ, có lưu request và ACK

Đây là workload tuần tự để demo failover, không phải benchmark capacity. PowerShell laptop, tạo `issues-workload.ps1` trong thư mục run, thay domain và chạy song song probe ở mục 15:

```powershell
$BaseUrl = 'https://app.YOUR_DOMAIN'
$LogFile = Join-Path $PWD 'issues-writes.jsonl'
if (Test-Path $LogFile) { throw 'Chon run moi, khong ghi de log.' }
$Finish = (Get-Date).AddMinutes(10)
$Sequence = 0
while ((Get-Date) -lt $Finish) {
    $Sequence++
    $RequestId = [guid]::NewGuid().ToString()
    $Body = @{request_id=$RequestId; author='Demo'; title="Failover sample $Sequence"; description="Write $RequestId"; status='open'; version=0} | ConvertTo-Json
    @{phase='before_send'; utc=(Get-Date).ToUniversalTime().ToString('o'); request_id=$RequestId; method='POST'; path='/api/issues'; body=$Body} | ConvertTo-Json -Compress | Add-Content -Path $LogFile -Encoding UTF8
    try {
        $Result = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/issues" -ContentType 'application/json' -Body $Body -TimeoutSec 5
        @{phase='ack'; utc=(Get-Date).ToUniversalTime().ToString('o'); request_id=$RequestId; response=$Result} | ConvertTo-Json -Depth 20 -Compress | Add-Content -Path $LogFile -Encoding UTF8
    } catch {
        @{phase='unknown_or_failed'; utc=(Get-Date).ToUniversalTime().ToString('o'); request_id=$RequestId; error=$_.Exception.Message} | ConvertTo-Json -Compress | Add-Content -Path $LogFile -Encoding UTF8
    }
    Start-Sleep -Seconds 5
}
```

Khi timeout, body/request_id đã nằm trong log; chưa kết luận giao dịch mất. Sau phục hồi, tìm issue theo request đã ghi nhận hoặc retry **chính body/request_id** từ before_send. Không tạo UUID mới cho retry. Đối chiếu các response ACK/issue ID trên Azure, rồi trên AWS sau rejoin/failback. Script này chỉ tạo issue; sửa/bình luận/đóng và version conflict kiểm thử riêng theo mục 2.

RPO tính từ giao dịch đã ACK nhưng không còn trên primary mới; các request unknown phải tra cứu/retry trước khi phân loại. Không suy ra RPO từ probe /version hoặc số error của HTTP.
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
| F1 | App pod failure | Rollout/recreate pod app K3s | Kiểm tra hồi phục tại cluster, không mặc định promote DB |
| F2 | Environment failure chính | Stop EC2 AWS | Đáp ứng yêu cầu đề tài |
| F3 | Recovery/failback | AWS bị chặn writer cũ, rejoin rồi chuyển role | Đo trở về primary sau đồng bộ |
| F4 | Failover dưới tải | Tăng tải có kiểm soát rồi stop | Đo capacity/tail latency Azure |
| F5 | Readiness 503 | Process sống nhưng not ready | Kiểm tra decision signal |

F2 là bằng chứng chính; F5 không thay thế F2. Mỗi scenario tối thiểu 3 lượt khởi đầu, đợi process probes hai origin healthy, AWS primary/Azure standby streaming giữa các lượt.

## 6. Quy trình một lượt F2

1. Tạo run ID, thư mục output và manifest.
2. Xác nhận version/digest/role/WAL và dữ liệu Issues hai origin.
3. Xác nhận writer checks đúng role, process probes và Grafana có dữ liệu.
4. Bắt đầu Prometheus/Grafana, probe CSV và k6.
5. Chạy baseline đủ 3 phút.
6. Ghi UTC gửi lệnh stop EC2 (`t_action`).
7. Ghi external origin down (`t0`).
8. Ghi Route 53 unhealthy (`t_detect`).
9. Xác nhận EC2 stopped trước promote. Run manual làm theo GĐ6A; run automatic theo dõi `journalctl -u dcs29-watcher`, không promote bằng tay. Xác nhận Azure writer readiness; ghi response Azure đầu tiên qua shared domain (t_first_B) và giao dịch ghi đầu tiên.
10. Ghi 10 success liên tiếp (`t_stable`).
11. Tiếp tục đo Azure ổn định.
12. AWS trở lại trong trạng thái chặn primary cũ; rejoin, chờ dữ liệu/WAL rồi failback có kiểm soát theo GĐ6.
13. Dừng tải, export raw data/dashboard/log.
14. Xác nhận resources và chi phí; không dọn trước khi lưu evidence.

Lệnh stop/start chỉ dùng đúng instance lab đã xác minh bằng describe. Gửi stop không đồng nghĩa instance down ngay.

## 8. Tổ chức output

Mỗi run lưu:

- `manifest.yaml`: scenario/config/digest/TTL, controller_mode, alarm period/evaluation và rule state.
- Nếu automatic: log `journalctl -u dcs29-watcher` (phát hiện, fence, promote), `failover-incident.json` và xác nhận EC2 stopped trước promote.
- `incident.csv`: event và UTC.
- `probe.csv`.
- `k6.json` hoặc summary.
- Prometheus/Grafana export.
- AWS/Azure/Route 53 snapshots.
- `analysis.md` nêu kết quả và anomaly.

Raw data là bất biến; tạo file tổng hợp riêng. Không chỉnh raw CSV để làm kết quả đẹp.

## 11. Cleanup sau thí nghiệm

Export evidence trước. Nếu đã cài watcher, `sudo systemctl disable --now dcs29-watcher` trước khi xóa VM (mục 9.2 bước 6 của hướng dẫn Terraform); không để dịch vụ chạy trong lúc xóa VM. Dừng/xóa đúng resources theo inventory: EC2, EBS nếu không cần, Elastic IP/public IPv4, Route 53 checks/records, Azure VM/managed disk/NIC/Public IP/Resource Group, log retention. Không xóa hosted zone/domain/tài nguyên dùng chung nếu chưa xác nhận.

## 12. Checklist nghiệm thu

- [ ] Có B0, B1, F2, F3; F2 chạy ít nhất 3 lượt.
- [ ] Probe/k6/monitor chạy ngoài AWS.
- [ ] Timeline đủ `t_action/t0/t_detect/t_first_B/t_stable`.
- [ ] Có error rate/downtime/latency, RTO ghi và RPO thực tế.
- [ ] Có issue/comments/history/receipt trước-sau, không hai writer.
- [ ] AWS nhận đủ dữ liệu Azure trước failback; standby streaming lại cuối vòng.
- [ ] Có raw data và run manifest.
- [ ] Có DNS authoritative vs recursive evidence.
- [ ] Có cost proxy.
- [ ] Phân tích outlier/limitations.
- [ ] Có runbook recovery/cleanup; run automatic có log watcher và rearm giữa các lượt.
- [ ] Có video hoặc dữ liệu dự phòng cho demo.

## 14. Kiểm chứng storage và giới hạn

Sau nghiệm thu primary/standby/dữ liệu mới, kiểm tra inventory PV/path/DB cũ và dung lượng. Chỉ dọn đúng bản cũ không phục vụ; không xóa PVC active hoặc prune cả cụm. Async replication có thể mất giao dịch cuối khi VM sập. Request chưa phản hồi cần kiểm tra receipt trước khi kết luận mất; bản nháp chưa save không tính là giao dịch mất.

Các mốc read-only/DNS của script cũ giữ để đối chiếu, nhưng báo cáo hiện hành phải tách chúng khỏi RTO/RPO của Issues/PostgreSQL. Không đánh dấu cloud đã pass từ kết quả local.


---

**Tham khảo / xử lý lỗi:** chỉ mở mục cần tra cứu. Nếu bước đang làm báo lỗi, dừng tại điểm kiểm tra và xem hướng dẫn tương ứng.

## 7. Định nghĩa metric

<details>
<summary>Tham khảo — mở nội dung chi tiết</summary>

| Chỉ số | Công thức/định nghĩa |
|---|---|
| Detection time | `t_detect - t0` |
| First-Azure failover | `t_first_B - t0` |
| Stable recovery | t_stable - t0, kèm xác minh giao dịch ghi hoạt động |
| RTO ghi | Thời điểm shared URL xác nhận giao dịch ghi thành công sau lỗi trừ t0 |
| RPO | Giao dịch primary cũ đã ACK commit nhưng không có trên primary mới; ghi số lượng và khoảng thời gian |
| Error rate | failed HTTP/network requests / total requests trong cửa sổ |
| Client downtime | tổng và khoảng lỗi liên tục dài nhất |
| Latency | p50/p95/p99 cho baseline, transition, Azure steady |
| Cost proxy | giờ chạy × vCPU/RAM/storage/IP/query/log/request |

Với probe 1 giây, độ phân giải xấp xỉ 1 giây cộng thời gian request. Không gọi first Azure là phục hồi hoàn toàn nếu request cũ vẫn lỗi.

</details>

## 9. Phân tích kết quả

<details>
<summary>Tham khảo — mở nội dung chi tiết</summary>

Báo từng lượt, trung bình, min/max và outlier. Tách ba cửa sổ: baseline, transition, Azure steady. So B0 với F2 để cho thấy giá trị standby. So latency AWS/Azure nhưng ghi region/network/client. Báo cả request count thực tế và dropped iterations.

Kết luận cần trả lời:

- Failover mất bao lâu ở authoritative DNS và ở client?
- Bao nhiêu request lỗi?
- Azure có chịu được tải?
- Failback có gây lỗi lần hai?
- Chi phí warm standby là gì?
- Những giới hạn nào ngăn kiến trúc trở thành production HA?

</details>

## 10. Lỗi/threats to validity

<details>
<summary>Khi gặp lỗi — mở hướng dẫn</summary>

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

</details>

## 13. Kết quả mong đợi

<details>
<summary>Tham khảo — mở nội dung chi tiết</summary>

Sản phẩm cuối không chỉ là website đổi từ AWS sang Azure, mà là bộ bằng chứng định lượng cho thấy failover diễn ra thế nào, mất bao lâu, lỗi bao nhiêu, tốn gì và giới hạn ở đâu.

</details>
