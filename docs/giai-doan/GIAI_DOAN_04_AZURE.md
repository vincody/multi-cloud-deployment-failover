# Giai đoạn 4 — Azure VM Ubuntu làm standby

**Trạng thái (24/09/2026):** đã chuyển từ Azure Container Apps sang Ubuntu VM. [Ảnh dashboard trước khi sửa region](../../images/azure-web-2026-09-24.png) và kiểm tra HTTP qua Public IP xác nhận app Azure đang healthy, trả `azure-standby`, đúng commit và fingerprint dữ liệu mẫu. Người triển khai xác nhận **VM ở India South Central**; sau khi sửa Compose, `/version` và `/api/status` đã trả `indiasouthcentral`. Digest container đang chạy và kết quả reboot vẫn cần xác nhận; xem [bảng kiểm tra GĐ4](GIAI_DOAN_04_AZURE_PORTAL.md#9-kiểm-tra-và-so-sánh-với-aws).

**Mục tiêu:** Azure VM chạy cùng Docker Compose và image digest với EC2 AWS; chỉ đổi `APP_ENV=azure-standby` và `APP_REGION` thành vùng Azure thực tế. Xem [hướng dẫn thao tác Azure Portal và SSH đầy đủ](GIAI_DOAN_04_AZURE_PORTAL.md) trước khi thực hiện.

## Tài nguyên và giới hạn

| Tài nguyên | Cấu hình dự kiến |
|---|---|
| Resource Group | `dcs29-rg`, dùng lại nếu đã có và an toàn |
| Azure VM | `dcs29-azure-vm`, Ubuntu Server 24.04 LTS x64, size B1ms nếu quota/capacity cho phép |
| OS disk | Standard SSD khoảng 30 GiB, không dùng ephemeral OS disk |
| VNet, subnet, NIC, NSG | Tạo cùng VM; SSH 22 chỉ từ IP quản trị, HTTP 80 từ Internet, không mở 8080 |
| Public IP | Standard, Static IPv4 để làm origin ổn định |
| Docker Compose | `app` FastAPI 8080 nội bộ + NGINX công khai cổng 80 |

Subscription Azure for Students hiện cho phép `koreacentral`, `malaysiawest`, `indiasouthcentral`, `japaneast`, `eastasia`. **VM hiện tại ở India South Central (`indiasouthcentral`)**, theo **VM → Overview → Location** do người triển khai xác nhận. Sau khi sửa `APP_REGION` trong Compose ngày 24/09/2026, API đã trả đúng `indiasouthcentral` theo [kiểm tra HTTP ở mục 9](GIAI_DOAN_04_AZURE_PORTAL.md#9-kiểm-tra-và-so-sánh-với-aws). Việc chuyển sang VM không bỏ qua policy giới hạn vùng. [Microsoft: quota VM](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas).

## Tạo Resource Group

Trước khi tạo VM, vào **Azure Portal → Resource groups → Create** và điền:

| Trường | Giá trị |
|---|---|
| Subscription | Azure for Students đã chọn |
| Resource group | `dcs29-rg` |
| Region | **East Asia** (`eastasia`), hoặc vùng dự phòng đã chọn cho VM |
| Tags (nếu dùng) | `Project=dcs29`, `Owner=<tên nhóm>` |

Chọn **Review + create → Create**, đợi tạo xong rồi xác nhận `dcs29-rg` trong danh sách Resource groups. Nếu nhóm này **đã tồn tại**, mở để kiểm tra subscription và các tài nguyên còn lại từ lần thử Container Apps; dùng lại nếu đúng nhóm, không xóa tài nguyên đang dùng. Region của Resource Group lưu metadata và có thể khác region của VM. [Microsoft: tạo Resource Group](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/manage-resource-groups-portal).

## Trình tự thực hiện

1. Tạo hoặc kiểm tra `dcs29-rg` theo mục trên; xác nhận các tài nguyên còn lại từ lần tạo Container Apps lỗi và không xóa tài nguyên đang dùng.
2. Tạo VM, disk, VNet/subnet, NSG và Static Public IP trong vùng được phép. Dùng SSH public key; giữ private key ngoài repo.
3. SSH vào VM, cài Docker Engine + Compose plugin, bật Docker service.
4. Tại `/opt/dcs29`, tạo `compose.yaml` và `nginx.conf` theo [mục 7 của hướng dẫn Portal](GIAI_DOAN_04_AZURE_PORTAL.md#7-tạo-compose-và-nginx). Dùng image GHCR cố định bằng digest `sha256:e48779c88fd265bebcf7bdaabfef39bf47d8498eac49b03ab30e9e61a7d51fe5`.
5. Nếu GHCR private, đăng nhập bằng username có quyền đọc package và token classic `read:packages` qua `docker login --password-stdin`; không lưu token trong Markdown/ảnh. `docker compose pull`, `docker compose up -d --pull never`.
6. Kiểm tra `http://AZURE_PUBLIC_IP/health/ready`, `/version`, `/api/devices` và dashboard; reboot VM để xác nhận Compose tự chạy lại.
7. Trước demo failover, bật **cả AWS EC2 lẫn Azure VM** và xác nhận health của cả hai. Sau demo có thể deallocate VM để dừng phí compute; disk/Public IP vẫn có thể phát sinh phí. [Microsoft: VM states/billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing).

## Mốc so sánh với AWS

| Trường | Kỳ vọng Azure |
|---|---|
| `version` | `sha-5b362a2321713ce8dfef45101bf37e834a21cc73` |
| `dataset_sha256` | Bắt đầu `40242be543f3`; để so hash đầy đủ, gọi lại AWS khi EC2 chạy |
| `environment` | `azure-standby` |
| `region` | `indiasouthcentral` đã xác nhận sau khi sửa `APP_REGION` |

### Bằng chứng hiện có

Ngày 24/09/2026, địa chỉ `http://172.198.68.77` mở được dashboard Azure. `/health/ready`, `/api/status`, `/api/devices` và `/version` đều trả HTTP 200. `/version` trả `environment=azure-standby`, `region=eastasia` (**sai so với Location VM đã xác nhận**), commit `5b362a2321713ce8dfef45101bf37e834a21cc73` và `dataset_sha256=40242be543f3d25dc7d07a135ef1227c1006b3e60b0cb6e6610818ecd1fbbe9d`. Thử kết nối công khai cổng 8080 bị timeout. Đây là bằng chứng ứng dụng chạy qua HTTP; chưa thay thế cho kiểm tra cấu hình VM/NSG, image digest hoặc reboot. Chi tiết lưu ở [GĐ4 Portal, mục 9](GIAI_DOAN_04_AZURE_PORTAL.md#9-kiểm-tra-và-so-sánh-với-aws).

Sau khi sửa Compose, kiểm tra lúc 14:49 UTC cùng ngày xác nhận `/version`, `/api/status`, `/health/ready` đều HTTP 200 và hai API đầu trả `region=indiasouthcentral`. Commit và fingerprint đầy đủ không đổi. Ảnh dashboard ở trên ghi lại trạng thái trước khi sửa.

GĐ4 chỉ dùng HTTP để kiểm tra từng origin. [GĐ5](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md) sẽ cấu hình HTTPS/domain chung trên cả hai VM; [GĐ6](GIAI_DOAN_06_ROUTE53_FAILOVER.md) mới làm DNS failover. Nếu Azure VM đang deallocated, failover tự động không hoạt động.
