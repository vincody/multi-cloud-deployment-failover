# Lộ trình triển khai

- [Thủ công](thu_cong/README.md): hướng dẫn từng giai đoạn bằng AWS Console/Azure Portal, từ local, image, AWS/Azure, database, HTTPS, failover có kiểm soát đến giám sát và kiểm thử. Không có failover tự động.
- [Tự động](tu_dong/README.md): triển khai bằng Terraform (K3s, WireGuard, PostgreSQL, app, HTTPS, DNS) và failover tự động bằng watcher trên VM Azure.

Tài liệu chung: [kế hoạch revamp](../REVAMP_PLAN.md) · [đo đạc failover](../DO_DAC_FAILOVER.md) · [độ phức tạp và trade-off](../TRADE_OFFS.md) · [replication và failover local](../LOCAL_REPLICATION.md).
