# GĐ4 — Các bước triển khai PostgreSQL và Issues trong K3s

Các bước đã được tích hợp tại chỗ vào [AWS Console, mục 9](GIAI_DOAN_03_AWS_CONSOLE.md#9-chuẩn-bị-namespace-và-image) và [Azure Portal, mục 7–8](GIAI_DOAN_04_AZURE_PORTAL.md#7-namespace-mạng-riêng-và-postgresql). File này được giữ làm tài liệu tham khảo chung; không cần chuyển sang đây khi làm hai hướng dẫn đó.

Đọc sau khi AWS t3.medium và Azure Standard_B2s đều có node Ready. Hai cluster độc lập, cùng K3s version và application digest. **Tài liệu phân biệt bước có lệnh dùng được và bước cần tạo manifest theo mạng/storage thực tế. Mẫu WireGuard, ConfigMap, PVC/StatefulSet/EndpointSlice và Secret đã có đầy đủ trong AWS Console/Azure Portal để tự tạo file. GĐ6B có hướng dẫn điều phối tự động AWS → Azure; cloud chưa nghiệm thu trên VM.** Không chạy đường dẫn manifest giả định hoặc script Compose local để thay phần còn thiếu.

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Tham khảo các bước database dùng chung; các mẫu đã nằm tại Console/Portal guide. |
| Chuẩn bị | Hai node Ready, tunnel hoạt động; ghi đúng IP, credential và digest. |
| Hoàn tất khi | Một primary, một standby; dữ liệu và role kiểm tra đúng. |

**Luồng thao tác:** [1. Ghi thông số trước khi triển khai](#1-ghi-thông-số-trước-khi-triển-khai) → [2. Chuẩn bị manifest database từ mẫu AWS/Azure](#2-chuẩn-bị-manifest-database-từ-mẫu-awsazure) → [3. Khởi tạo primary và quyền replication](#3-khởi-tạo-primary-và-quyền-replication) → [5. Bootstrap schema — chỉ trên AWS primary](#5-bootstrap-schema--chỉ-trên-aws-primary) → [6. Base backup và standby Azure](#6-base-backup-và-standby-azure) → [7. Đối chiếu role/WAL và dữ liệu](#7-đối-chiếu-rolewal-và-dữ-liệu) → [8. Ingress HTTP để kiểm tra origin theo GĐ3/GĐ4](#8-ingress-http-để-kiểm-tra-origin-theo-gđ3gđ4) → [9. Storage cũ và điểm kết thúc](#9-storage-cũ-và-điểm-kết-thúc).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

**Tham khảo:** khi triển khai bằng AWS Console/Azure Portal, tiếp tục ngay trong hai hướng dẫn đó; không chạy lại các bước ở đây.

## 1. Ghi thông số trước khi triển khai

1. Trên từng SSH, chạy `sudo k3s kubectl get nodes -o wide`, `free -h`, `df -h /`.
2. Ghi node/VM ID, disk/path, K3s version, app digest và PostgreSQL image/version vào bảng thông số.
3. Chốt namespace `issues`, StatefulSet DB `postgres`, app Deployment `issues-app`, app Service `issues-app` để các lệnh kiểm tra thống nhất.
4. Chốt endpoint DB giữa cloud và phương án VPN/tunnel có xác thực. VPC/VNet private IP và ClusterIP không tự định tuyến xuyên cloud.
5. Kiểm tra endpoint DB truy cập được từ pod, không chỉ SSH host. Đầu cuối phải dùng được theo cả hai chiều sau failback; 5432/NodePort không mở cho Internet.

## 2. Chuẩn bị manifest database từ mẫu AWS/Azure

Tạo cấu hình đã được review theo bảng dưới. Không có một manifest dùng chung mọi mạng/disk; các thông số thực tế phải chốt trước khi apply.

| Thành phần | AWS | Azure |
|---|---|---|
| PostgreSQL | StatefulSet một replica primary ban đầu | StatefulSet một replica standby ban đầu |
| Image | PostgreSQL 17, ghim cùng bản/digest | Cùng PostgreSQL image |
| PVC | Storage riêng trên EBS bền vững | Storage riêng trên managed disk bền vững |
| PGDATA | Thư mục thuộc PVC đã xác minh | Thư mục mới/trống cho base backup |
| Service app → DB | ClusterIP nội bộ | ClusterIP nội bộ |
| Replication endpoint | Endpoint được định tuyến qua kết nối riêng | Endpoint riêng để AWS rejoin sau này |
| Secret | Password app và user replication | Cùng user replication cần thiết, DSN app riêng |
| WAL | wal_level=replica, sender đủ cho standby/base backup | hot_standby=on, primary_conninfo trỏ AWS |

PVC local-path của K3s là dữ liệu trên node, không tự sao chép hoặc thay thế backup. Ghi storageClass/PV/path/reclaim policy; xóa PVC có thể xóa dữ liệu tùy chính sách. Không đặt PostgreSQL trên temporary disk Azure. Giữ dư RAM cho OS/K3s, đo requests/limits trước khi tăng app replica.

Khi manifest đã thực sự tồn tại trên VM, chạy dry-run và kiểm tra trước khi apply. Ví dụ **chỉ sau khi đã tạo file** `/opt/dcs29/postgres.yaml`:

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/postgres.yaml
sudo k3s kubectl apply -f /opt/dcs29/postgres.yaml
sudo k3s kubectl -n issues get statefulset,pods,svc,pvc -o wide
sudo k3s kubectl -n issues describe statefulset postgres
```

Trên AWS tạo primary trước. Azure chưa chạy initdb độc lập; hoàn tất schema/base backup ở các mục sau trước khi khởi động standby. Nếu manifest và kết nối riêng chưa hoàn thiện, dừng ở đây.

## 3. Khởi tạo primary và quyền replication

1. AWS: đợi pod `postgres-0` hoạt động, PVC Bound; xem log và PostgreSQL version.
2. Tạo database/tài khoản app theo Secret đã chốt; không dùng password local demo trên VM công khai.
3. Bật WAL replication, hot standby và max_wal_senders đủ cho base backup/streaming. Cả hai phía phải có thông số tương thích khi đổi role.
4. Role `issues_app` cần `pg_read_all_stats` để API đọc được receiver status; nếu thiếu, chạy GRANT trên primary theo Console guide, không sửa standby. Tạo user replication riêng; giới hạn pg_hba và firewall theo nguồn thực tế qua tunnel, dùng xác thực phù hợp.
5. Kiểm tra từ pod Azure tới endpoint AWS; đừng mở 5432 cho 0.0.0.0/0 để xử lý timeout.

Trên SSH AWS, dùng tài khoản quản trị DB thực tế thay `DB_ADMIN_USER`:

```bash
sudo k3s kubectl -n issues logs postgres-0 --tail=100
sudo k3s kubectl -n issues exec postgres-0 -- psql -U DB_ADMIN_USER -d postgres -c 'SELECT version(), pg_is_in_recovery();'
```

Kỳ vọng recovery=false. Lệnh không tạo credential; phần CREATE ROLE/database cần thực hiện qua kênh quản trị có bảo vệ, không ghi password trong tài liệu/history.

## 4. Secret app và Deployment/Service

Trên mỗi SSH: tạo `/opt/dcs29/secret.env` bằng editor, chỉ chứa `DATABASE_URL=postgresql://...` tới Service DB trong cluster tương ứng. Đặt quyền 600, không commit/copy vào image hoặc chụp màn hình giá trị. Tạo Secret:

```bash
cd /opt/dcs29
chmod 600 secret.env
sudo k3s kubectl -n issues create secret generic issues-db --from-env-file=secret.env --dry-run=client -o yaml | sudo k3s kubectl apply -f -
```

Output lệnh chỉ báo kết quả apply, nhưng YAML giữa pipeline chứa dữ liệu Secret; không chạy riêng bước generate để lưu vào repo. DSN của app không dùng cổng DB localhost trên laptop.

Tạo `/opt/dcs29/app.yaml`, thay image/digest, environment và region trước khi apply:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: issues-app
  namespace: issues
spec:
  replicas: 1
  selector:
    matchLabels:
      app: issues-app
  template:
    metadata:
      labels:
        app: issues-app
    spec:
      containers:
        - name: app
          image: GHCR_IMAGE@sha256:IMAGE_DIGEST
          ports:
            - name: http
              containerPort: 8080
          env:
            - name: APP_ENV
              value: aws
            - name: APP_REGION
              value: AWS_REGION
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: issues-db
                  key: DATABASE_URL
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
          livenessProbe:
            httpGet:
              path: /health/live
              port: http
            initialDelaySeconds: 10
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /api/database/status
              port: http
            initialDelaySeconds: 5
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: issues-app
  namespace: issues
spec:
  selector:
    app: issues-app
  ports:
    - name: http
      port: 80
      targetPort: http
```

Azure đổi APP_ENV thành azure, APP_REGION thành Location thật; giữ image digest. Requests/limits trên là điểm bắt đầu cho app, cần đo chứ không cam kết chịu tải. Package private cần imagePullSecret được tạo riêng; mẫu trên dành cho package public.

Readiness `/api/database/status` cho phép primary và standby kết nối được tham gia Service. Nó chưa kiểm tra toàn bộ schema/dữ liệu, nên vẫn phải nghiệm thu `/api/issues` sau bootstrap. `/health/database` dành cho writer gate, **không** dùng làm readiness/liveness của Service đọc standby vì standby khỏe trả 503.

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/app.yaml
sudo k3s kubectl apply -f /opt/dcs29/app.yaml
sudo k3s kubectl -n issues get pods -l app=issues-app
```

App có thể chưa Ready khi control schema chưa tồn tại. Đợi container Running rồi làm bootstrap ở mục 5; không sửa probe để che DB lỗi.

## 5. Bootstrap schema — chỉ trên AWS primary

Khi container app Running và DSN trỏ primary, trong SSH AWS:

```bash
sudo k3s kubectl -n issues exec deployment/issues-app -- python -c "from database.issues import initialize; initialize()"
sudo k3s kubectl -n issues rollout status deployment/issues-app --timeout=120s
```

Lệnh dùng DATABASE_URL trong Secret đã truyền vào pod. Bootstrap tasks chỉ khi cần demo cũ. App không tự init schema khi khởi động; không chạy lệnh này trên Azure standby.

## 6. Base backup và standby Azure

1. Xác minh storage Azure mới/trống, ghi PVC/path. Không xóa dữ liệu hiện có để ép clone.
2. Trong init Job/container có mount PVC và cùng PostgreSQL image, dùng pg_basebackup tới endpoint riêng AWS, user replication và credential từ Secret.
3. Chọn `-R` để tạo standby.signal/primary_conninfo, `-X stream` để nhận WAL và `-c fast` phù hợp demo; dùng đúng target PGDATA trên PVC. Lệnh phải được đặt vào manifest init đã review, không chạy từ laptop vào một thư mục tùy ý.
4. Hoàn tất clone rồi mới khởi động StatefulSet standby Azure; nếu copy dở, dừng và kiểm tra, không tự xóa thư mục không rỗng.
5. Triển khai app Azure như mục 4; schema đã đến qua bản sao primary nên không init riêng.

Mẫu tham số trong init container, **chỉ sau khi mount PVC/Secret và endpoint đã chốt**, password qua PGPASSWORD hoặc passfile có quyền phù hợp:

```bash
pg_basebackup -h AWS_DB_PRIVATE_HOST -p DB_REPLICATION_PORT -U replicator -D PGDATA_TARGET -R -X stream -c fast
```

Thay mọi placeholder; ghi quyền sở hữu PGDATA theo user postgres của image. Không chạy lại base backup vào thư mục dữ liệu đang dùng. Nguồn: [pg_basebackup PostgreSQL 17](https://www.postgresql.org/docs/17/app-pgbasebackup.html).

## 7. Đối chiếu role/WAL và dữ liệu

SSH AWS, với DB_ADMIN_USER thật:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -U DB_ADMIN_USER -d postgres -c 'SELECT pg_is_in_recovery(); SELECT state, sent_lsn, replay_lsn FROM pg_stat_replication;'
```

SSH Azure:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -U DB_ADMIN_USER -d postgres -c 'SELECT pg_is_in_recovery(), pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn(); SELECT status FROM pg_stat_wal_receiver;'
```

AWS recovery=false, Azure=true; sender/receiver streaming. Đợi WAL cuối replay rồi so issue/comments/history/receipt. App chỉ đọc standby, không dùng APP_ENV làm quyền ghi.

Để kiểm tra Service trước khi ingress, mở một SSH riêng trên từng VM:

```bash
sudo k3s kubectl -n issues port-forward service/issues-app 18080:80 --address 127.0.0.1
```

Trong SSH thứ hai trên **cùng VM**:

```bash
curl -fsS http://127.0.0.1:18080/version
curl -fsS http://127.0.0.1:18080/api/database/status
curl -fsS http://127.0.0.1:18080/api/issues
```

Ctrl+C để dừng port-forward sau kiểm tra. Không mở 18080 trên cloud firewall. GĐ5 sẽ tạo Ingress chính thức.

## 8. Ingress HTTP để kiểm tra origin theo GĐ3/GĐ4

Sau khi Service/DB đọc được, trên từng VM tạo `/opt/dcs29/ingress-http.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: issues-http
  namespace: issues
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
spec:
  ingressClassName: traefik
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: issues-app
                port:
                  number: 80
```

```bash
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/ingress-http.yaml
sudo k3s kubectl apply -f /opt/dcs29/ingress-http.yaml
sudo k3s kubectl -n issues get ingress
```

SG/NSG cổng 80 phải đã mở theo Console/Portal guide. Trên laptop kiểm tra `http://AWS_PUBLIC_IP/issues.html`, Azure tương ứng và `/version`, `/api/database/status`, `/api/issues`. Không mở app 8080 trực tiếp. HTTP chỉ dùng dữ liệu demo; GĐ5 cấp TLS rồi cập nhật Ingress HTTP thành redirect HTTPS.

## 9. Storage cũ và điểm kết thúc

Local giữ volume cũ khi rebuild; cloud cần cơ chế tương đương cho PVC/path. Ghi inventory trước đổi role/rejoin. Chỉ dọn bản cũ sau khi primary/standby/dữ liệu đã xác minh; giữ bản gần nhất ngắn hạn nếu cần đối chiếu. Không prune hoặc xóa PVC đang dùng.

Chuyển GĐ5 sau khi app hai bên cùng digest, AWS ghi/Azure chỉ đọc, streaming và dữ liệu thật khớp; VM reboot giữ storage. Phần manifest DB/mạng riêng chưa triển khai thì chưa đủ điều kiện nghiệm thu. Nguồn: [K3s storage](https://docs.k3s.io/storage/), [PostgreSQL standby](https://www.postgresql.org/docs/17/warm-standby.html).

## Sao lưu trước khi đổi storage hoặc dọn VM

Trên primary đang phục vụ, tạo logical dump trên host (không đặt trong PVC active); lệnh mẫu dùng user postgres qua Unix socket trong pod:

```bash
sudo install -d -m 700 /opt/dcs29/backups
BACKUP_FILE="/opt/dcs29/backups/issues-$(date -u +%Y%m%dT%H%M%SZ).dump"
sudo bash -c 'umask 077; /usr/local/bin/k3s kubectl -n issues exec postgres-0 -- pg_dump -U postgres -d issues -Fc > "$1"' backup-save "$BACKUP_FILE"
sudo test -s "$BACKUP_FILE"
sudo k3s kubectl -n issues exec postgres-0 -- pg_restore --version
```

pg_dump không thay thế physical clone để tạo standby. Kiểm tra dump bằng cách copy vào một database tách biệt hoặc môi trường local và dùng pg_restore; không restore đè database active. Download bằng SCP sau khi copy file sang thư mục chỉ user quản trị đọc được, giữ bản ngoài VM trước destroy. Không đưa dump vào Git. Ghi file, thời gian, checksum và kết quả restore thử.


### Kiểm tra dump bằng restore vào database tách biệt

Chạy trên primary đã có BACKUP_FILE từ block backup trước; bảo đảm đủ đĩa giữ thêm bản restore. Tên issues_restore_check phải chưa tồn tại, createdb sẽ dừng nếu đã có:

```bash
set -o pipefail
sudo k3s kubectl -n issues exec postgres-0 -- createdb -U postgres issues_restore_check || { echo 'Không tạo được database kiểm thử: dừng, không restore vào DB đã có'; exit 1; }
sudo cat "$BACKUP_FILE" | sudo k3s kubectl -n issues exec -i postgres-0 -- pg_restore -U postgres -d issues_restore_check --no-owner --exit-on-error || { echo 'Restore lỗi: giữ dump và DB thử để kiểm tra'; exit 1; }
sudo k3s kubectl -n issues exec postgres-0 -- psql -U postgres -d issues_restore_check -c 'SELECT count(*) FROM issues; SELECT count(*) FROM comments; SELECT count(*) FROM issue_history;'
sudo sha256sum "$BACKUP_FILE"
```

Đối chiếu ID/nội dung với source. Restore check không thay đổi DATABASE_URL app, không promote standby. Sau khi ghi kết quả, nếu không cần DB thử nữa, chỉ xóa đúng database thử đã tạo:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- dropdb -U postgres issues_restore_check
```

Để download backup qua SCP, copy thành file user quản trị sở hữu quyền 600; chỉ thực hiện trong thư mục backup của lab:

```bash
sudo cp "$BACKUP_FILE" /opt/dcs29/issues-download.dump
sudo chown "$USER:$USER" /opt/dcs29/issues-download.dump
chmod 600 /opt/dcs29/issues-download.dump
```

PowerShell laptop, thay user/IP/key của VM primary hiện tại:

```powershell
scp -i PATH_TO_PRIMARY_KEY PRIMARY_ADMIN_USER@PRIMARY_PUBLIC_IP:/opt/dcs29/issues-download.dump "$env:USERPROFILE/.dcs29-private/issues-backup.dump"
```

Giữ bản ngoài VM và checksum trước khi destroy; nếu backup mới thì chọn tên file laptop mới, không ghi đè bản đã nghiệm thu.
