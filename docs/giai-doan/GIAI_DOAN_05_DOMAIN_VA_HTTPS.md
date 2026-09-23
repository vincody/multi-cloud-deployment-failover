# Giai đoạn 5 — Domain chung và HTTPS ở hai origin

**Trạng thái:** Chưa thực hiện.
**Mục tiêu:** cả AWS và Azure phục vụ hợp lệ hostname người dùng, ví dụ `app.example.com`, trước khi bật failover.

## 1. Vì sao giai đoạn này độc lập

DNS trỏ sang Azure không thay đổi TLS SNI/HTTP Host mà browser gửi: chúng vẫn là `app.example.com`. Vì vậy cả NGINX AWS và Azure Container App phải nhận hostname này và có certificate hợp lệ. Chỉ thấy Azure FQDN hoạt động chưa đủ.

## 2. Đầu vào

- Domain/subdomain nhóm kiểm soát.
- Route 53 public hosted zone hoặc delegation vào Route 53.
- Elastic IP AWS, Azure default FQDN.
- Hai origin đã pass health/version riêng.
- Quyết định certificate: ACME DNS-01/certificate tự cung cấp; lưu renewal owner.

## 3. DNS nền

1. Tạo/kiểm tra hosted zone.
2. Đối chiếu NS tại registrar/delegated parent.
3. Tạo `aws-origin.example.com` A record tới Elastic IP.
4. Giữ Azure default FQDN làm origin Azure.
5. Tạo TXT validation Azure theo portal, thường dạng `asuid.app`.
6. Chưa tạo failover records cho `app` cho tới khi hai HTTPS origin pass.

Ghi TTL, record ID, nameserver và thời gian propagation. Không health-check hostname chung ở bước này.

## 4. Certificate AWS

Dùng certificate cho `app.example.com` và hostname origin cần thiết. Với NGINX trên EC2, ACME DNS-01 phù hợp vì quyền DNS nằm ở Route 53. Private key chỉ nằm trên server/secret storage, không vào Git.

NGINX cần:

- Listen 443 SSL.
- `server_name app.example.com aws-origin.example.com` theo certificate.
- Full certificate chain và private key read-only.
- Proxy tới `app:8080`.
- Redirect HTTP → HTTPS sau khi HTTPS pass.
- Renewal hook kiểm tra `nginx -t` rồi reload.

Ghi issuer, serial/fingerprint, notBefore/notAfter và người chịu trách nhiệm renewal.

## 5. Custom domain Azure

1. Mở Container App → Custom domains.
2. Thêm `app.example.com`.
3. Tạo TXT verification Azure yêu cầu.
4. Import certificate/PFX phù hợp và bind hostname.
5. Trong giai đoạn verify, có thể tạm CNAME `app` tới Azure FQDN; sau binding phải thay bằng failover records ở GĐ6.
6. Giữ TXT validation nếu Azure yêu cầu cho renewal/ownership.

Nếu dùng managed certificate, đọc điều kiện validation/renewal; DNS thường ưu tiên AWS có thể ảnh hưởng cơ chế Azure. Với bài lab, certificate tự cung cấp giúp chủ động nhưng tăng trách nhiệm renewal.

## 6. Kiểm tra SNI/Host không qua failover

AWS:

```bash
curl --resolve app.example.com:443:AWS_ELASTIC_IP https://app.example.com/version
```

Azure:

```bash
curl --connect-to app.example.com:443:AZURE_FQDN:443 https://app.example.com/version
```

Không dùng `-k`. Cả hai lệnh phải verify certificate chain và trả identity đúng. Kiểm tra thêm ready/devices, redirect HTTP, expiry và response headers.

## 7. Ma trận kết quả

| Kiểm tra | AWS | Azure |
|---|---|---|
| TLS hostname hợp lệ | bắt buộc | bắt buộc |
| Certificate chain tin cậy | bắt buộc | bắt buộc |
| `/health/ready` 200 | bắt buộc | bắt buộc |
| Version/commit/dataset | giống Azure | giống AWS |
| Environment | `aws-primary` | `azure-standby` |

## 8. Lỗi thường gặp

| Triệu chứng | Kiểm tra |
|---|---|
| TLS mismatch | SAN không chứa `app.example.com`, SNI sai |
| Azure 404 | Custom domain chưa bind/Host không được nhận |
| Full chain lỗi | Thiếu intermediate certificate |
| HTTP pass, HTTPS fail | Port 443/SG/binding/certificate |
| Renewal fail | TXT bị xóa, DNS-01 permission/owner |
| curl origin đúng nhưng browser sai | DNS/cache đang tới origin khác |

## 9. Rollback

Không bật Route 53 failover khi một origin TLS chưa pass. Nếu Azure binding lỗi, trả record verify về cấu hình trước; nếu AWS cert lỗi, giữ HTTP chỉ trong môi trường kiểm tra và sửa certificate. Không hạ chuẩn bằng self-signed hoặc `-k` để “pass”.

## 10. Artifact cần lưu

- Zone/record export đã che thông tin không cần thiết.
- Certificate fingerprint/expiry, không lưu private key.
- NGINX TLS config không secret.
- Azure domain binding screenshot/config.
- Output hai lệnh curl ép origin.
- Quy trình renewal và owner.

## 11. Checklist hoàn thành

- [ ] Nameserver/delegation đúng.
- [ ] AWS origin hostname ổn định.
- [ ] AWS chấp nhận shared hostname bằng HTTPS.
- [ ] Azure custom domain/certificate bind đúng.
- [ ] Hai lệnh ép origin pass không dùng `-k`.
- [ ] Version/commit/fingerprint giống nhau.
- [ ] Renewal/runbook và evidence đã lưu.

## 12. Kết quả mong đợi và bước tiếp theo

GĐ5 hoàn thành khi cùng hostname có TLS hợp lệ ở từng origin; chưa có chuyển traffic tự động. GĐ6 tạo health checks và Failover records. Xem [GĐ6](GIAI_DOAN_06_ROUTE53_FAILOVER.md).
