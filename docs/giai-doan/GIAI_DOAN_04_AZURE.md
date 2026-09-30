# Giai đoạn 4 — Azure VM Ubuntu làm standby

**Đầu vào:** điền `GHCR_IMAGE`, `IMAGE_DIGEST`, `AZURE_REGION` và sau khi tạo VM là `AZURE_PUBLIC_IP` trong [bảng thông số](THONG_SO_TRIEN_KHAI.md). Đây là mẫu để triển khai trong subscription của bạn; không dùng thông số của lượt chạy cũ.

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

Subscription và policy quyết định region/VM size nào được dùng. Chọn `AZURE_REGION` được phép, rồi đặt `APP_REGION` bằng **mã Location thật** trong **VM → Overview**. VM, VNet, NIC, NSG, Public IP và disk phải ở vùng phù hợp. [Microsoft: quota VM](https://learn.microsoft.com/en-us/azure/virtual-machines/quotas).

## Tạo Resource Group

Trước khi tạo VM, vào **Azure Portal → Resource groups → Create** và điền:

| Trường | Giá trị |
|---|---|
| Subscription | Subscription bạn có quyền tạo VM |
| Resource group | `dcs29-rg` |
| Region | Vùng bạn chọn cho Resource Group; có thể dùng `AZURE_REGION` |
| Tags (nếu dùng) | `Project=dcs29`, `Owner=<tên nhóm>` |

Chọn **Review + create → Create**, đợi tạo xong rồi xác nhận `dcs29-rg` trong danh sách Resource groups. Nếu nhóm này **đã tồn tại**, mở để kiểm tra subscription và tài nguyên bên trong trước khi dùng lại. Region của Resource Group lưu metadata và có thể khác region của VM. [Microsoft: tạo Resource Group](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/manage-resource-groups-portal).

## Trình tự thực hiện

1. Tạo hoặc kiểm tra `dcs29-rg` theo mục trên; không xóa tài nguyên đang dùng trong nhóm có sẵn.
2. Tạo VM, disk, VNet/subnet, NSG và Static Public IP trong vùng được phép. Dùng SSH public key; giữ private key ngoài repo.
3. SSH vào VM, cài Docker Engine + Compose plugin, bật Docker service.
4. Tại `/opt/dcs29`, tạo `compose.yaml` và `nginx.conf` theo [mục 7 của hướng dẫn Portal](GIAI_DOAN_04_AZURE_PORTAL.md#7-tạo-compose-và-nginx). Dùng image GHCR cố định bằng digest `sha256:IMAGE_DIGEST`.
5. Nếu GHCR private, đăng nhập bằng username có quyền đọc package và token classic `read:packages` qua `docker login --password-stdin`; không lưu token trong Markdown/ảnh. `docker compose pull`, `docker compose up -d --pull never`.
6. Kiểm tra `http://AZURE_PUBLIC_IP/health/ready`, `/version`, `/api/devices` và dashboard; reboot VM để xác nhận Compose tự chạy lại.
7. Trước demo failover, bật **cả AWS EC2 lẫn Azure VM** và xác nhận health của cả hai. Sau demo có thể deallocate VM để dừng phí compute; disk/Public IP vẫn có thể phát sinh phí. [Microsoft: VM states/billing](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing).

## Mốc so sánh với AWS

| Trường | Kỳ vọng Azure |
|---|---|
| `commit_sha` | `COMMIT_SHA` của run GĐ2 vừa chọn, giống AWS |
| `dataset_sha256` | Giá trị đầy đủ giống AWS; gọi `/version` của cả hai khi hai VM đang chạy |
| `environment` | `azure-standby` |
| `region` | `AZURE_REGION` đúng với Location VM |

Lưu kết quả `/health/ready`, `/version`, `/api/devices`, cấu hình NSG/Static Public IP, image digest và kết quả reboot **của lần triển khai này**. Chỉ đánh dấu GĐ4 hoàn thành khi các kiểm tra trong [hướng dẫn Portal mục 9](GIAI_DOAN_04_AZURE_PORTAL.md#9-kiểm-tra-và-so-sánh-với-aws) pass.

GĐ4 chỉ dùng HTTP để kiểm tra từng origin. [GĐ5](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md) sẽ cấu hình HTTPS/domain chung trên cả hai VM; [GĐ6](GIAI_DOAN_06_ROUTE53_FAILOVER.md) mới làm DNS failover. Nếu Azure VM đang deallocated, failover tự động không hoạt động.
