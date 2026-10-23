# Lộ trình hiện hành — Issues và Disaster Recovery AWS/Azure

Ứng dụng hiện hành là hệ thống ticket hỗ trợ theo kiểu GitHub Issues. AWS là môi trường chính; Azure là dự phòng với PostgreSQL riêng nhận streaming replication. Mỗi VM chạy một cluster K3s độc lập, không ghép hai VM thành một cluster xuyên cloud.

## Cách dùng bộ hướng dẫn

1. Điền [bảng thông số](THONG_SO_TRIEN_KHAI.md), rồi chọn giai đoạn trong bảng bên dưới.
2. Đọc mục **Đọc nhanh trước khi triển khai** để biết điều kiện đầu vào, thứ tự công việc và kết quả cần đạt. Các liên kết đưa thẳng tới bước chi tiết trong cùng file.
3. **Bắt buộc:** làm các bước cấu hình và kiểm tra của luồng đã chọn; giữ các cảnh báo fencing, credential, storage và startup guard ngay tại bước thao tác.
4. **Tùy chọn:** GĐ6B nếu cần failover tự động; các bước khác chỉ bỏ qua khi hướng dẫn ghi rõ tùy chọn hoặc điều kiện không áp dụng.
5. **Khi gặp lỗi / tham khảo:** mở mục thu gọn cuối file. Lệnh, manifest và hướng dẫn xử lý vẫn được giữ đầy đủ; không cần tìm sang một file xử lý lỗi riêng.

AWS/Azure có trang tổng quan và trang thao tác chi tiết. Khi triển khai, dùng **AWS Console** và **Azure Portal** để đi theo từng bước; trang K3s/database chung là tham khảo, không phải một lượt cấu hình bổ sung. Số mục gốc được giữ để các tham chiếu đang có vẫn dùng được.

## Cấu hình đã chốt

| Thành phần | AWS | Azure |
|---|---|---|
| VM | EC2 `t3.medium` | `Standard_B2s` |
| CPU / RAM | 2 vCPU / 4 GiB | 2 vCPU / 4 GiB |
| OS | Ubuntu Server 24.04 LTS x86_64 | Ubuntu Server 24.04 LTS x64 |
| Disk | EBS gp3 30 GiB ban đầu | Managed OS disk Standard SSD, 30 GiB hoặc lớn hơn theo image |
| Kubernetes | K3s single-node độc lập | K3s single-node độc lập |
| Ứng dụng | Image GHCR chung, cấu hình AWS | Cùng digest GHCR, cấu hình Azure |
| PostgreSQL | Primary, đọc/ghi | Standby, chỉ đọc, nhận WAL |

30 GiB là dung lượng ban đầu cho mỗi VM; Azure dùng lớn hơn nếu image yêu cầu. Không lưu PostgreSQL trên temporary disk Azure. B2s thuộc Bv1 có thông báo retirement; giữ lựa chọn đã chốt, nhưng kiểm tra khả dụng/quota/lifecycle tại region trước khi tạo. Nguồn: [AWS T3](https://aws.amazon.com/ec2/instance-types/t3/), [Azure Bv1](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/bv1-series).

## Trạng thái và thứ tự thực hiện

| Giai đoạn | Nội dung hiện hành | Trạng thái / điều kiện nghiệm thu |
|---|---|---|
| [GĐ1](GIAI_DOAN_01_UNG_DUNG_LOCAL.md) | Issues, hai database, URL chung, failover/rejoin/failback | Đã kiểm chứng local; bảo toàn dữ liệu và bản nháp |
| [GĐ2](GIAI_DOAN_02_IMAGE_VA_CI.md) | GHCR, CI PostgreSQL, cùng digest | Đã phát hành; chọn đúng digest cho lần triển khai |
| [GĐ3](GIAI_DOAN_03_AWS_CONSOLE.md) | Tạo t3.medium, mạng, SSH và K3s | Chưa nghiệm thu VM theo lộ trình này; node Ready sau reboot |
| [GĐ4](GIAI_DOAN_04_AZURE_PORTAL.md) | B2s, K3s, mạng riêng, PostgreSQL và app hai bên | Azure standby nhận đủ dữ liệu, AWS phục vụ ghi |
| [GĐ5](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md) | Ingress, domain, HTTPS | Cùng hostname/TLS dùng được trên hai origin |
| [GĐ6](GIAI_DOAN_06_ROUTE53_FAILOVER.md) | Fencing, promote, DNS, rejoin, failback | Một writer; chuyển tuyến sau DB mới sẵn sàng |
| [GĐ7](GIAI_DOAN_07_OBSERVABILITY.md) | Monitor ngoài hai VM, pods và replication | Thực hiện sau triển khai VM; pod/scale/recovery hiện đúng |
| [GĐ8](GIAI_DOAN_08_KIEM_THU_FAILOVER.md) | Lỗi pod/VM/mạng, đo RTO/RPO | Có bằng chứng dữ liệu và số đo cloud |

Bắt đầu tại [bảng thông số](THONG_SO_TRIEN_KHAI.md). Lượt 1: làm AWS Console mục 1–9 tới namespace/guard và Azure Portal mục 1–7 tới namespace/guard; chưa apply standby. Lượt 2: trao đổi WireGuard public key/cấu hình hai peer, sau đó cấu hình AWS primary/app/schema, rồi Azure clone/standby/app. Sau reboot, startup guard yêu cầu kiểm tra writer và start K3s thủ công. Cài GĐ7 trước buổi diễn tập GĐ6B để có evidence bên ngoài VM; số giai đoạn là nhóm nội dung, không bắt buộc bật controller trước monitoring. GĐ5–GĐ8 là kế hoạch nghiệm thu cloud, chưa xác nhận đã triển khai.

## Code có sẵn và phần cần triển khai

- Có sẵn: ứng dụng, image, schema SQL, role check, replication và điều phối chuyển đổi **local**.
- Hướng dẫn cấu hình tại chỗ: WireGuard, PostgreSQL/PVC/EndpointSlice, Secret/app/schema, standby, Ingress/TLS, startup guard và rejoin. Người triển khai tạo file theo mẫu, thay key/IP/digest và nghiệm thu trên VM.
- Failover tự động chỉ có ở nhánh [Terraform](../tu_dong/AUTOMATION_FAILOVER.md) (watcher trên VM Azure); nhánh thủ công dừng ở failover có kiểm soát bằng lệnh (GĐ6). Chưa nghiệm thu trên cloud.
- Script local và proxy 8090 dùng Compose/state trên một máy; không chạy nguyên xi trên hai VM.
- PVC local không replicate xuyên cloud. K3s restart pod không tự promote DB hoặc chuyển người dùng sang cloud kia.
- Các trang GĐ1–GĐ8 giữ hướng dẫn chi tiết tại chỗ: Console/Portal, SSH, lệnh, cấu hình, kiểm tra và xử lý lỗi đã chỉnh theo Issues/K3s. Terraform hiện có chưa nghiệm thu cho K3s/Issues database.

Khối powershell chạy trên laptop; bash chạy qua SSH ở VM được chỉ định. Thay mọi placeholder trước khi chạy. Không commit credential, kubeconfig, SSH key, password DB hoặc trạng thái runtime. Ghi thông số thực tế và kết quả vào sổ triển khai riêng.

Nguồn: [K3s single-node](https://docs.k3s.io/quick-start), [PostgreSQL replication](https://www.postgresql.org/docs/17/warm-standby.html). Chi tiết local: [LOCAL_REPLICATION.md](../../LOCAL_REPLICATION.md).

## Đường dẫn thao tác chi tiết

- [AWS Console: từng bước VPC/SG/key/EC2/EIP/SSH/K3s](GIAI_DOAN_03_AWS_CONSOLE.md)
- [Azure Portal: từng bước quota/RG/VM/NSG/SSH/K3s](GIAI_DOAN_04_AZURE_PORTAL.md)
- [Domain/IAM/DNS-01/Certbot, Ingress TLS và renewal chi tiết](GIAI_DOAN_05_DOMAIN_VA_HTTPS.md)
- [Prometheus/Grafana, panel, tunnel metrics và pod/scale](GIAI_DOAN_07_OBSERVABILITY.md)

Mục nào phụ thuộc manifest DB/mạng riêng chưa triển khai đều ghi điểm dừng; không đánh dấu cloud đã pass trước khi thực hiện trên VM.

## Các phần đã bổ sung sau rà soát

- AWS Console/Azure Portal: WireGuard key/peer/firewall, PostgreSQL config/PVC/StatefulSet/EndpointSlice, Secret/DSN, startup guard, backup và log limits.
- Failover tự động: xem nhánh [Terraform](../tu_dong/AUTOMATION_FAILOVER.md); không còn hướng dẫn tạo controller bằng Console.
- GĐ6: rejoin trên PVC mới, giữ storage cũ, start server không agent trước khi chuyển manifest, failback sau đồng bộ.
- GĐ7: node exporter, PostgreSQL textfile collector và PromQL cho role/streaming/WAL lag.
- GĐ8: probe CSV chạy trên PowerShell và phạm vi đo workload Issues.
- Mẫu SQL/base backup đã kiểm tra trong container local; WireGuard/K3s/cloud routing và rejoin phải nghiệm thu trên VM thực tế.
