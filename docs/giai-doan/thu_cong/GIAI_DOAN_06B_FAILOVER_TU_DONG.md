# Giai đoạn 6B — Tạo failover tự động bằng AWS Console

Sau khi GĐ6 thủ công đã được diễn tập và hệ thống trở về **AWS primary/Azure standby**, có thể bổ sung CloudWatch Alarm → EventBridge → Step Functions Standard → Lambda. Lambda dừng EC2, xác nhận fencing rồi gửi SSM Run Command để promote PostgreSQL Azure. Route 53 chuyển DNS khi Azure trở thành writer sẵn sàng.

Tài liệu dành cho VM dựng bằng Console/Portal, **không chạy Terraform**. Dùng code Lambda và workflow có sẵn trong repository; mọi cấu hình Console, agent, quyền và vận hành được trình bày tại đây. Chưa nghiệm thu các thao tác này trên cloud thật. Không tạo trùng tài nguyên nếu đang dùng nhánh Terraform quản lý chúng.

## Đọc nhanh trước khi triển khai

| Nội dung | Tóm tắt |
|---|---|
| Mục tiêu | Tùy chọn: điều phối failover AWS → Azure bằng các dịch vụ AWS. |
| Chuẩn bị | Đã diễn tập GĐ6 và về baseline; monitoring bên ngoài sẵn sàng. |
| Hoàn tất khi | Controller fence trước promote; có evidence và quy trình rearm. |

**Luồng thao tác:** [1. Điều kiện và thông số cần ghi lại](#1-điều-kiện-và-thông-số-cần-ghi-lại) → [2. SSH — xác minh baseline và tạo marker nghiệm thu riêng](#2-ssh--xác-minh-baseline-và-tạo-marker-nghiệm-thu-riêng) → [3. AWS Console + SSH Azure — đăng ký SSM hybrid node](#3-aws-console--ssh-azure--đăng-ký-ssm-hybrid-node) → [5. Laptop — chuẩn bị ZIP, workflow và policy JSON](#5-laptop--chuẩn-bị-zip-workflow-và-policy-json) → [6. Console — IAM role, Lambda và Step Functions](#6-console--iam-role-lambda-và-step-functions) → [7. Console — CloudWatch alarm](#7-console--cloudwatch-alarm) → [8. Console — EventBridge rule, bật cuối cùng](#8-console--eventbridge-rule-bật-cuối-cùng) → [9. Diễn tập và chứng minh kết quả](#9-diễn-tập-và-chứng-minh-kết-quả) → [10. Lỗi, bảo trì, rejoin/failback và rearm](#10-lỗi-bảo-trì-rejoinfailback-và-rearm) → [11. Chi phí, dọn lab và nghiệm thu](#11-chi-phí-dọn-lab-và-nghiệm-thu).

**Bắt buộc:** thực hiện các bước cấu hình và kiểm tra của luồng đã chọn; chỉ bỏ qua khi bước đó ghi rõ tùy chọn hoặc điều kiện không áp dụng.

**Cách đọc:** các bước cấu hình và kiểm tra nằm ở phần chính; mục thu gọn cuối file dành cho tham khảo hoặc xử lý lỗi. Giữ số mục gốc để tra cứu các tham chiếu. Thay placeholder và chạy lệnh đúng laptop/VM được ghi tại bước đó.

**Tùy chọn:** chỉ làm giai đoạn này nếu chọn failover tự động; các điều kiện fencing, bảo trì và rearm vẫn bắt buộc.

## 1. Điều kiện và thông số cần ghi lại

- Đã hoàn tất GĐ4–GĐ5 và một vòng GĐ6 thủ công, gồm fencing, promote, rejoin và failback. Có backup ngoài VM và inventory PVC active.
- AWS primary/writable; Azure standby/streaming; hai app cùng digest/commit; HTTPS hai origin hợp lệ. Route 53 có health checks và cặp CNAME failover `app`.
- Hai VM có startup guard; sau reboot K3s chỉ được start khi đã xác định writer. Không bỏ guard để demo.
- Không có command/chuyển role đang chạy. Người thực hiện có quyền tạo IAM role/policy, Lambda, DynamoDB, CloudWatch Alarm, Step Functions, EventBridge, hybrid activation/Parameter Store và `iam:PassRole`.
- Code hiện dùng namespace `issues`, pod `postgres-0`, admin PostgreSQL `postgres`, database `issues`, PostgreSQL **17** và `/usr/local/bin/k3s`. Nếu đã đổi các tên này, chỉnh bản copy `azure_command.py` trước đóng gói và kiểm thử lại; không đổi tên database đang phục vụ chỉ để chạy hướng dẫn.

| Thông số | Ví dụ / nguồn |
|---|---|
| ACCOUNT_ID | 12 chữ số trong AWS Console hoặc `aws sts get-caller-identity` |
| PRIMARY_REGION | Region chứa EC2, ví dụ `ap-southeast-1` |
| PRIMARY_INSTANCE | ID đúng EC2 AWS, dạng `i-...` |
| AWS_HEALTH_CHECK_ID | ID check `/health/database` của origin AWS ở GĐ6 |
| DOMAIN | Domain thật, không có `https://` |
| AZURE_NODE_ID | `mi-...`, lấy sau đăng ký SSM ở mục 3 |
| Control Region | **us-east-1**, chọn tại góc phải Console cho mọi dịch vụ dưới đây |

IAM là dịch vụ toàn cục. EC2 giữ Region hiện tại; không chuyển VM sang us-east-1. Metric health check Route 53 được đọc tại us-east-1. Nếu API EC2 không truy cập được hoặc không chứng minh EC2 đã stopped, workflow **dừng trước promote**; đây chưa phải cơ chế tự DR toàn Region khi không thể fencing. [Route 53 metrics](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/monitoring-health-checks.html).

## 2. SSH — xác minh baseline và tạo marker nghiệm thu riêng

Trên **cả hai VM**, chạy:

```bash
sudo systemctl is-active k3s
sudo cat /etc/systemd/system/k3s.service.d/10-dr-start-guard.conf
test -f /run/issues-k3s-start-approved
sudo k3s kubectl -n issues get pods,pvc,svc,endpointslices
sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U postgres -d issues -c 'SHOW server_version_num; SELECT pg_is_in_recovery(); SELECT writes_enabled FROM app_control WHERE singleton=true;'
```

Guard phải chứa `ExecStartPre=/usr/bin/test -f /run/issues-k3s-start-approved`. AWS recovery=`f`, writes_enabled=`t`; Azure recovery=`t`. Gate Azure phản ánh giá trị đã replicate, nhưng standby vẫn không ghi được. Không tiếp tục nếu major khác 17 hoặc role sai.

**SSH AWS:**

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -U postgres -d issues -c 'SELECT state, sent_lsn, replay_lsn FROM pg_stat_replication;'
```

Phải thấy standby streaming. **SSH Azure:**

```bash
sudo k3s kubectl -n issues exec postgres-0 -- psql -U postgres -d issues -c 'SELECT status FROM pg_stat_wal_receiver; SELECT pg_is_wal_replay_paused(), pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn();'
```

Receiver streaming, replay không pause. Tạo một issue/bình luận trên AWS, đợi và đọc đúng nội dung/lịch sử từ Azure; kiểm tra `/version` và `/api/database/status` qua hai public origin trên laptop. `/health/database` AWS 200, Azure 503 là đúng baseline.

Chỉ sau khi xác minh baseline và diễn tập GĐ6 đạt, **SSH Azure** tạo marker riêng; marker chỉ ghi nhận nghiệm thu, không cấu hình DB hoặc đảm bảo dữ liệu luôn đồng bộ:

```bash
sudo mkdir -p /opt/dcs29
# Giữ owner/quyền thư mục manifest đã tạo ở GĐ3/GĐ4.
sudo python3 - <<'PY'
import json, os
from datetime import datetime, timezone
from pathlib import Path
p = Path('/opt/dcs29/manual-dr-baseline.json')
if p.exists():
    raise SystemExit('Marker đã có: đối chiếu baseline trước, không ghi đè mù quáng')
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    json.dump({'mode': 'console', 'verified_at': datetime.now(timezone.utc).isoformat()}, f)
PY
```

Không tạo `automation-result.json`: file đó thuộc deployment Terraform. Bản Lambda đóng gói ở mục 5 sẽ kiểm tra `manual-dr-baseline.json`; vẫn kiểm tra role/replay/K3s mỗi lần chạy.

## 3. AWS Console + SSH Azure — đăng ký SSM hybrid node

### 3.1. IAM role cho agent

IAM → Roles → Create role → Custom trust policy, dán:

```json
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ssm.amazonaws.com"},"Action":"sts:AssumeRole"}]}
```

Attach managed policy **AmazonSSMManagedInstanceCore**, đặt role name `dcs29-azure-ssm`. Không gắn quyền EC2 Stop vào role agent.

### 3.2. Hybrid activation

Chọn us-east-1 → Systems Manager → **Hybrid Activations** → Create activation:

- Description/default instance name: `dcs29-azure-dr`.
- Instance limit: **1**; chọn IAM role vừa tạo, không tạo role khác tự động.
- Expiration: khoảng một giờ nếu Console hỗ trợ; nếu chỉ chọn ngày, dùng hạn ngắn nhất phù hợp rồi xóa activation ngay sau đăng ký.
- Lưu Activation ID và Code tạm trong nơi riêng; code chỉ hiển thị khi tạo, không commit/chụp màn hình.

### 3.3. Cài agent trên Azure

Agent chỉ cần **outbound HTTPS 443** tới SSM/ssmmessages và S3 us-east-1, DNS và đồng hồ chính xác. Không mở inbound 22/5432/6443 cho Lambda; không đưa Azure client secret hoặc SSH key vào Lambda.

Trong **SSH Azure**, kiểm tra trước:

```bash
sudo test ! -e /var/lib/amazon/ssm/registration || { echo 'SSM đã đăng ký: dừng để đối chiếu identity'; exit 1; }
test ! -d /snap/amazon-ssm-agent || { echo 'SSM snap đã có: dừng để đối chiếu installation'; exit 1; }
```

Nếu lệnh trả lỗi do đã có registration hoặc snap agent, dừng và kiểm tra identity/service hiện có; không chạy clear/uninstall để ép đăng ký. Node Online thuộc đúng account/Region có thể được dùng lại sau khi đối chiếu identity. Mẫu bên dưới dành cho VM chưa đăng ký.

```bash
sudo install -d -m 700 /tmp/dcs29-ssm
sudo curl --fail --show-error --location https://amazon-ssm-us-east-1.s3.us-east-1.amazonaws.com/latest/debian_amd64/ssm-setup-cli -o /tmp/dcs29-ssm/ssm-setup-cli
sudo chmod 700 /tmp/dcs29-ssm/ssm-setup-cli
read -r -p 'Activation ID: ' DR_ACTIVATION_ID
read -r -s -p 'Activation Code: ' DR_ACTIVATION_CODE; printf '\n'
sudo /tmp/dcs29-ssm/ssm-setup-cli -register -activation-code "$DR_ACTIVATION_CODE" -activation-id "$DR_ACTIVATION_ID" -region us-east-1
unset DR_ACTIVATION_CODE DR_ACTIVATION_ID
sudo systemctl enable --now amazon-ssm-agent
sudo systemctl status amazon-ssm-agent --no-pager
```

Không bật shell tracing (`set -x`), không đặt code literal vào command history. Installer thực hiện đăng ký; không chạy lại lệnh nếu chưa biết lần trước đã đăng ký thành công hay chưa. [AWS hybrid Linux installer](https://docs.aws.amazon.com/systems-manager/latest/userguide/hybrid-multicloud-ssm-agent-install-linux.html).

Console → Systems Manager → Fleet Manager/Managed nodes: tìm `dcs29-azure-dr`, xác nhận **Online**, ghi `AZURE_NODE_ID`. Add tags `Project=dcs29`, `Purpose=azure-dr`. Xóa activation trong Hybrid Activations; việc này không deregister node đã đăng ký.

Systems Manager → **Parameter Store** → Create parameter:

| Trường | Giá trị |
|---|---|
| Name | `/dcs29/dr/azure-node` |
| Tier | Standard |
| Type | String |
| Value | `AZURE_NODE_ID` thật |

ID không phải password. Test agent bằng Systems Manager → Run Command → Run command → `AWS-RunShellScript` → chọn đúng node Azure, commands:

```bash
systemctl is-active k3s
/usr/local/bin/k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U postgres -d issues -At -c 'SELECT pg_is_in_recovery()'
```

Command phải Success, output active và `t`. Không chạy pg_promote ở bước này. Agent/command không hoạt động thì sửa trước khi tạo trigger.

## 4. Console — DynamoDB và CloudWatch Logs

**DynamoDB us-east-1 → Tables → Create table:** name `dcs29-dr-latch`, partition key `id` String, không sort key; chọn Customize settings → Capacity mode **On-demand**. Không bật TTL. Không cần tạo item; Lambda sẽ tạo `id=writer` khi nhận incident. Latch được giữ cả khi workflow thất bại; rearm thủ công ở mục 10.

**CloudWatch us-east-1 → Logs → Log groups → Create log group:** `/aws/lambda/dcs29-auto-failover`; đặt retention **7 days**. Tạo trước để IAM Lambda không cần quyền tạo log group tùy ý.

## 5. Laptop — chuẩn bị ZIP, workflow và policy JSON

Mở PowerShell tại **root repository**. Chỉ tạo file local; các lệnh không tạo cloud resource. Thay placeholder bằng thông số mục 1/3, giữ tên resource bên dưới nhất quán:

```powershell
$ErrorActionPreference = 'Stop'
$AccountId = 'ACCOUNT_ID'
$PrimaryRegion = 'PRIMARY_REGION'
$PrimaryInstance = 'PRIMARY_INSTANCE'
$AzureNode = 'AZURE_NODE_ID'
$Domain = 'YOUR_DOMAIN'
$DrName = 'dcs29-auto-failover'
$LambdaArn = "arn:aws:lambda:us-east-1:${AccountId}:function:$DrName"
$WorkflowArn = "arn:aws:states:us-east-1:${AccountId}:stateMachine:$DrName"
$DrDir = Join-Path $env:TEMP ('dcs29-console-dr-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $DrDir | Out-Null
$Utf8 = [Text.UTF8Encoding]::new($false)
Copy-Item -LiteralPath deploy/terraform/failover/lambda/handler.py -Destination "$DrDir/handler.py"
$Command = [IO.File]::ReadAllText((Resolve-Path deploy/terraform/failover/lambda/azure_command.py))
if (-not $Command.Contains('automation-result.json')) { throw 'Kiểm tra lại marker trong source trước đóng gói' }
$Command = $Command.Replace('automation-result.json', 'manual-dr-baseline.json')
[IO.File]::WriteAllText("$DrDir/azure_command.py", $Command, $Utf8)
Compress-Archive -LiteralPath "$DrDir/handler.py", "$DrDir/azure_command.py" -DestinationPath "$DrDir/lambda.zip"
$Definition = [IO.File]::ReadAllText((Resolve-Path deploy/terraform/failover/workflow.json.tmpl))
$Definition = $Definition.Replace('${lambda_arn}', $LambdaArn)
$Definition | ConvertFrom-Json | Out-Null
[IO.File]::WriteAllText("$DrDir/workflow.json", $Definition, $Utf8)
$DrDir
```

ZIP phải có **handler.py và azure_command.py ngay tại root**, không có folder cha. Bản copy đổi duy nhất marker nghiệm thu; source repo/Terraform không bị sửa. SDK boto3 có trong runtime Lambda; không đưa venv/test/SSH key/password vào ZIP.

Tạo policy Lambda bằng block tiếp theo trong **cùng phiên PowerShell**, giữ biến đã điền:

```powershell
$LambdaPolicy = @{
  Version = '2012-10-17'
  Statement = @(
    @{ Effect='Allow'; Action=@('logs:CreateLogStream','logs:PutLogEvents'); Resource="arn:aws:logs:us-east-1:${AccountId}:log-group:/aws/lambda/${DrName}:*" },
    @{ Effect='Allow'; Action=@('dynamodb:PutItem','dynamodb:GetItem','dynamodb:UpdateItem'); Resource="arn:aws:dynamodb:us-east-1:${AccountId}:table/dcs29-dr-latch" },
    @{ Effect='Allow'; Action='ec2:DescribeInstances'; Resource='*' },
    @{ Effect='Allow'; Action='ec2:StopInstances'; Resource="arn:aws:ec2:${PrimaryRegion}:${AccountId}:instance/$PrimaryInstance" },
    @{ Effect='Allow'; Action='cloudwatch:DescribeAlarms'; Resource="arn:aws:cloudwatch:us-east-1:${AccountId}:alarm:$DrName" },
    @{ Effect='Allow'; Action='ssm:GetParameter'; Resource="arn:aws:ssm:us-east-1:${AccountId}:parameter/dcs29/dr/azure-node" },
    @{ Effect='Allow'; Action=@('ssm:DescribeInstanceInformation','ssm:GetCommandInvocation'); Resource='*' },
    @{ Effect='Allow'; Action='ssm:SendCommand'; Resource='arn:aws:ssm:us-east-1::document/AWS-RunShellScript' },
    @{ Effect='Allow'; Action='ssm:SendCommand'; Resource="arn:aws:ssm:us-east-1:${AccountId}:managed-instance/$AzureNode" }
  )
} | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText("$DrDir/lambda-policy.json", $LambdaPolicy, $Utf8)
$StatesPolicy = @{ Version='2012-10-17'; Statement=@(@{ Effect='Allow'; Action='lambda:InvokeFunction'; Resource=$LambdaArn }) } | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText("$DrDir/states-policy.json", $StatesPolicy, $Utf8)
$EventsPolicy = @{ Version='2012-10-17'; Statement=@(@{ Effect='Allow'; Action='states:StartExecution'; Resource=$WorkflowArn }) } | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText("$DrDir/events-policy.json", $EventsPolicy, $Utf8)
```

Lambda chỉ Stop đúng EC2, gửi command đúng node Azure, không có StartInstances/DeleteItem hoặc quyền promote host khác. Nếu node được thay mới sau migration, phải cập nhật **cả parameter và IAM node ARN** sau đối chiếu; không mở Resource `*` để chữa AccessDenied.

## 6. Console — IAM role, Lambda và Step Functions

### 6.1. Ba execution role

IAM → Roles → Create role → Custom trust policy. Với mỗi dòng, dùng JSON bên dưới, thay `SERVICE` bằng giá trị tương ứng:

```json
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"SERVICE"},"Action":"sts:AssumeRole"}]}
```

| Role name | SERVICE | Inline policy JSON đã tạo trên laptop |
|---|---|---|
| `dcs29-dr-lambda` | `lambda.amazonaws.com` | `lambda-policy.json` |
| `dcs29-dr-states` | `states.amazonaws.com` | `states-policy.json` |
| `dcs29-dr-events` | `events.amazonaws.com` | `events-policy.json` |

Tạo role không attach admin policy. Mở từng role → Permissions → Add permissions → Create inline policy → JSON; mở file tương ứng trong `$DrDir` bằng editor, dán **toàn bộ nội dung**, Review/Create policy, đặt tên `dcs29-dr-runtime`. Ghi role ARN. Không thêm quyền rộng mặc định khi Console gợi ý.

### 6.2. Lambda

us-east-1 → Lambda → Create function → Author from scratch:

| Trường | Giá trị |
|---|---|
| Function name | `dcs29-auto-failover` |
| Runtime / architecture | Python 3.12 / x86_64 |
| Execution role | Use existing role `dcs29-dr-lambda` |
| Memory / timeout | 128 MB / **60 seconds** |
| VPC | Không gắn VPC; dùng public AWS APIs và HTTPS origins |

Code → Upload from → `.zip file` → chọn `$DrDir/lambda.zip`; Runtime settings → handler **handler.handler**. Configuration → General configuration → sửa timeout; Configuration → Environment variables → thêm:

| Key | Value |
|---|---|
| PRIMARY_REGION | Region EC2 thật |
| PRIMARY_INSTANCE | ID EC2 thật |
| AWS_ORIGIN | `https://aws-origin.YOUR_DOMAIN` |
| AZURE_ORIGIN | `https://azure-origin.YOUR_DOMAIN` |
| ALARM_NAME | `dcs29-auto-failover` |
| LOCK_TABLE | `dcs29-dr-latch` |
| NODE_PARAMETER | `/dcs29/dr/azure-node` |
| EXECUTION_PREFIX | `arn:aws:states:us-east-1:ACCOUNT_ID:execution:dcs29-auto-failover:` |

Giữ dấu `:` cuối EXECUTION_PREFIX; thay ACCOUNT_ID/domain thật. Không cấu hình trigger Lambda trực tiếp từ alarm; EventBridge sẽ gọi workflow. Không dùng Lambda Test với action `fence`/`promote` để kiểm tra cài đặt.

### 6.3. Step Functions

us-east-1 → Step Functions → State machines → Create state machine → Create from blank. Chọn **Standard**, name `dcs29-auto-failover`. Trong Code/ASL editor dán toàn bộ `$DrDir/workflow.json`; Validate definition. Permissions chọn **existing role `dcs29-dr-states`**; không để Console tạo role rộng. Không bật execution logging/CloudWatch Logs cho state machine trong cấu hình tối thiểu này; dùng Execution history.

Workflow timeout 1.500 giây; xác nhận graph có thứ tự:

`AcquireIncident → ConfirmFailure → RecheckDelay → ConfirmAgain → AzurePreflight → RequestFence → FenceResult/Fenced → PromoteAzure → VerifyWriter → CompleteIncident`.

Chỉ nhánh `Fenced` ready=true được tới `PromoteAzure`. Các vòng polling hữu hạn; command ghi/mutation không tự retry mù quáng. Chưa bấm Start execution; bộ điều phối có thể Stop VM khi đã nhận incident hợp lệ.

## 7. Console — CloudWatch alarm

us-east-1 → CloudWatch → Alarms → Create alarm → Select metric → **Route 53 / Health Check Metrics**. Chọn **HealthCheckStatus** với dimension **HealthCheckId=AWS_HEALTH_CHECK_ID** đã ghi. Không chọn check Azure hoặc domain chung.

| Trường | Giá trị |
|---|---|
| Statistic / period | Minimum / 1 minute (60 seconds) |
| Threshold type | Static |
| Condition | Lower than `1` |
| Datapoints to alarm | **2 out of 2** |
| Missing data treatment | **Treat missing data as good / not breaching** |
| Alarm name | `dcs29-auto-failover` |
| SNS/EC2 alarm actions | Không cấu hình; EventBridge điều phối riêng |

Tạo alarm và đợi **OK** ở baseline. Nếu metric chưa xuất hiện, đối chiếu đúng us-east-1/check ID và đợi dữ liệu; không thay bằng EC2 CPU metric. Không dùng SetAlarmState giả ALARM để nghiệm thu mất VM: Lambda còn xác minh public writer health.

Health checks GĐ6 giữ standard interval 30 giây, failure threshold 3. Alarm thêm 2 datapoint phút để giảm chuyển cloud do lỗi ngắn; đây không phải demo failover chắc chắn trong 30 giây.

## 8. Console — EventBridge rule, bật cuối cùng

us-east-1 → EventBridge → Rules → Create rule:

- Name `dcs29-auto-failover`, event bus **default**, rule with event pattern.
- Event source AWS events; Custom pattern/JSON editor, dán mẫu dưới sau thay ACCOUNT_ID.
- Target: AWS service → **Step Functions state machine** → `dcs29-auto-failover`; execution role **existing `dcs29-dr-events`**.
- Target input: **Matched event / toàn bộ event**, không tạo Input transformer hoặc payload Lambda riêng. Workflow tự dùng `$$.Execution.Id` làm owner.
- Giữ rule **Disabled** khi đang kiểm tra cấu hình. Nếu wizard không cho tạo Disabled, bỏ chọn Enable rule nếu có hoặc disable ngay sau tạo; chỉ thực hiện khi baseline alarm OK, không đang diễn tập.

```json
{
  "source": ["aws.cloudwatch"],
  "detail-type": ["CloudWatch Alarm State Change"],
  "resources": ["arn:aws:cloudwatch:us-east-1:ACCOUNT_ID:alarm:dcs29-auto-failover"],
  "detail": {
    "alarmName": ["dcs29-auto-failover"],
    "state": {"value": ["ALARM"]}
  }
}
```

Đối chiếu target/role/Region, agent Online, parameter đúng Azure, alarm OK, startup guard và backup. Sau đó **Enable rule**; từ lúc này sự cố có thể tự Stop EC2/promote Azure. Bật rule khi alarm đã ALARM không đảm bảo nhận lại sự kiện cũ; phải phục hồi baseline/OK trước.

[CloudWatch alarm events](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch-and-eventbridge.html) · [EventBridge → Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/tutorial-cloudwatch-events-s3.html).

## 9. Diễn tập và chứng minh kết quả

1. Bật monitoring/evidence bên ngoài hai VM. Tạo issue/comment trên AWS, ghi request ID, ACK và thời điểm UTC; xác nhận Azure nhận được trước thử nghiệm planned.
2. Trong EC2 Console ở PRIMARY_REGION, Stop **đúng** EC2 để mô phỏng sự cố. Không promote bằng SSH song song; không xóa pod PostgreSQL để giả fencing.
3. Theo dõi Route 53 health AWS → Unhealthy, CloudWatch → ALARM, Step Functions execution mới. Xem từng state và Systems Manager Run Command invocation. Workflow lấy latch, xác minh hai lần cách 20 giây, preflight standby, chờ EC2 stopped, promote, mở gate rồi verify public Azure.
4. Xác nhận EC2 **stopped**, Azure `/api/database/status` primary/writable, `/health/database` 200; Route 53 Azure Healthy, shared hostname dần về Azure theo cache.

**PowerShell laptop**, thay domain thật:

```powershell
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/api/database/status
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/health/database
curl.exe --fail-with-body https://app.YOUR_DOMAIN/version
curl.exe --fail-with-body https://app.YOUR_DOMAIN/api/issues
```

5. Đọc issue/comment/history cũ, tạo issue mới qua shared URL, xác nhận environment azure và ghi ACK thành công. Lưu execution ARN, SSM command IDs, fencing evidence, DNS/HTTP timeline và RTO/RPO. Không chỉ nhìn execution Succeeded rồi kết luận shared DNS đã đổi.

RTO gồm health check, alarm, xác minh, EC2 Stop, SSM, writer readiness và DNS cache. Replication **bất đồng bộ** có thể mất WAL chưa truyền; preflight chờ replay WAL đã nhận, không chứng minh toàn bộ commit AWS đã đến Azure. Bản nháp chưa save vẫn ở browser, không được PostgreSQL replicate.

## 10. Lỗi, bảo trì, rejoin/failback và rearm

Nếu AWS hồi phục, Azure offline/replay pause, DNS/TLS không đáng tin, StopInstances lỗi hoặc SSM timeout, workflow giữ latch và dừng để kiểm tra. Timeout không chứng minh command chưa chạy; xem invocation và role thực tế trước thao tác tiếp. Không xóa latch/receipt để thử lại khi chưa xác định writer.

### 10.1. Tạm dừng trigger

EventBridge → rule → **Disable**. Step Functions → Executions: xác nhận không còn Running; Systems Manager → Run Command: mọi command của node phải terminal, không Pending/InProgress/Delayed/Cancelling. Disable rule hoặc Stop execution **không tự hủy SSM command đã gửi**.

Giữ EC2 cũ bị fence. Sau khi command đã terminal và xác định role, thực hiện rejoin trên **PVC mới**, failback, rồi rejoin Azure theo GĐ6 mục 8.1–8.4. Runbook rejoin dài đã giữ nguyên tại [GĐ6 thủ công](GIAI_DOAN_06_ROUTE53_FAILOVER.md); đây là quy trình phục hồi đã có, không phải bước tạo controller còn thiếu. Không Start AWS primary cũ chỉ để domain quay lại AWS.

### 10.2. Rearm sau khi đã trở lại baseline

Chỉ khi AWS primary/writable, Azure standby/streaming, cùng commit, AWS writer Healthy/alarm OK và toàn bộ execution/command cũ terminal:

**SSH Azure:**

```bash
test "$(sudo k3s kubectl -n issues exec postgres-0 -- psql -v ON_ERROR_STOP=1 -U postgres -d issues -At -c 'SELECT pg_is_in_recovery()')" = t || exit 1
sudo cat /opt/dcs29/failover-incident.json
sudo rm -f /opt/dcs29/failover-incident.json
```

Đối chiếu owner trong receipt với execution đang xử lý trước xóa. Nếu incident dừng trước promote và chưa có receipt, cat báo không tồn tại; vẫn kiểm tra role/command trước bước latch. Giữ `manual-dr-baseline.json`; không xóa marker baseline để tắt trigger.

**DynamoDB Console:** Tables → `dcs29-dr-latch` → Explore table items, đọc item `id=writer`, lưu owner/state vào evidence. Khi rule Disabled và mọi command đã terminal, xóa **đúng item này**, không xóa table. Latch không TTL, không tự reset sau Succeeded/Failed.

Xác minh CloudWatch alarm **OK** rồi Enable EventBridge rule. Nếu Azure vẫn primary hoặc AWS còn guard/rejoin chưa xong, giữ rule Disabled và giữ latch. Failback không tự động chạy từ alarm OK.

### 10.3. Chẩn đoán

| Biểu hiện | Kiểm tra / hành động |
|---|---|
| Agent Offline | Service agent, outbound 443/DNS/time và đúng account/Region; không clear identity |
| AccessDenied StopInstances | Role Lambda, PRIMARY_REGION/INSTANCE và đúng EC2 ARN |
| AccessDenied SendCommand | Role Lambda, node parameter và đúng managed-instance ARN/document ARN |
| Marker missing | Baseline chưa nghiệm thu hoặc ZIP dùng nhầm marker Terraform; không tạo marker trước xác minh |
| Command Success nhưng workflow fail | Xem output `DCS29_RESULT=`, role thực tế và public HTTPS; không promote lần nữa mù quáng |
| DuplicateIgnored | Incident latch vẫn tồn tại; kiểm tra incident cũ, chỉ rearm khi baseline phục hồi |
| DNS còn AWS | Health check writer Azure, authoritative DNS/TTL/cache; giữ AWS bị fence |
| Không có execution | Rule Enabled, pattern alarm ARN/state/Region, target role; sự kiện cũ không tự phát lại |

## 11. Chi phí, dọn lab và nghiệm thu

Có thêm DynamoDB on-demand và SSM hybrid ngoài CloudWatch/EventBridge/Lambda/Step Functions. Theo giá SSM hiện hành, Run Command trên node ngoài EC2 tính **0,002 USD/invocation**; một vòng bình thường gửi preflight và promote, không gửi lại command mỗi lần poll. Kiểm tra giá/hạn mức tài khoản trước dùng; không cam kết tổng phí bằng 0. Không tạo NAT Gateway hoặc Lambda VPC cho luồng này. [SSM pricing](https://aws.amazon.com/systems-manager/pricing/).

Trước dọn: Disable rule, mọi execution/SSM command terminal, backup và export evidence. Trong SSM deregister **đúng node Azure**, xóa parameter `/dcs29/dr/azure-node`; xóa activation còn sót. Sau đó xóa rule/target, workflow, Lambda, latch table, log group và bốn IAM role/policy đã tạo ở đây. Không xóa VM/DB/Route 53 hoặc role TLS nếu chỉ gỡ controller. Deregister agent không phục hồi database role, không thay failback.

- [ ] Baseline và diễn tập thủ công đã đạt; guard/backup còn nguyên.
- [ ] Agent Azure Online, parameter/node ARN đúng; không mở DB public.
- [ ] IAM giới hạn đúng resource, Lambda ZIP root đúng, marker riêng cho Console.
- [ ] Workflow Standard, fencing gate trước promote, alarm 2/2 và EventBridge pattern đúng.
- [ ] Diễn tập chứng minh chỉ một writer, dữ liệu cũ và ghi mới trên Azure.
- [ ] Có evidence RTO/RPO và biết Disable/rejoin/failback/rearm.

Kiểm thử local của code không xác nhận IAM, SSM registration hoặc EventBridge delivery thực tế. Chỉ đánh dấu GĐ6B cloud pass sau nghiệm thu trên hai VM.

[Quay lại lộ trình thủ công](README.md) · [GĐ7 monitoring](GIAI_DOAN_07_OBSERVABILITY.md) · [GĐ8 kiểm thử](GIAI_DOAN_08_KIEM_THU_FAILOVER.md).
