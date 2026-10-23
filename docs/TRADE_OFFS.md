# Độ phức tạp vận hành và trade-off của triển khai multi-cloud

Đề tài 29 yêu cầu ghi lại độ phức tạp vận hành và các trade-off. Tài liệu này gom các quyết định thiết kế của project, lý do chọn và cái giá phải trả. Số liệu cụ thể lấy từ [đo đạc failover](DO_DAC_FAILOVER.md).

## 1. Tóm tắt kiến trúc cần đánh đổi

Một ứng dụng (Issues) chạy trên hai môi trường độc lập: AWS EC2 là primary, Azure VM là dự phòng. Mỗi bên có K3s và PostgreSQL riêng, nối nhau qua WireGuard; Azure nhận WAL bất đồng bộ. Route 53 chuyển traffic dựa trên `/health/database`; việc chuyển database do lệnh `failover`/`switchover` hoặc watcher chạy trên Azure thực hiện.

## 2. Các đánh đổi chính

| Quyết định | Được | Mất |
|---|---|---|
| **Hai database riêng, replication bất đồng bộ** | Mỗi cloud sống độc lập; ghi không bị chậm theo độ trễ giữa hai cloud | RPO > 0: giao dịch chưa kịp sang Azure có thể mất khi AWS sập đột ngột. `switchover` (khóa ghi, chờ replay) thì không mất |
| **Một DB dùng chung thay vì hai** (phương án đã loại) | Đơn giản nhất, không mất dữ liệu | DB thành điểm lỗi duy nhất; failover chỉ còn là chuyển traffic, không chứng minh được gì về dữ liệu |
| **Fence trước, promote sau** | Không có lúc hai primary cùng nhận ghi (split-brain) | Chậm hơn: phải chờ EC2 `stopped` (có thể hàng chục giây); nếu không fence được thì không failover và hệ thống chấp nhận downtime thay vì liều |
| **Cần hai nguồn xác nhận, N lần liên tiếp** | Ít failover nhầm khi mạng chập chờn hoặc một probe lỗi | Phát hiện chậm hơn: xấp xỉ `threshold × (interval + timeout)`. Lần đo local: 11,8–16,7 giây với ngưỡng 3 |
| **Failover tự động một chiều, failback thủ công** | AWS sống lại không tự đẩy hệ thống về dữ liệu cũ | Người vận hành phải rejoin (dựng lại AWS làm standby từ Azure) rồi failback; thời gian chạy chỉ một cloud kéo dài |
| **Route 53 quyết định traffic, watcher quyết định database** | Mỗi bên làm đúng việc: DNS không biết gì về PostgreSQL | Có khoảng cả hai origin đều unhealthy (từ lúc khóa ghi đến lúc Azure promote xong); DNS còn chịu TTL và cache phía client |
| **Watcher trên VM Azure thay cho Step Functions/Lambda** | Ít thành phần (một dịch vụ thay vì sáu dịch vụ AWS), chạy ngoài region cần giám sát, thử được ở local | Watcher là điểm lỗi duy nhất của luồng tự động; cần access key AWS quyền hẹp đặt trên VM Azure |
| **K3s trên mỗi VM, hai cluster độc lập** | Đúng yêu cầu "hai môi trường độc lập"; không có cluster xuyên cloud | Mỗi VM chỉ một node nên không có HA trong cloud; thêm manifest, ingress, chứng chỉ phải vận hành |

## 3. Độ phức tạp vận hành

Mỗi thành phần thêm vào để có failover kéo theo một việc phải vận hành:

| Thành phần | Việc phải làm / rủi ro |
|---|---|
| WireGuard | Quản lý khóa và rule UDP 51820 theo IP peer; mất tunnel là mất replication |
| Streaming replication | Giữ WAL đủ (không có WAL archive/slot, standby ngắt quá lâu phải dựng lại từ base backup); theo dõi LSN |
| Fencing | Bắt buộc mỗi lần failover; K3s có startup guard để VM bị fence không tự khởi động lại thành primary cũ |
| HTTPS | Chứng chỉ riêng cho từng origin và domain chung, gia hạn bằng DNS-01 với credential riêng trên Azure |
| Health check Route 53 | Phải cố ý để standby báo Unhealthy; hiểu sai điều này dễ dẫn tới chuyển DNS sai |
| Watcher | Pause khi bảo trì, rearm sau incident, xoay access key; thoát mã 2 khi cần người xử lý |
| Rejoin và failback | Dựng lại standby trên volume mới, giữ volume cũ để đối chiếu; vài bước thủ công có thứ tự bắt buộc |
| Hai nhà cung cấp cloud | Hai bộ công cụ, quyền, quota, bảng giá; Terraform quản lý cả hai nhưng state và credential nằm ở laptop |

## 4. Điểm lỗi duy nhất còn lại

- **Watcher** (VM Azure): nếu VM Azure sập thì không ai theo dõi, nhưng cũng không còn standby để promote.
- **Route 53 và domain**: dịch vụ DNS là một phụ thuộc chung của cả hai cloud.
- **Laptop vận hành**: Terraform state, SSH key, giám sát và probe đều chạy từ đây trong bản demo.
- **Đường WireGuard**: đứt thì replication dừng và RPO tăng dần; không có đường dự phòng.

## 5. Điều hệ thống không làm

- **Replication không phải backup.** Xóa nhầm hoặc hỏng dữ liệu theo logic sẽ được sao chép sang Azure ngay. Chưa có `pg_dump`/`pg_basebackup` định kỳ ra ngoài VM.
- **Không đảm bảo RPO = 0** khi AWS sập đột ngột.
- **Không chống được cô lập mạng một phần** nếu API fence cũng không gọi được: khi đó watcher dừng thay vì promote.
- **Chưa có xác thực/phân quyền** trong ứng dụng; tài khoản và mật khẩu trong Compose chỉ dành cho demo local.

## 6. Mức độ đã kiểm chứng

| Phần | Trạng thái |
|---|---|
| Replication, switchover, failover, rejoin, failback (local) | Kiểm chứng bằng `scripts/check_local_lifecycle.py`, gồm failback bị dừng giữa chừng và failover sau khi kill database AWS |
| Quyết định của watcher | Unit test cho cả bản local và bản cloud; bản local chạy end to end |
| Terraform | `terraform validate` và `terraform test` (plan với provider giả) |
| Triển khai trên hai VM thật, Route 53 health check, `StopInstances`, `systemd` | **Chưa nghiệm thu** |

Số đo local và cloud không so sánh trực tiếp: cloud còn có chu kỳ health check, TTL DNS và thời gian dừng EC2.
