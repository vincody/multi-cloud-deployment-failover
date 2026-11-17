# Triển khai tự động

## Bắt đầu từ đâu

Chỉ cần đọc **một file chính**, theo thứ tự các mục: [Triển khai bằng Terraform](TRIEN_KHAI_BANG_TERRAFORM.md).

| Thứ tự | Việc cần làm | Mục trong file chính |
|---|---|---|
| 1 | Chuẩn bị công cụ, tài khoản, SSH key, image, K3s version | [2](TRIEN_KHAI_BANG_TERRAFORM.md#2-chuẩn-bị-trên-laptop) |
| 2 | **Lượt 1** — hạ tầng, database, app (HTTP) | [3](TRIEN_KHAI_BANG_TERRAFORM.md#3-lượt-1--hạ-tầng-database-và-app-http) |
| 3 | **Lượt 2** — domain và HTTPS | [4](TRIEN_KHAI_BANG_TERRAFORM.md#4-lượt-2--domain-và-https) |
| 4 | **Lượt 3–4** — health check, DNS failover | [5](TRIEN_KHAI_BANG_TERRAFORM.md#5-lượt-3-và-4--health-check-và-dns-failover) |
| 5 | Giám sát | [6](TRIEN_KHAI_BANG_TERRAFORM.md#6-giám-sát-prometheusgrafana) |
| 6 | Diễn tập failover/failback, demo, đo RTO/RPO | [7](TRIEN_KHAI_BANG_TERRAFORM.md#7-diễn-tập-demo-và-nghiệm-thu) |
| 7 | **Lượt 5 (tùy chọn)** — failover tự động bằng watcher | [8](TRIEN_KHAI_BANG_TERRAFORM.md#8-lượt-5-tùy-chọn--failover-tự-động-aws--azure-watcher) |
| 8 | Cập nhật image, dọn lab | [9](TRIEN_KHAI_BANG_TERRAFORM.md#9-cập-nhật-image-bảo-vệ-state-và-dọn-lab) |

Khi cần: [Phụ lục A — runbook failover/rejoin/failback thủ công](TRIEN_KHAI_BANG_TERRAFORM.md#phụ-lục-a--runbook-failover-rejoin-và-failback-thủ-công) · [Phụ lục B — lỗi, reboot, migration](TRIEN_KHAI_BANG_TERRAFORM.md#phụ-lục-b--xử-lý-lỗi-reboot-và-migration).

## Tài liệu kỹ thuật kèm theo (không bắt buộc đọc để triển khai)

Mô tả cơ chế bên trong của từng phần automation; đọc khi cần hiểu hoặc gỡ lỗi.

- [Automation WireGuard, PostgreSQL và Issues](AUTOMATION_WIREGUARD_POSTGRESQL_APP.md): trình tự, file/cấu hình, credential, retry, giới hạn và kiểm thử.
- [Automation HTTPS](AUTOMATION_HTTPS.md): credential, DNS, cấp certificate, Ingress, renewal và retry.
- [Failover tự động bằng watcher](AUTOMATION_FAILOVER.md): watcher trên VM Azure, hai nguồn xác nhận, fencing trước promote, quyền IAM hẹp và vận hành.

## Phạm vi

Terraform hiện tạo hạ tầng, bootstrap K3s và tự triển khai WireGuard, PostgreSQL AWS primary/Azure standby, schema, Secret, app và HTTP Ingress. HTTPS/renewal được tự cấu hình khi bật `enable_https`. Delegation domain, key DNS Azure, backup và monitoring vẫn cần thực hiện theo hướng dẫn. GĐ6B cài watcher trên VM Azure khi bật `enable_auto_failover`: watcher tự fence AWS/promote Azure. Rejoin và failback vẫn thủ công, trừ khi bật thêm `enable_auto_recovery` / `enable_auto_failback` (opt-in, chưa nghiệm thu trên cloud).

[Cấu hình Terraform](../../../deploy/terraform/README.md) · [Đo đạc failover](../../DO_DAC_FAILOVER.md) · [Trade-off](../../TRADE_OFFS.md) · [Quay lại lộ trình](../README.md).
