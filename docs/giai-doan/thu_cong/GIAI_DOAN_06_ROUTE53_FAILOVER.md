# Giai đoạn 6 — Fencing, PostgreSQL failover/failback và Route 53

**Đầu vào:** GĐ4–GĐ5 đạt trên hai VM; cùng image digest, schema và streaming, HTTPS/Ingress hợp lệ. **Trạng thái:** hướng dẫn thao tác có kiểm soát, chưa có script điều phối cloud đã nghiệm thu. Không chạy local_lifecycle.py nguyên xi trên VM.

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Diễn tập fencing, promote, DNS failover, rejoin và failback thủ công. |
| Chuẩn bị | Hai origin HTTPS đạt, standby streaming; có backup và quyền thao tác VM. |
| Hoàn tất khi | Chỉ một writer; dữ liệu được kiểm tra sau cả failover và failback. |

**Luồng thao tác:** [2. LAPTOP — kiểm tra đầu vào](#2-laptop--kiểm-tra-đầu-vào) → [3. AWS CONSOLE — tạo hai health check độc lập](#3-aws-console--tạo-hai-health-check-độc-lập) → [4. Chuyển role database — trước khi thử DNS failover](#4-chuyển-role-database--trước-khi-thử-dns-failover) → [5. AWS CONSOLE — tạo hai record failover tên app](#5-aws-console--tạo-hai-record-failover-tên-app) → [6. LAPTOP — quan sát tuyến shared và ghi dữ liệu](#6-laptop--quan-sát-tuyến-shared-và-ghi-dữ-liệu) → [8. Rejoin AWS và failback có kiểm soát](#8-rejoin-aws-và-failback-có-kiểm-soát) → [11. Checklist nghiệm thu](#11-checklist-nghiệm-thu).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

## 1. Mô hình và nơi thao tác

Một writer: ban đầu AWS primary, Azure standby. Pod app lỗi thì K3s hồi phục; không promote Azure vì một pod lỗi. Khi mất VM/mạng cần xác minh fencing trước promote. DNS và APP_ENV không phải khóa writer.

- Console/Portal: xác nhận/dừng/cô lập VM cụ thể.
- SSH: kiểm tra/chuyển role PostgreSQL và Kubernetes.
- PowerShell laptop: probe shared hostname, DNS, identity và dữ liệu.

Runbook này là GĐ6A, thực hiện thủ công trước. Sau diễn tập và trở về AWS primary/Azure standby, có thể tạo [controller tự động GĐ6B bằng Console](GIAI_DOAN_06B_FAILOVER_TU_DONG.md). Nếu đã bật controller, Disable EventBridge rule và xác nhận execution/SSM command đã terminal trước chạy chuyển role thủ công; không promote tay song song với workflow.

## 2. LAPTOP — kiểm tra đầu vào

```powershell
Resolve-DnsName aws-origin.YOUR_DOMAIN -Type A -Server 1.1.1.1
Resolve-DnsName azure-origin.YOUR_DOMAIN -Type A -Server 1.1.1.1
curl.exe --fail-with-body https://aws-origin.YOUR_DOMAIN/version
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/version
curl.exe --fail-with-body https://aws-origin.YOUR_DOMAIN/api/database/status
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/api/database/status
```

AWS writable, Azure standby streaming. /version cùng commit; dữ liệu Issues/schema được đối chiếu. Test hostname chung ép IP theo GĐ5; không dùng --resolve khi đo DNS thật.

SSH từng VM kiểm tra K3s/pod/PVC; không dùng docker compose up để bật primary cũ. Ghi VM ID, PV/path/role và trạng thái writer trước mọi thao tác.

## 3. AWS CONSOLE — tạo hai health check độc lập

### 3.1. Health check AWS — tạo trước

1. Mở **AWS Console → Route 53 → Health checks → Create health check**.
2. Nếu đã có check đúng cấu hình, mở kiểm tra thay vì tạo trùng. Giao diện Route 53 có thể là bản mới hoặc cũ, tên trường hơi khác.
3. Chọn **Resource = Endpoint → Specify endpoint by = Domain name**. Nếu giao diện có một ô bên phải menu **HTTPS**, nhập hostname origin AWS, port và path như sau (sau khi thay `YOUR_DOMAIN`):

   ```text
   aws-origin.YOUR_DOMAIN:443/health/database
   ```

   Mẫu là `hostname:443/path`. Không thêm `https://` khi menu đã chọn HTTPS; không nhập `app.YOUR_DOMAIN` vì tên chung sẽ đổi đích khi failover. Nếu Console có ô **Domain name**, **Port** và **Path** riêng, nhập lần lượt hostname origin, `443` và `/health/database`.

   | Trường/điều cần kiểm tra | Giá trị AWS |
   |---|---|
   | Name | `dcs29-aws-https-ready` |
   | Resource | **Endpoint** |
   | Specify endpoint by | **Domain name** |
   | Protocol trong menu | **HTTPS** |
   | Ô bên phải menu HTTPS nếu giao diện gộp trường | `aws-origin.YOUR_DOMAIN:443/health/database` |
   | Port | `443`, nằm giữa hostname và path trong cùng ô |

   **Nếu Console của bạn hiển thị UI khác:** ô Domain name riêng chỉ nhập `aws-origin.YOUR_DOMAIN`, ô Path/Resource path riêng nhập `/health/database`. Không nhập URL đầy đủ `https://...` vào ô chỉ nhận hostname.
4. Mở **Advanced configuration / Additional settings**:

| Trường | Chọn |
|---|---|
| Request interval | **Standard / 30 seconds** |
| Failure threshold | `3` |
| Enable SNI | **Bật** |
| Host name nếu có ô riêng | `aws-origin.YOUR_DOMAIN` |
| String matching / Search string | **Tắt**, để trống |
| Latency measurement / Latency graphs | **Tắt** |
| Health checker regions | Giữ recommended/default |
| Invert health check status | **Tắt** |
| Disabled | **Tắt**, check phải được enabled |

5. Trước khi tạo, xem phần review/preview nếu có: endpoint phải là HTTPS đến `aws-origin.YOUR_DOMAIN` và path `/health/database`, không phải `/`. Nếu preview không hiển thị path, sau khi tạo mở chi tiết check → cấu hình endpoint để xác nhận **Resource path = `/health/database`**. Nếu AWS lưu path là `/`, sửa health check ngay trước khi tạo record failover; không coi check healthy là đủ vì `/` cũng có thể trả 200. Nếu có bước **Create alarm**, chọn **No / Không tạo alarm** trong GD6. Chọn **Next/Create health check** theo giao diện.
6. Mở check vừa tạo, ghi **Health check ID** vào ghi chú cá nhân (không phải Hosted zone ID). Đợi trạng thái cập nhật, không kết luận lỗi ngay khi vừa tạo.

Nếu Console đang dùng form **IP address**: IP AWS là `AWS_PUBLIC_IP`, vẫn phải có hostname origin trong ô Host name/Domain name và bật SNI; không dùng IP làm TLS hostname. Chỉ chọn một cách cấu hình và ghi lại cách đã chọn.

Tham khảo [AWS: tạo health check](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-creating.html) và [các trường cấu hình](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-creating-values.html).

### 3.2. Health check Azure — cũng tạo trên AWS Console

Vẫn trong **Route 53 → Health checks → Create health check**. Với UI một ô như ảnh, chọn HTTPS và nhập `azure-origin.YOUR_DOMAIN:443/health/database` vào ô bên phải menu HTTPS. Lặp lại mục 3.1 với **các giá trị khác** sau:

| Trường | Giá trị Azure |
|---|---|
| Name | `dcs29-azure-https-ready` |
| Ô Domain name của UI một ô trong ảnh | `azure-origin.YOUR_DOMAIN:443/health/database` |
| Host name nếu có ô riêng | `azure-origin.YOUR_DOMAIN` |
| IP nếu chọn form IP address | `AZURE_PUBLIC_IP` |

**Các giá trị còn lại giống AWS:** HTTPS, 443, `/health/database`, interval 30, threshold 3, SNI bật; string matching/latency/alarm/invert tắt. Lưu check, mở chi tiết để xác nhận **Resource path = `/health/database`**, rồi ghi Health check ID Azure riêng. Nếu UI cũ có ô riêng, Domain name chỉ là `azure-origin.YOUR_DOMAIN`, Path là `/health/database`.

### 3.3. Hiểu trạng thái writer check

Ban đầu AWS primary trả 200, Azure standby khỏe trả **503** tại /health/database. AWS writer check healthy, Azure writer check unhealthy là đúng; không đợi cả hai healthy hoặc bỏ check Azure để ép chuyển.

Kiểm tra Azure sống/đọc bằng /api/database/status và /api/issues. Chỉ sau fencing/promote/mở ghi Azure thì check Azure mới phải healthy. Route 53 chỉ chuyển DNS, không thực hiện các bước DB.

Health check HTTPS của Route 53 không thay kiểm tra certificate như browser/curl. Xác nhận SNI/path/IP và TLS riêng theo GĐ5. [AWS health-check behavior](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-determining-health-of-endpoints.html).

## 4. Chuyển role database — trước khi thử DNS failover

**Thứ tự thao tác:** đọc mục này trước để hiểu runbook, nhưng tạo record ở mục 5 và xác nhận baseline AWS ở mục 6 trước khi thực hiện chuyển role. Các lệnh mục 4 chỉ chạy trong lượt thử chuyển đổi đã chuẩn bị; không promote ngay khi đang tạo health checks.

### 4.1. AWS còn truy cập được: khóa ghi và lấy WAL cuối

Chỉ trên **primary AWS đã xác minh**, thay DB_ADMIN_USER và APP_DATABASE. Không chạy lệnh này trên standby. Advisory lock dùng cùng khóa (2900,1) với writer của ứng dụng:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c "BEGIN; SET LOCAL lock_timeout='15s'; SELECT pg_advisory_xact_lock(2900,1); UPDATE app_control SET writes_enabled=false WHERE singleton=true; COMMIT;"
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c 'SELECT pg_current_wal_flush_lsn();'
```

Đợi lệnh đầu success rồi mới lấy LSN. Nếu timeout/error, dừng xử lý, không tiếp tục promote. Khóa chờ các transaction app kết thúc; sau commit writer mới thấy writes_enabled=false. Writer DB trực tiếp ngoài ứng dụng cũng phải dừng riêng.

Trên Azure, so replay với LSN vừa ghi, thay FINAL_WAL_LSN:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c "SELECT pg_is_in_recovery(), pg_last_wal_replay_lsn(), pg_last_wal_replay_lsn() >= 'FINAL_WAL_LSN'::pg_lsn AS caught_up;"
```

Chỉ tiếp khi recovery=true, caught_up=true. Đối chiếu dữ liệu/receipt mới nhất. Nếu lag chưa bắt kịp, chờ/kiểm tra streaming, không đổi đích DNS.

### 4.2. Fence AWS

AWS Console → EC2 → Instances → chọn đúng AWS_VM_ID → Instance state → Stop instance. Đợi trạng thái **Stopped**, xác minh IP đúng instance và không có cơ chế tự bật writer khác. Stop command được gửi chưa đủ để chứng minh fencing.

K3s StatefulSet sẽ tạo lại pod bị xóa, nên kubectl delete pod postgres-0 không phải fencing. Nếu dùng phương án cô lập thay Stop, phải chứng minh writer không nhận được ghi và không tự tái xuất hiện. Thiếu xác nhận thì dừng promote.

AWS sập bất ngờ: bỏ qua bước drain nếu không thể truy cập, nhưng vẫn xác minh fencing, WAL Azure và ghi rõ nguy cơ mất giao dịch async. Không cam kết RPO=0.

### 4.3. Promote và mở ghi Azure

Chỉ sau xác nhận AWS đã bị fence, trên SSH Azure:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c 'SELECT pg_promote(true,30);'
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c 'SELECT pg_is_in_recovery();'
```

Promote phải success, recovery=false; nếu chưa đúng không mở ghi. Sau đó mở gate của primary mới:

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U DB_ADMIN_USER -d APP_DATABASE -c "BEGIN; SET LOCAL lock_timeout='15s'; SELECT pg_advisory_xact_lock(2900,1); UPDATE app_control SET writes_enabled=true WHERE singleton=true; COMMIT;"
```

Laptop thử /health/database Azure: HTTP 200/writable=true; thử tạo bình luận qua origin và kiểm tra request_id không trùng. Chỉ sau đó cho shared traffic sang Azure. Writer check mới healthy có độ trễ; client DNS có cache riêng.

## 5. AWS CONSOLE — tạo hai record failover tên app

### 5.1. Kiểm tra xung đột trước khi tạo

**Route 53 → Hosted zones → YOUR_DOMAIN → Records**, lọc tên `app.YOUR_DOMAIN`.

Theo GD5 chưa có DNS record `app`, nên thường không có gì cần xóa. Nếu có record A/AAAA/CNAME simple cũ cho `app`, lưu cấu hình cũ trước và xác định nó có đang dùng không. CNAME không được cùng tồn tại với A/AAAA ở cùng tên; simple CNAME cũng không trộn với cặp failover. Chỉ thay record `app` liên quan, không xóa NS/SOA hoặc A record hai origin.

### 5.2. Primary — AWS

Chọn **Create record**; nếu mở wizard, chọn **Failover routing**. Form thường có thể nằm ở **Switch to quick create**. Điền:

| Trường | Giá trị |
|---|---|
| Record name | `app` (giao diện thêm `.YOUR_DOMAIN`) |
| Record type | **CNAME** |
| Alias | **Off** |
| Value | `aws-origin.YOUR_DOMAIN` |
| TTL | `30` giây |
| Routing policy | **Failover** |
| Failover record type / Role | **Primary** |
| Record ID / Set identifier | `aws-primary` |
| Associate with health check | **Yes** nếu có tùy chọn |
| Health check | Chọn `dcs29-aws-https-ready` / đúng Health check ID AWS |

Bấm **Create records**. Value là hostname origin, không phải IP, không có `https://` hoặc `/version`. Không để record name trống: đó là apex, không dùng CNAME được.

### 5.3. Secondary — Azure

Chọn **Create record** lần nữa, điền các giá trị giống Primary ngoại trừ:

| Trường | Giá trị |
|---|---|
| Record name | Vẫn là `app` |
| Value | `azure-origin.YOUR_DOMAIN` |
| Failover record type / Role | **Secondary** |
| Record ID / Set identifier | `azure-secondary` |
| Health check | `dcs29-azure-https-ready` / đúng ID Azure |

Type vẫn **CNAME**, Alias **Off**, TTL **30**, Routing **Failover**, gắn health check **Yes**. Không đặt record name là `app-azure`; hai record phải cùng tên để Route 53 lựa chọn.

Bấm **Create records**. Danh sách cuối phải có **hai dòng CNAME cùng tên `app.YOUR_DOMAIN`**, khác role/identifier/value/check. Đây là hợp lệ với routing Failover. Không phải hai A record của origin; chúng vẫn giữ ở zone. Xem [AWS: giá trị failover record](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resource-record-sets-values-failover.html).

## 6. LAPTOP — quan sát tuyến shared và ghi dữ liệu

Đặt SHARED_HOST thực tế; gọi qua DNS thật, không --resolve:

```powershell
Resolve-DnsName app.YOUR_DOMAIN -Type A -Server 1.1.1.1
curl.exe --fail-with-body https://app.YOUR_DOMAIN/version
curl.exe --fail-with-body https://app.YOUR_DOMAIN/api/database/status
curl.exe --fail-with-body https://app.YOUR_DOMAIN/api/issues
```

Ban đầu AWS phục vụ; sau mục 4 Azure mới đủ điều kiện phục vụ ghi. Ghi UTC DNS đổi, giao dịch ghi thành công đầu tiên và lỗi client. 30 giây × threshold không phải RTO chắc chắn.

Khi cả hai writer check unhealthy, Route 53 có thể vẫn trả record theo hành vi fail-open; backend phải tiếp tục chặn ghi sai role. DNS không thay khóa writer. [AWS lựa chọn record](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-how-route-53-chooses-records.html).

## 7. Dữ liệu sau failover

Giữ cùng URL HTTPS /issues.html, tạo/sửa/bình luận/đóng issue trên Azure. Ghi ID/version/request_id, nội dung và lịch sử. Retry không trùng; bản nháp chưa gửi vẫn ở cùng localStorage và không phải giao dịch DB đã commit.

Giữ AWS cũ bị fence. Không Start EC2 với PostgreSQL primary cũ tự khởi động; trước khi bật lại phải có cơ chế chặn workload DB cũ và endpoint writer. Startup guard và các bước rejoin chi tiết ở mục 8.1–8.4 bên dưới kiểm soát lần khởi động lại; GĐ6B có hướng dẫn controller tự động AWS → Azure, nhưng rejoin/failback vẫn làm theo runbook này.

## 8. Rejoin AWS và failback có kiểm soát

1. Azure tiếp tục nhận ghi. AWS phục hồi trong trạng thái bị chặn writer cũ; ghi inventory PV/path và giữ storage cũ.
2. Tạo storage mới, clone AWS standby từ Azure bằng quy trình GĐ4 theo chiều ngược. Không init AWS thành primary mới độc lập.
3. Đợi streaming/replay và đối chiếu toàn bộ dữ liệu mới trên Azure.
4. Khóa ghi Azure/drain bằng câu lệnh mục 4.1 trên primary Azure; lấy WAL cuối, so với replay AWS.
5. Fence Azure bằng Portal/cơ chế đã kiểm chứng; promote AWS và mở gate theo mục 4.3 trên AWS.
6. Kiểm tra /health/database AWS 200 và dữ liệu, rồi Route 53 mới đủ điều kiện quay về AWS. VM AWS Running hoặc /health/live 200 chưa đủ điều kiện failback.
7. Dựng Azure standby từ AWS trên storage mới, xác nhận streaming và cả hai API.
8. Sau verify mới dọn đúng DB/PV cũ không dùng; giữ bản gần nhất ngắn hạn nếu cần đối chiếu.


### 8.1. Khôi phục VM cũ mà không bật primary cũ

Điều kiện: đã cấu hình startup guard `/run/issues-k3s-start-approved` ở hướng dẫn AWS/Azure **trước** khi gây lỗi. Sau failover, Start VM cũ và SSH; K3s phải bị chặn vì file /run chưa tồn tại. Không touch file phê duyệt trước các bước chuyển storage dưới.

```bash
sudo systemctl stop k3s
test ! -e /run/issues-k3s-start-approved || { echo 'Marker boot còn tồn tại: dừng và xác minh đây là VM cũ đã reboot/fence'; exit 1; }
sudo systemctl status k3s --no-pager
```

Lệnh status có thể trả mã khác 0 khi service failed/inactive; đây là trạng thái mong đợi lúc đang rejoin. Kiểm tra `ps`/`sudo ss -ltnp` không còn PostgreSQL lắng nghe tunnel 5432. Nếu còn tiến trình K3s/containerd cũ sau reboot bất thường, dừng xử lý và xác minh chúng đã dừng trước khi cho VM kết nối lại mạng DB. Startup guard chặn K3s, không thay fencing cloud.

### 8.2. Tạo một PVC mới, giữ nguyên storage cũ

K3s bị chặn nên chưa chạy kubectl. Trên VM cũ cài parser để sửa bản manifest local, lưu bản sao trước:

```bash
sudo apt-get install -y python3-yaml
cd /opt/dcs29
REJOIN_BACKUP="postgres-before-rejoin-$(date -u +%Y%m%dT%H%M%SZ).yaml"
cp -n postgres.yaml "$REJOIN_BACKUP"
nano rejoin-init.yaml
```

Dán init container sau vào rejoin-init.yaml. Mẫu này **AWS rejoin từ Azure**, SOURCE_DB_HOST=`10.200.29.2`. Khi Azure rejoin từ AWS ở cuối vòng, đổi host thành `10.200.29.1`:

```yaml
name: base-backup
image: POSTGRES_IMAGE_DIGEST
envFrom:
  - secretRef: {name: postgres-auth}
env:
  - {name: PGDATA, value: /var/lib/postgresql/data/pgdata}
  - {name: SOURCE_DB_HOST, value: "10.200.29.2"}
command: [bash, -ec]
args:
  - |
    install -d -o postgres -g postgres -m 700 /var/lib/postgresql/repl
    printf '%s:5432:*:replicator:%s\n' "$SOURCE_DB_HOST" "$REPLICATION_PASSWORD" > /var/lib/postgresql/repl/pgpass
    chown postgres:postgres /var/lib/postgresql/repl/pgpass
    chmod 600 /var/lib/postgresql/repl/pgpass
    export PGPASSFILE=/var/lib/postgresql/repl/pgpass
    if [ -s "$PGDATA/PG_VERSION" ]; then exit 0; fi
    mkdir -p "$PGDATA"
    test -z "$(ls -A "$PGDATA")" || { echo 'Partial copy: choose a new PVC'; exit 1; }
    chown postgres:postgres "$PGDATA"
    gosu postgres pg_basebackup -d "host=$SOURCE_DB_HOST port=5432 user=replicator passfile=$PGPASSFILE" -D "$PGDATA" -R -X stream -c fast
volumeMounts:
  - {name: data, mountPath: /var/lib/postgresql/data}
  - {name: repl-pass, mountPath: /var/lib/postgresql/repl}
```

Thay digest đúng bản PostgreSQL đang chạy. Clone dùng password replication vẫn có trong Secret cũ; nếu đã rotate phải cập nhật Secret trước. ConfigMap listen_addresses phải là tunnel IP **VM đang rejoin**, không phải IP source.

Tạo manifest `postgres-rejoin.yaml` với PVC mới và replicas=0; code chỉ viết file, không xóa tài nguyên:

```bash
python3 - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import yaml
docs=list(yaml.safe_load_all(Path('postgres.yaml').read_text()))
clone=yaml.safe_load(Path('rejoin-init.yaml').read_text())
new_name='postgres-data-rejoin-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
for doc in docs:
    if doc['kind']=='PersistentVolumeClaim':
        doc['metadata']['name']=new_name
    if doc['kind']=='StatefulSet':
        doc['spec']['replicas']=0
        pod=doc['spec']['template']['spec']
        pod['initContainers']=[clone]
        for volume in pod['volumes']:
            if volume['name']=='data': volume['persistentVolumeClaim']['claimName']=new_name
Path('postgres-rejoin.yaml').write_text(yaml.safe_dump_all(docs,sort_keys=False))
print('New PVC:',new_name)
PY
```

Kiểm tra file và ghi PVC mới vào inventory. Không tiếp tục nếu không đủ dung lượng giữ cả hai bản DB trên OS disk 30 GiB; tăng đĩa hoặc lưu backup ngoài VM trước.

### 8.3. Cài manifest vào hàng đợi trước khi K3s khởi động

Để kubelet không khởi động pod primary cũ trong lúc sửa StatefulSet, K3s khởi động tạm **không có agent**, rồi scale/update workload trước khi cho kubelet chạy. Trên VM rejoin:

```bash
set -euo pipefail
sudo install -d -m 700 /etc/rancher/k3s/config.yaml.d
sudo tee /etc/rancher/k3s/config.yaml.d/99-rejoin.yaml >/dev/null <<'EOF'
disable-agent: true
EOF
sudo touch /run/issues-k3s-start-approved
sudo systemctl reset-failed k3s
sudo systemctl start k3s
for attempt in $(seq 1 90); do
  if sudo k3s kubectl get --raw=/readyz >/dev/null 2>&1; then break; fi
  sleep 2
done
sudo k3s kubectl get --raw=/readyz >/dev/null
sudo k3s kubectl -n issues scale deployment/issues-app --replicas=0
sudo k3s kubectl -n issues scale statefulset/postgres --replicas=0
sudo k3s kubectl apply --dry-run=server -f /opt/dcs29/postgres-rejoin.yaml
sudo k3s kubectl apply -f /opt/dcs29/postgres-rejoin.yaml
sudo k3s kubectl -n issues delete pod postgres-0 --ignore-not-found --force --grace-period=0
sudo k3s kubectl -n issues get pods -l app=postgres
```

Chế độ disable-agent là tính năng K3s; kiểm tra release pin hỗ trợ trước demo. Không dùng k3s-killall/uninstall vì có thể thay đổi runtime/mạng/storage ngoài ý muốn. Trong server không agent, node cũ có thể NotReady; controller/API vẫn dùng được. Không xác minh được DB cũ đã dừng thì giữ VM bị fence.

Vì kubelet đang tắt và PostgreSQL cũ đã xác nhận dừng, xóa Pod object cũ để nó không được kubelet phục hồi từ manifest cũ. Chỉ khởi động agent khi không còn Pod postgres cũ và StatefulSet đã trỏ PVC mới với replicas=0:

```bash
set -euo pipefail
sudo k3s kubectl -n issues get statefulset postgres -o jsonpath='{.spec.template.spec.volumes}'
sudo k3s kubectl -n issues get pvc
sudo mv /etc/rancher/k3s/config.yaml.d/99-rejoin.yaml /opt/dcs29/99-rejoin.yaml.disabled
sudo systemctl restart k3s
sudo k3s kubectl -n issues scale statefulset/postgres --replicas=1
sudo k3s kubectl -n issues rollout status statefulset/postgres --timeout=300s
sudo k3s kubectl -n issues logs postgres-0 -c base-backup --tail=100
sudo k3s kubectl -n issues exec postgres-0 -- psql -U postgres -d issues -c 'SELECT pg_is_in_recovery(), pg_last_wal_replay_lsn(); SELECT status FROM pg_stat_wal_receiver;'
```

Rejoin phải recovery=true/streaming. Cập nhật EndpointSlice postgres-tunnel vẫn về tunnel IP local; Service app không đổi DSN. Patch **PV của PVC active mới** (không dùng lại tên postgres-data cũ) rồi bật app:

```bash
set -euo pipefail
ACTIVE_PVC=$(sudo k3s kubectl -n issues get statefulset postgres -o jsonpath='{.spec.template.spec.volumes[?(@.name=="data")].persistentVolumeClaim.claimName}')
test -n "$ACTIVE_PVC"
ACTIVE_PV=$(sudo k3s kubectl -n issues get pvc "$ACTIVE_PVC" -o jsonpath='{.spec.volumeName}')
test -n "$ACTIVE_PV"
sudo k3s kubectl patch pv "$ACTIVE_PV" -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
sudo k3s kubectl -n issues scale deployment/issues-app --replicas=1
sudo k3s kubectl -n issues rollout status deployment/issues-app --timeout=180s
```

Sau kiểm chứng role/replay/dữ liệu, ghi manifest mới thành `postgres.yaml` để vòng rejoin sau lấy đúng PVC active. Lệnh chỉ sửa file local, không apply thêm lần nữa; giữ bản trước rejoin:

```bash
cd /opt/dcs29
python3 - <<'PY'
from pathlib import Path
import yaml
source = Path('postgres-rejoin.yaml')
docs = list(yaml.safe_load_all(source.read_text()))
sets = [d for d in docs if d.get('kind') == 'StatefulSet' and d['metadata']['name'] == 'postgres']
if len(sets) != 1:
    raise SystemExit('Không tìm đúng một StatefulSet postgres; dừng')
sets[0]['spec']['replicas'] = 1
text = yaml.safe_dump_all(docs, sort_keys=False)
source.write_text(text)
Path('postgres.yaml').write_text(text)
PY
```

Không apply lại `postgres-before-rejoin-<UTC>.yaml`: nó trỏ dữ liệu cũ. Đối chiếu issue/comment mới Azure trên AWS trước failback. Init container tái tạo passfile mỗi lần pod được tạo, kể cả khi PGDATA đã tồn tại.

### 8.4. Failback

Nếu có GĐ6B, rule vẫn Disabled và mọi execution/SSM command phải terminal. Sau AWS standby đã bắt kịp, chạy drain/gate/WAL mục 4.1 **trên Azure primary**, so replay AWS. Stop Azure VM và đợi Stopped/Deallocated đúng máy. Promote AWS, mở gate theo mục 4.3; kiểm tra shared URL ghi được và Route 53 chọn AWS. Cuối cùng rejoin Azure bằng các bước 8.1–8.3 với source AWS `10.200.29.1`, PVC mới. Không init schema lần nữa.

Nguồn: [K3s server flags, disable-agent](https://docs.k3s.io/cli/server), [pg_basebackup](https://www.postgresql.org/docs/17/app-pgbasebackup.html). Đây là runbook demo cần thử trên VM trước buổi đo; không phải hệ thống tự động recovery đã nghiệm thu.

## 10. Nghỉ, rollback và evidence

Ghi writer thực tế và phase trước khi stop/deallocate. Hosted zone/health checks/disk/IP có thể tiếp tục tính phí. Rollback DNS không phải rollback dữ liệu: sau khi Azure đã nhận ghi, không trả tuyến về AWS DB cũ.

Giữ cấu hình Health check/record, UTC từng bước, WAL cutoff/replay, fencing trạng thái VM, promote/gate, dữ liệu/receipt và raw metrics. Che credential, không sửa raw data.

## 11. Checklist nghiệm thu

- [ ] Health check/record đúng origin, path writer readiness và SNI.
- [ ] AWS primary/Azure standby được phân biệt, không yêu cầu cả hai writer check healthy.
- [ ] Fence được xác minh trước promote; chỉ một writer.
- [ ] Dữ liệu Azure mới về AWS trước failback; không chỉ đổi DNS.
- [ ] URL/TLS/bản nháp và retry ổn định; Azure trở lại standby sau vòng.
- [ ] Có recovery runbook cho thao tác dở và inventory DB cũ.

## 12. Tiếp theo

[GĐ7 — giám sát độc lập](GIAI_DOAN_07_OBSERVABILITY.md) theo dõi pod/DB/URL; [GĐ8](GIAI_DOAN_08_KIEM_THU_FAILOVER.md) đo RTO/RPO trên VM thực tế. Chưa tự động hóa cloud khi runbook chưa nghiệm thu.

---

**Tham khảo / xử lý lỗi:** chỉ mở mục cần tra cứu. Nếu bước đang làm báo lỗi, dừng tại điểm kiểm tra và xem hướng dẫn tương ứng.

## 9. Lỗi và điểm dừng

<details>
<summary>Khi gặp lỗi — mở hướng dẫn</summary>

| Hiện tượng | Hành động |
|---|---|
| Standby writer check unhealthy | Bình thường khi writable=false; kiểm tra streaming/API đọc |
| Gate lock timeout | Không promote; kiểm tra transaction đang chạy và writer trực tiếp |
| Không xác minh được fencing | Dừng chuyển primary, tránh hai writer |
| Promote/verify lỗi | Chưa đổi DNS/gate; ghi phase/log để xử lý |
| DNS còn IP cũ | So authoritative/recursive, TTL/cache, backend cũ vẫn phải bị fence |
| AWS tự quay lại | Kiểm tra writer eligibility, không dùng /health/live cho failback |

</details>
