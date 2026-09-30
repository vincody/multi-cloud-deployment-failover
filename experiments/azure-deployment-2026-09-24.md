# Ghi nhận Azure VM — 24/09/2026

Nguồn: [ảnh dashboard](../images/azure-web-2026-09-24.png) do người triển khai cung cấp và các yêu cầu HTTP thực hiện từ bên ngoài Azure VM ngày 24/09/2026. Không lưu username, SSH key hoặc GHCR token trong ghi chú này.

## Kết quả quan sát được

| Mục | Kết quả |
|---|---|
| Public URL | `http://172.198.68.77` |
| Dashboard | Hiển thị **Healthy**, `azure-standby`, `eastasia`, version `sha-5b362a2321713ce8dfef45101bf37e834a21cc73`, fingerprint rút gọn `40242be543f3` |
| `/health/ready` | HTTP 200; `status=ready` |
| `/api/status` | HTTP 200; `status=healthy`, `environment=azure-standby` |
| `/api/devices` | HTTP 200 |
| `/version` | HTTP 200; commit `5b362a2321713ce8dfef45101bf37e834a21cc73`; `dataset_sha256=40242be543f3d25dc7d07a135ef1227c1006b3e60b0cb6e6610818ecd1fbbe9d` |
| TCP 8080 qua Public IP | HTTP request timeout sau khoảng 4 giây; chưa kiểm tra trực tiếp rule NSG |

Commit khớp mốc AWS đã lưu trong [hướng dẫn AWS](../docs/giai-doan/GIAI_DOAN_03_AWS_CONSOLE.md#giá-trị-aws-đã-ghi-nhận-để-đối-chiếu-với-azure). Fingerprint đầy đủ khớp [dữ liệu mẫu GĐ1](../docs/giai-doan/GIAI_DOAN_01_UNG_DUNG_LOCAL.md). Mốc AWS hiện chỉ ghi 12 ký tự đầu fingerprint, nên chưa có bằng chứng hai `/version` trực tiếp trả cùng hash đầy đủ trong một lượt kiểm tra.

## Kiểm tra sau khi sửa APP_REGION

Người triển khai xác nhận **VM → Overview → Location = India South Central** và đã sửa Compose. Kiểm tra trực tiếp từ bên ngoài VM lúc **14:49 UTC ngày 24/09/2026**:

| Endpoint | Kết quả sau khi sửa |
|---|---|
| `/version` | HTTP 200; `environment=azure-standby`, `region=indiasouthcentral`, commit `5b362a2321713ce8dfef45101bf37e834a21cc73`, fingerprint đầy đủ không đổi |
| `/api/status` | HTTP 200; `status=healthy`, `region=indiasouthcentral` |
| `/health/ready` | HTTP 200; `status=ready` |

Ảnh dashboard ở đầu ghi lại trạng thái **trước khi sửa**, nên vẫn hiển thị `eastasia`. API hiện đã khớp Location thật của VM.

## Cần xác nhận trên Azure Portal/VM

1. Kiểm tra Public IP là **Standard / Static / IPv4**, NSG mở 80 từ Internet, SSH 22 chỉ từ IP quản trị, không có rule 8080.
2. Trên VM, chạy `cd /opt/dcs29 && sudo docker compose config --images && sudo docker compose ps` để lưu đường dẫn image theo digest và trạng thái container. Đối chiếu digest `sha256:e48779c88fd265bebcf7bdaabfef39bf47d8498eac49b03ab30e9e61a7d51fe5` với image AWS GĐ2. Cấu hình Compose chưa tự chứng minh image đang chạy; nếu cần bằng chứng mạnh hơn, lưu thêm kết quả inspect container/image.
3. Reboot Azure VM, xác nhận container tự chạy lại và `/health/ready` vẫn trả HTTP 200.
4. Khi AWS EC2 chạy, gọi `/version` ở cả hai Public IP trong cùng lượt và lưu hai hash đầy đủ. Sau đó mới cấu hình domain/HTTPS và DNS failover.

Public IP và trạng thái HTTP có thể thay đổi sau thời điểm ghi nhận; đây là snapshot kiểm thử, không phải cam kết endpoint luôn sẵn sàng.
