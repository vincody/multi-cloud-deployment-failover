# Bảng thông số triển khai Issues trên hai VM

Điền giá trị thực tế vào sổ triển khai riêng trước khi làm GĐ3–GĐ8. Không lưu credential trong bảng này.

| Ký hiệu | Giá trị / cách lấy | Mục đích |
|---|---|---|
| AWS_INSTANCE_TYPE | `t3.medium` | 2 vCPU, 4 GiB RAM |
| AZURE_VM_SIZE | `Standard_B2s` | 2 vCPU, 4 GiB; kiểm tra quota/lifecycle |
| OS_IMAGE | Ubuntu Server 24.04 LTS x86_64/x64 | Image linux/amd64 |
| AWS_DISK_GIB | 30, gp3 ban đầu | OS, containerd, log, dữ liệu demo |
| AZURE_DISK_GIB | 30 GiB hoặc lớn hơn theo image, Standard SSD | Không nhỏ hơn image; không dùng temporary disk cho DB |
| AWS_REGION, AZURE_REGION | Vùng được quota/policy cho phép | APP_REGION đúng mã vùng |
| AWS_PUBLIC_IP, AZURE_PUBLIC_IP | Elastic IP / Standard Static IP | SSH, origin và đầu cuối tunnel |
| ADMIN_PUBLIC_IP | IP laptop hiện tại | SSH chỉ từ /32 |
| AWS_VM_ID, AZURE_VM_ID | ID tài nguyên vừa tạo | Fencing và kiểm chứng trạng thái |
| PRIVATE_NETWORK_METHOD | WireGuard wg0 | AWS 10.200.29.1, Azure 10.200.29.2, UDP 51820 chỉ peer public IP |
| AWS_DB_PRIVATE_HOST, AZURE_DB_PRIVATE_HOST | Endpoint DB định tuyến qua kết nối riêng | Phải đi được từ pod, theo cả hai chiều |
| K3S_VERSION | Một release hỗ trợ Ubuntu, ghi version đầy đủ | Cùng version trên hai VM |
| APP_NAMESPACE | issues dự kiến | Tách tài nguyên ứng dụng |
| APP_ENV | aws / azure | Nhãn cloud, không xác nhận role DB |
| GHCR_IMAGE | ghcr.io/vincody/multi-cloud-deployment-failover | Image ứng dụng chung |
| IMAGE_DIGEST | 64 ký tự sau sha256 từ artifact đã chọn | Cùng digest ở hai cluster |
| COMMIT_SHA, ACTIONS_RUN_URL | Cùng run tạo digest | Đối chiếu /version |
| POSTGRES_IMAGE_REF | PostgreSQL 17 cùng bản/image digest | Tương thích physical replication |
| DATABASE_URL | DSN DB của từng cluster | Secret riêng ngoài repo |
| DB_STORAGE_PATH, DB_PVC_SIZE | Vị trí bền vững và dung lượng dự trù | Mỗi DB có storage riêng |
| YOUR_DOMAIN | Domain nhóm sở hữu | Không dùng domain lượt cũ |
| AWS_ORIGIN_HOST, AZURE_ORIGIN_HOST | aws-origin/azure-origin dưới domain thật | Kiểm tra từng origin |
| SHARED_HOST | app dưới domain thật | Origin trình duyệt giữ nguyên |
| HOSTED_ZONE_ID, CERT_EMAIL | Zone/email TLS | Điền khi làm GĐ5 |
| MONITOR_HOST | Laptop/máy thứ ba | Monitor độc lập hai VM |

## Thông số bổ sung cho các bước hướng dẫn chi tiết

| Ký hiệu | Điền từ đâu / cách dùng |
|---|---|
| PATH_TO_REPO | Đường dẫn repo laptop, dùng ở GĐ7 |
| PATH_TO_AWS_PRIVATE_KEY, PATH_TO_AZURE_PRIVATE_KEY | Key SSH ngoài repo, dùng đúng VM |
| AZURE_ADMIN_USER | Username lúc tạo Azure VM, không mặc định user AWS |
| GHCR_OWNER | Owner có quyền đọc private package nếu cần |
| DB_ADMIN_USER, APP_DATABASE | User quản trị và database thật đã tạo trong manifest DB |
| DB_REPLICATION_PORT | Cổng endpoint DB qua kết nối riêng, không mặc định cổng laptop |
| PGDATA_TARGET | Thư mục mới/trống trên PVC mount của init container |
| FINAL_WAL_LSN | Vị trí WAL sau khi khóa ghi, dùng để so replay trước promote |
| KSM_VERSION | Tag kube-state-metrics tương thích K3s/Kubernetes |
| AWS_HEALTH_CHECK_ID, AZURE_HEALTH_CHECK_ID | ID writer check từng origin ở GĐ6 |
| ROUTE53_NS_1 … ROUTE53_NS_4 | Bốn NS của public hosted zone vừa tạo, điền tại nhà đăng ký |
| DR_CONTROL_REGION, ACCOUNT_ID | us-east-1 và AWS account thật, dùng GĐ6B |
| AZURE_NODE_ID, DR_CONTROLLER_MODE | SSM mi-ID thật; manual/automatic ghi theo lượt đo, không dùng ID máy người khác |
| NODE_EXPORTER_IMAGE_DIGEST | Image node-exporter linux/amd64 pin theo GĐ7 |
| DATASET_SHA256 | Chỉ dùng so dashboard cũ; không thay kiểm chứng dữ liệu Issues |

## Thứ tự điền và kiểm tra trước khi chạy lệnh

1. GĐ2 lấy image/digest/commit từ cùng version hoặc artifact run; digest trong lệnh đã có tiền tố sha256 nên chỉ điền 64 ký tự hex. Package public và repo source public là hai thiết lập riêng.
2. GĐ3/GĐ4 chọn vùng, tạo đúng VM size, ghi IP/ID/disk/K3S_VERSION; key/password không nằm trong ghi chú được commit. Chốt endpoint riêng và từ pod kiểm tra được theo cả hai chiều.
3. GĐ4 ghi tài nguyên manifest thực tế và DB/schema. Đường dẫn file trong hướng dẫn chỉ chạy sau khi đã tạo file tương ứng; mẫu manifest chi tiết đã nằm trong AWS Console/Azure Portal; thay digest/IP trước apply.
4. GĐ5 dùng domain thuộc quyền quản lý: tạo hosted zone, ghi bốn NS, đổi nameserver tại nhà đăng ký, xác nhận delegation rồi tạo origin A records/certificate/Ingress.
5. GĐ6 ghi writer role, WAL, VM fencing và health check IDs. DNS quay về AWS chỉ sau rejoin/chuyển role; không tự failback khi VM bật lại.
6. GĐ6B (tùy chọn) ghi node SSM, IAM/ARN, alarm/rule/workflow và rearm; cấu hình GĐ7 trước demo automatic để có evidence.
7. GĐ7 điền ba probe targets /health/live bằng hostname thật; thêm exporter/metrics riêng cho pod/DB. Không dùng standby /health/database 503 làm tín hiệu VM sập.

Thay mọi YOUR_DOMAIN, IP, username, image, version và đường dẫn mẫu trước khi chạy. Các IP tài liệu 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 chỉ minh họa, không dùng triển khai. Kubeconfig, token, DSN password và certificate private key không đưa vào repo.

## Kiểm tra trước khi tạo VM

- T3 dùng CPU credit; ghi chế độ Standard/Unlimited và dự toán theo region. B2s cũng là burstable, cần theo dõi credit khi thử tải.
- Bv1 có thông báo retirement. Nếu B2s không khả dụng, ghi lỗi và chốt lại size, không tự đổi cấu hình.
- Managed disk Azure không thể thu nhỏ trực tiếp. Chọn theo image ngay lúc tạo; Azure lớn hơn 30 GiB không buộc AWS phải tăng theo.
- Không dùng thông số nghiệm thu của lượt chạy cũ. Dataset fingerprint chỉ liên quan dashboard cũ; Issues kiểm chứng dữ liệu PostgreSQL, role và WAL.

Nguồn: [AWS CPU credit](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-performance-instances-unlimited-mode-concepts.html), [Azure Bv1](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/bv1-series), [Azure disk FAQ](https://learn.microsoft.com/en-us/azure/virtual-machines/faq-for-disks).

## Thông số database mẫu

- DB issues; app role issues_app; replication role replicator; admin postgres.
- Namespace issues; Service postgres không selector và EndpointSlice postgres-tunnel tới wg0 local.
- StatefulSet postgres; PVC postgres-data 5Gi local-path, PV Retain; OS disk 30 GiB.
- Image PostgreSQL 17-bookworm cùng digest; K3s cùng version.
- Startup guard /run/issues-k3s-start-approved: sau reboot phải kiểm tra writer trước khi start K3s.

- DB_ADMIN_USER=postgres; APP_DATABASE=issues; AWS_DB_PRIVATE_HOST=10.200.29.1; DB_REPLICATION_PORT=5432; PGDATA_TARGET=/var/lib/postgresql/data/pgdata trong init container. Không chạy base backup vào host filesystem.
