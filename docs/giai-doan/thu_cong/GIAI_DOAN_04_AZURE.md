# Giai đoạn 4 — Azure VM Ubuntu làm standby

**Đầu vào:** điền `GHCR_IMAGE`, `IMAGE_DIGEST`, `AZURE_REGION` và sau khi tạo VM là `AZURE_PUBLIC_IP` trong [bảng thông số](THONG_SO_TRIEN_KHAI.md). Đây là mẫu để triển khai trong subscription của bạn; không dùng thông số của lượt chạy cũ.

**Mục tiêu:** Azure VM chạy K3s độc lập, cùng version K3s/application digest với EC2 AWS; chỉ đổi `APP_ENV=azure` và `APP_REGION` thành vùng Azure thực tế. Xem [hướng dẫn thao tác Azure Portal và SSH đầy đủ](GIAI_DOAN_04_AZURE_PORTAL.md) trước khi thực hiện.

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Nắm các mốc Azure standby; thực hiện bằng Portal guide. |
| Chuẩn bị | Có subscription, quota và image digest chung với AWS. |
| Hoàn tất khi | Azure nhận WAL từ AWS và app chỉ đọc khi còn standby. |

**Luồng thao tác:** [Trình tự thực hiện](#trình-tự-thực-hiện) → [Mốc so sánh với AWS](#mốc-so-sánh-với-aws).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

## Tài nguyên và giới hạn

| Tài nguyên | Cấu hình dự kiến |
|---|---|
| Resource Group | `dcs29-rg`, dùng lại nếu đã có và an toàn |
| Azure VM | `dcs29-azure-vm`, Ubuntu Server 24.04 LTS x64, **Standard_B2s**, 2 vCPU / 4 GiB theo cấu hình đã chốt |
| OS disk | Standard SSD 30 GiB hoặc lớn hơn nếu OS image yêu cầu; không dùng temporary/ephemeral disk cho DB |
| VNet, subnet, NIC, NSG | Tạo cùng VM; SSH 22 chỉ từ IP quản trị, HTTP 80 từ Internet, không mở 8080 |
| Public IP | Standard, Static IPv4 để làm origin ổn định |
| K3s | Single-node độc lập; Traefik → Service app, PostgreSQL standby/PVC riêng |

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

1. Giữ các bước Resource Group phía trên; theo Portal guide mục 2–5 tạo VM/NSG/key/Static IP và SSH.
2. Cài K3s cùng version AWS theo Portal mục 6, không join AWS; kiểm tra node Ready.
3. Theo mục 7 và [hướng dẫn database](GIAI_DOAN_04_K3S_DATABASE.md): namespace, mạng riêng, PVC, AWS primary/schema và Azure base backup/standby.
4. Tạo Secret/Deployment/Service app, cùng digest; không dùng Docker login host thay imagePullSecret.
5. Kiểm tra API/UI/role/WAL, tạo dữ liệu AWS và đối chiếu Azure; standby từ chối ghi.
6. Reboot, xác minh K3s/pods/PVC/streaming; không restart primary cũ sau failover.
7. Ghi evidence/chi phí, Start cả hai VM trước demo; Stop/Deallocate khi nghỉ không xóa dữ liệu.

## Mốc so sánh với AWS

| Trường | Kỳ vọng Azure |
|---|---|
| `commit_sha` | `COMMIT_SHA` của package version GĐ2 đã chọn, giống AWS |
| `database role / WAL` | Azure standby, streaming/replay bắt kịp; so issue/comments/history thực tế |
| `environment` | `azure` |
| `region` | `AZURE_REGION` đúng với Location VM |

Lưu kết quả `/api/database/status`, `/version`, `/api/issues`, cấu hình NSG/Static Public IP, image digest và kết quả reboot **của lần triển khai này**. Chỉ đánh dấu GĐ4 hoàn thành khi các kiểm tra trong [hướng dẫn Portal mục 9](GIAI_DOAN_04_AZURE_PORTAL.md#9-kiểm-tra-và-so-với-aws) pass.

GĐ4 chỉ dùng HTTP để kiểm tra từng origin. [GĐ5](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md) sẽ cấu hình HTTPS/domain chung trên cả hai VM; [GĐ6](GIAI_DOAN_06_ROUTE53_FAILOVER.md) mới làm database và DNS failover. Nếu Azure VM đang deallocated, failover tự động không hoạt động.
