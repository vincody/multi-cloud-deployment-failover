# Giai đoạn 5 — Domain và HTTPS

**Mục tiêu:** AWS và Azure cùng phục vụ một hostname người dùng, ví dụ `app.example.com`, bằng HTTPS hợp lệ.

## Việc sẽ làm

1. Chuẩn bị domain/subdomain và hosted zone Route 53.
2. Thiết lập origin AWS và Azure để đều chấp nhận `Host: app.example.com`.
3. Cấp certificate phù hợp cho hostname chung ở cả hai origin (DNS-01 là hướng thuận tiện khi đã kiểm soát DNS).
4. Kiểm tra HTTPS trực tiếp từng origin với đúng hostname/SNI trước khi bật failover.
5. Bảo đảm HTTP redirect HTTPS, certificate valid và API response không cache.

## Bằng chứng đạt

- Browser/curl không cảnh báo certificate khi đến từng origin bằng hostname chung.
- Cả hai origin trả cùng application identity trừ environment/region.

## Tiếp theo

Thực hiện [GĐ6 — Route 53 failover](GIAI_DOAN_06_ROUTE53_FAILOVER.md).
