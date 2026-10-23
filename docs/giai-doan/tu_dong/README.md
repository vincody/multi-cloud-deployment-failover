# Triển khai tự động

- [Triển khai bằng Terraform và các bước tiếp theo](TRIEN_KHAI_BANG_TERRAFORM.md): chuẩn bị, plan/apply, kiểm tra automation, HTTPS, DNS, failover/failback và monitoring; các bước cấu hình giữ ngay trong tài liệu.
- [Failover tự động GĐ6B](AUTOMATION_FAILOVER.md): watcher trên VM Azure, hai nguồn xác nhận, fencing trước promote, quyền IAM hẹp và vận hành.
- [Chi tiết automation HTTPS](AUTOMATION_HTTPS.md): credential, DNS, cấp certificate, Ingress, renewal và retry.
- [Chi tiết automation WireGuard, PostgreSQL và Issues](AUTOMATION_WIREGUARD_POSTGRESQL_APP.md): trình tự, file/cấu hình, credential, retry, giới hạn và kiểm thử.

Terraform hiện tạo hạ tầng, bootstrap K3s và tự triển khai WireGuard, PostgreSQL AWS primary/Azure standby, schema, Secret, app và HTTP Ingress. HTTPS/renewal được tự cấu hình khi bật `enable_https`. Delegation domain, key DNS Azure, backup và monitoring vẫn cần thực hiện theo hướng dẫn. GĐ6B tự fence AWS/promote Azure khi bật `enable_auto_failover`; rejoin/failback vẫn thủ công.

[Cấu hình Terraform](../../../deploy/terraform/README.md) · [Quay lại lộ trình](../README.md).
