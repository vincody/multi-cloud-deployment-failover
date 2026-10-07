# GĐ6B — Failover tự động AWS → Azure

Terraform có thể triển khai CloudWatch Alarm, EventBridge, Step Functions Standard, Lambda, DynamoDB incident latch và SSM Agent trên Azure. Mặc định `enable_auto_failover=false`. Chưa chạy apply cloud hoặc nghiệm thu trên VM thật trong quá trình phát triển.

## 1. Phạm vi và trình tự

| Bước | Thành phần | Điều kiện/hành động |
|---|---|---|
| 1 | Route 53 | Kiểm tra `/health/database` AWS; interval 30 giây, threshold 3 hiện có |
| 2 | CloudWatch | `HealthCheckStatus < 1` trong 2 datapoint 60 giây; missing data không kích hoạt |
| 3 | EventBridge | Chỉ nhận ALARM của alarm `dcs29-auto-failover`, bắt đầu workflow |
| 4 | Lambda/DynamoDB | Ghi latch độc quyền cho incident; sự kiện trùng không tiếp tục |
| 5 | Lambda | Alarm vẫn ALARM và probe độc lập xác nhận AWS không sẵn sàng; kiểm tra lại sau 20 giây |
| 6 | SSM Azure | K3s đang chạy, PostgreSQL 17 là standby, replay không pause và đã replay WAL nhận được |
| 7 | Lambda/EC2 | Yêu cầu Stop EC2, poll tới đúng trạng thái `stopped`; không dùng force-stop |
| 8 | Lambda/SSM | Kiểm tra EC2 vẫn stopped, chạy promote trên Azure qua Run Command |
| 9 | PostgreSQL | Ghi incident receipt trước promote, promote và mở writer gate, kiểm tra lại role |
| 10 | Lambda | Public HTTPS Azure báo primary/writable và EC2 vẫn stopped |
| 11 | Route 53 | Writer health Azure thành Healthy, DNS failover chọn Azure; client còn cache cần thời gian |

Step Functions thực hiện các bước chờ và polling hữu hạn, timeout toàn workflow 25 phút. Lambda không giữ SSH key, không kết nối 5432 public và không tự khởi động K3s bị guard. Gửi SSM command không tự retry khi chưa biết command trước đã thực thi chưa; promotion cùng incident có receipt để tránh promote lặp.

## 2. Region và quyền

CloudWatch metric của Route 53 được đọc ở **us-east-1**, nên alarm, EventBridge, Step Functions, Lambda, latch và SSM Azure nằm ở đó. EC2 vẫn dùng `aws_region` của project (ví dụ ap-southeast-1). Control plane ở ngoài VM AWS; với region chính khác us-east-1, nó cũng ở khác Region.

Đây là automation cho sự cố VM/app trong điều kiện API EC2 vẫn truy cập được. **Nếu cả Region AWS mất khả dụng và không thể xác nhận EC2 đã dừng, workflow dừng trước promote.** Nó không đảm bảo DR tự động toàn Region khi không còn bằng chứng fencing.

Azure được đăng ký thành SSM hybrid/multicloud node; không cần Azure client secret hoặc SSH runtime từ Lambda. Agent gọi endpoint SSM/ssmmessages us-east-1 qua outbound HTTPS 443. Không mở thêm inbound 22/5432/6443 cho Lambda. Registration dùng activation một máy, hết hạn sau một giờ và được xóa sau đăng ký; activation code chỉ đi qua bộ nhớ/SSH stdin, không nằm trong Terraform state.

User chạy Terraform cần AWS CLI đăng nhập đúng **cùng account Terraform** và thêm quyền (bao gồm `sts:GetCallerIdentity` để đối chiếu account):

- Tạo IAM role/policy, Lambda, Step Functions, CloudWatch/EventBridge và DynamoDB trong us-east-1.
- `ssm:CreateActivation`, `DeleteActivation`, `DescribeInstanceInformation`, `AddTagsToResource`, `PutParameter`.
- `iam:PassRole` cho role `dcs29-azure-ssm` khi tạo activation.

Lambda chỉ được Stop đúng EC2, gửi command đến SSM node gắn tag Project=dcs29/Purpose=azure-dr, đọc node parameter và quản lý incident của project. Lambda **không có DeleteItem để tự xóa latch**, không có StartInstances, không cập nhật DNS trực tiếp và không tự failback.

## 3. Trước khi bật

Hoàn tất bốn lượt Terraform hạ tầng/app, HTTPS, health check, DNS. Làm GĐ6A: fencing/promote/rejoin/failback thủ công, rồi trả hệ thống về baseline:

- AWS primary/writable, Azure standby/streaming, cùng app commit.
- Hai public origin HTTPS hoạt động, startup guard có trên cả hai VM.
- AWS writer health Healthy, Azure standby writer health Unhealthy là đúng.
- Không còn diễn tập/command chuyển role đang chạy, có backup ngoài VM và inventory PVC active.

Terraform không biết buổi diễn tập đã pass; bạn phải xác nhận thực tế trước bật flag. Bootstrap kiểm tra lại role/gate/streaming và guard trước khi đăng ký agent, nhưng không thay thế nghiệm thu fencing/rejoin.

VM Azure có SSM registration/snap từ một hệ thống khác sẽ bị từ chối tự nhận; cần migration/adoption riêng. Không xóa agent identity để ép đăng ký lại. Trên VM mới của project, automation cài agent từ AWS và lưu marker `/opt/dcs29/ssm-dr.json` root-only.

## 4. Lượt apply GĐ6B

Trong `deploy/terraform/terraform.tfvars` giữ các flag cũ và thêm:

```hcl
enable_workload_automation = true
enable_https               = true
enable_health_checks       = true
enable_failover            = true
enable_auto_failover       = true
```

Trên **PowerShell laptop**, thư mục `deploy/terraform`:

```powershell
terraform init
terraform fmt -check
terraform validate
terraform test
terraform plan -out=auto-failover.tfplan
# Đọc plan: thêm control resources/SSM; không replace VM, disk hoặc PVC active.
terraform apply auto-failover.tfplan
terraform output auto_failover
```

`terraform init` bổ sung provider archive để đóng gói Lambda. Bootstrap dùng Python/OpenSSH và AWS CLI trên laptop; không cần boto3 trên laptop để triển khai. Code Lambda dùng SDK có sẵn trong runtime.

Terraform tạo hybrid role, kiểm tra baseline, đăng ký/reuse agent Azure, chờ Online, gắn tag và ghi node ID vào Parameter Store Standard `/dcs29/dr/azure-node`. ID không phải credential. Resource `terraform_data.azure_ssm` không tự promote database trong apply. Sau đó tạo workflow/alarm/event target; từ thời điểm rule được bật, sự cố có thể tự dẫn tới Stop EC2/promote Azure.

Trong **AWS Console**, chọn Region us-east-1:

- Systems Manager → Managed nodes: `dcs29-azure-dr` Online.
- Parameter Store: `/dcs29/dr/azure-node` trỏ đúng node Azure.
- CloudWatch → Alarms: `dcs29-auto-failover` về OK sau đủ dữ liệu baseline.
- EventBridge → Rules: `dcs29-auto-failover` Enabled, target là workflow.
- Step Functions: state machine Standard `dcs29-auto-failover`, chưa có execution đang promote.

## 5. Diễn tập và kiểm chứng

Bật monitoring/evidence trước. Khi đã cho phép gây sự cố, Stop đúng EC2 hoặc gây lỗi có kiểm soát; không promote Azure bằng tay song song với workflow. Theo dõi execution trên Step Functions:

```powershell
$WorkflowArn = terraform output -json auto_failover | ConvertFrom-Json | Select-Object -ExpandProperty workflow
aws stepfunctions list-executions --state-machine-arn $WorkflowArn --region us-east-1
```

Sau completion, kiểm tra trên laptop, thay YOUR_DOMAIN thật:

```powershell
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/api/database/status
curl.exe --fail-with-body https://azure-origin.YOUR_DOMAIN/health/database
curl.exe --fail-with-body https://app.YOUR_DOMAIN/version
curl.exe --fail-with-body https://app.YOUR_DOMAIN/api/issues
```

Azure primary/writable, EC2 stopped, shared URL về environment azure sau DNS cache; issue/comment/history đã replicate vẫn đọc được. Ghi giao dịch ACK và timeline/RTO/RPO theo GĐ8. AWS ALARM vẫn có thể tồn tại sau failover; latch chặn các incident trùng, không tự trả AWS về primary.

Thời gian gồm health check, alarm 2×60 giây, xác minh, preflight, EC2 stop, SSM, health check Azure và cache DNS. Không coi interval 30 giây là RTO 30 giây. Async replication không đảm bảo dữ liệu cuối đã commit trên AWS đều đến Azure; chờ replay chỉ đảm bảo xử lý WAL **đã nhận**, không xác nhận WAL chưa truyền.

## 6. Lỗi, maintenance và failback

Nếu AWS hồi phục, DNS/TLS probe không đáng tin, Azure offline/replay pause, không Stop được EC2 hoặc SSM trả kết quả lỗi: workflow dừng, giữ latch. Trường hợp command timeout, promotion có thể đã xảy ra trước khi trả kết quả; kiểm tra SSM invocation và role thực tế, không gửi promote mới mù quáng.

Trước maintenance, rejoin hoặc failback, tắt **rule trigger**, không đặt `enable_auto_failover=false` chỉ để pause vì biến đó sẽ gỡ cả control resources:

```powershell
aws events disable-rule --name dcs29-auto-failover --region us-east-1
aws stepfunctions list-executions --state-machine-arn $WorkflowArn --status-filter RUNNING --region us-east-1
$AzureNode = aws ssm get-parameter --name /dcs29/dr/azure-node --query Parameter.Value --output text --region us-east-1
aws ssm list-commands --instance-id $AzureNode --region us-east-1
```

Disable rule không hủy execution/SSM command đã bắt đầu. Không Start AWS primary cũ khi còn execution/command Pending/InProgress/Delayed/Cancelling hoặc chưa xác định role. Giữ AWS bị fence; rejoin/failback bằng mục 9.1 của hướng dẫn triển khai, không dùng initial workload automation để ghi đè PVC active sau rejoin.

Sau toàn bộ command/execution cũ đã terminal và failback đã về AWS primary/Azure standby/streaming, mới rearm. Trong SSH Azure, chỉ xóa incident receipt khi database đã là standby; root file chứa owner ARN để đối chiếu:

```bash
test "$(sudo k3s kubectl -n issues exec postgres-0 -- psql -U postgres -d issues -At -c 'SELECT pg_is_in_recovery()')" = t || exit 1
sudo cat /opt/dcs29/failover-incident.json
sudo rm -f /opt/dcs29/failover-incident.json
```

Không xóa marker khi Azure còn primary. Trên laptop, đọc latch bằng file JSON để tránh lỗi quoting trên Windows PowerShell:

```powershell
$LatchKeyFile = Join-Path $env:TEMP 'dcs29-dr-latch-key.json'
[IO.File]::WriteAllText($LatchKeyFile, '{"id":{"S":"writer"}}', [Text.UTF8Encoding]::new($false))
$Incident = aws dynamodb get-item --table-name dcs29-dr-latch --key "file://$LatchKeyFile" --consistent-read --region us-east-1 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $Incident.Item.owner.S) { throw 'Không đọc được incident owner' }
$Incident.Item
```

Chỉ khi roles/SSM commands đã được xác minh, xóa đúng latch với điều kiện owner không đổi:

```powershell
$OwnerFile = Join-Path $env:TEMP 'dcs29-dr-latch-owner.json'
$OwnerJson = @{ ':owner' = @{ S = $Incident.Item.owner.S } } | ConvertTo-Json -Depth 4 -Compress
[IO.File]::WriteAllText($OwnerFile, $OwnerJson, [Text.UTF8Encoding]::new($false))
aws dynamodb delete-item --table-name dcs29-dr-latch --key "file://$LatchKeyFile" --condition-expression 'owner = :owner' --expression-attribute-values "file://$OwnerFile" --region us-east-1
if ($LASTEXITCODE -ne 0) { throw 'Latch chưa được xóa; giữ rule disabled' }
Remove-Item -LiteralPath $LatchKeyFile, $OwnerFile
```

Kiểm tra CloudWatch alarm về OK trước bật lại rule:

```powershell
aws cloudwatch describe-alarms --alarm-names dcs29-auto-failover --query 'MetricAlarms[].StateValue' --region us-east-1
aws events enable-rule --name dcs29-auto-failover --region us-east-1
```

Terraform có thể sửa drift Enabled/Disabled của rule ở apply sau; không apply trong lúc đang maintenance/rejoin. Latch không có TTL và không tự mở lại, kể cả khi workflow fail; đây là bước xác nhận thủ công để tránh tự động chạy incident mới trên topology sau failover chưa được phục hồi.

## 7. Chi phí và cleanup

Ngoài CloudWatch/EventBridge/Lambda/Step Functions còn có DynamoDB on-demand (vài record/request incident) và SSM hybrid. Run Command trên Azure hiện tính 0,002 USD/invocation; workflow gửi preflight/promote, polling không phải gửi command mới. Logs Lambda giữ 7 ngày. Không có NAT Gateway, Lambda không gắn VPC. Chi phí thực tế còn phụ thuộc region, hạn mức miễn phí, logs và số retry; không đảm bảo tổng bằng 0.

Khi dọn lab: disable rule, bảo đảm không còn execution/SSM command active, sao lưu DB/evidence. Deregister đúng hybrid node và xóa parameter do bootstrap tạo trước khi destroy:

```powershell
aws ssm deregister-managed-instance --instance-id $AzureNode --region us-east-1
aws ssm delete-parameter --name /dcs29/dr/azure-node --region us-east-1
```

SSM identity/agent trong VM và parameter tạo qua bootstrap không tự rollback khi bỏ resource Terraform. Không deregister để retry khi VM vẫn đang phục vụ; phục hồi registration có kế hoạch. Destroy latch/workflow không khôi phục role database hay thay rejoin/failback.

## 8. Kiểm thử và file triển khai

- `deploy/terraform/failover.tf`: control resources/IAM/opt-in và bootstrap.
- `failover/workflow.json.tmpl`: state machine với fencing gate và polling hữu hạn.
- `failover/lambda/handler.py`: các action Lambda, latch, EC2/SSM và HTTPS checks.
- `failover/lambda/azure_command.py`: root promote/receipt/replay/writer gate, không chứa credential.
- `failover/bootstrap*.py`: đăng ký SSM Azure qua SSH, activation không vào state.
- `failover/tests/test_failover.py`: SDK Stubber/offline tests; cài dependencies bằng `failover/tests/requirements.txt` trong venv để chạy.

Đã pass 11 Terraform mock tests và 15 Python offline tests (latch/SDK, fencing gate, receipt, registration cleanup/reuse/account). Kiểm thử Docker với PostgreSQL 17 thật đã xác nhận replication, Stop source, chạy production promote/writer gate, retry cùng incident, giữ issue/history và ghi mới trên Azure. Harness chỉ thay transport K3s/systemctl bằng kết nối PostgreSQL trong container;

Có thể chạy lại từ root repository, trong venv riêng:

```powershell
python -m pip install -r deploy/terraform/failover/tests/requirements.txt
python -m unittest discover -s deploy/terraform/failover/tests -v
docker build -t dcs29-issues:push-check .
python deploy/terraform/failover/tests/runtime_check.py
```

Docker test tạo network/volume/container riêng và dọn đúng tài nguyên đó. Không chạy lên database đang phục vụ. Các kiểm thử local này không xác nhận AWS/Azure thật; cloud IAM, hybrid registration, EventBridge delivery và workflow thực tế vẫn phải nghiệm thu trên hai VM. Không tuyên bố cloud failover đã pass từ kiểm thử local.

Nguồn: [Route 53 metrics/CloudWatch Region](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/monitoring-health-checks.html), [SSM hybrid activation](https://docs.aws.amazon.com/systems-manager/latest/userguide/hybrid-activation-managed-nodes.html), [SSM Linux agent](https://docs.aws.amazon.com/systems-manager/latest/userguide/hybrid-multicloud-ssm-agent-install-linux.html), [SSM pricing](https://aws.amazon.com/systems-manager/pricing/).

[Quay lại nhánh tự động](README.md).
