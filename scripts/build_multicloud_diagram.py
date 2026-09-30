"""Build the editable, standalone DCS29 architecture SVG from source icons."""

from __future__ import annotations

import base64
import html
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "images" / "icons"
OUT = ROOT / "images" / "kien-truc-multi-cloud.svg"
parts: list[str] = []


def add(markup: str) -> None:
    parts.append(markup)


def text(x: int, y: int, value: str, cls: str = "body", **attrs: str) -> None:
    extra = " ".join(f'{k.replace("_", "-")}="{html.escape(str(v))}"' for k, v in attrs.items())
    add(f'<text x="{x}" y="{y}" class="{cls}" {extra}>{html.escape(value)}</text>')


def rect(x: int, y: int, w: int, h: int, cls: str = "card", **attrs: str) -> None:
    extra = " ".join(f'{k.replace("_", "-")}="{html.escape(str(v))}"' for k, v in attrs.items())
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" class="{cls}" {extra}/>')


def path(d: str, cls: str = "flow", **attrs: str) -> None:
    extra = " ".join(f'{k.replace("_", "-")}="{html.escape(str(v))}"' for k, v in attrs.items())
    add(f'<path d="{d}" class="{cls}" {extra}/>')


def icon(name: str, x: int, y: int, size: int = 54) -> None:
    svg = (ICONS / f"{name}.svg").read_bytes()
    uri = base64.b64encode(svg).decode("ascii")
    add(f'<image x="{x}" y="{y}" width="{size}" height="{size}" href="data:image/svg+xml;base64,{uri}"/>')


def service(x: int, y: int, w: int, title: str, subtitle: str, name: str, tone: str) -> None:
    rect(x, y, w, 88, "service", fill=tone)
    icon(name, x + 18, y + 17, 52)
    text(x + 83, y + 39, title, "service-title")
    text(x + 83, y + 63, subtitle, "small")


def step(x: int, number: str, title: str, detail: str, fill: str) -> None:
    rect(x, 1610, 420, 105, "step", fill=fill)
    add(f'<circle cx="{x + 42}" cy="1648" r="22" fill="#17253c"/>')
    text(x + 42, 1655, number, "step-number", text_anchor="middle")
    text(x + 78, 1647, title, "step-title")
    text(x + 78, 1676, detail, "small")


add('''<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="1770" viewBox="0 0 2400 1770" role="img" aria-labelledby="title desc">
<title id="title">DCS29 Multi-cloud failover: AWS EC2 và Azure VM</title>
<desc id="desc">Sơ đồ kiến trúc mục tiêu. GitHub Actions build image rồi push GHCR. AWS EC2 và Azure VM chạy cùng digest qua Docker Compose, NGINX và FastAPI. Route 53 DNS failover dựa trên hai health check. Prometheus, Blackbox Exporter, Grafana và k6 chạy ngoài hai cloud.</desc>
<defs>
  <marker id="arr" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto"><path d="M1 1 L10 5.5 L1 10" fill="none" stroke="#34445b" stroke-width="1.8"/></marker>
  <marker id="arr-blue" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto"><path d="M1 1 L10 5.5 L1 10" fill="none" stroke="#1769aa" stroke-width="1.8"/></marker>
  <marker id="arr-green" markerWidth="11" markerHeight="11" refX="9" refY="5.5" orient="auto"><path d="M1 1 L10 5.5 L1 10" fill="none" stroke="#16845c" stroke-width="1.8"/></marker>
  <style>
    *{font-family:Arial,'Segoe UI',sans-serif}.bg{fill:#fbfcff}.title{font-size:42px;font-weight:800;fill:#17253c}.subtitle{font-size:21px;fill:#53637a}.section{font-size:22px;font-weight:800;fill:#17253c}.frame-label{font-size:27px;font-weight:800;fill:#17253c}.body{font-size:19px;fill:#26364d}.small{font-size:16px;fill:#596a7c}.tiny{font-size:14px;fill:#687789}.service-title{font-size:18px;font-weight:700;fill:#17253c}.step-title{font-size:20px;font-weight:750;fill:#17253c}.step-number{font-size:19px;font-weight:800;fill:#fff}.card{fill:#fff;stroke:#cfdae4;stroke-width:2;rx:14}.service{stroke:#cad5df;stroke-width:1.8;rx:13}.step{stroke:#d2dce5;stroke-width:1.8;rx:15}.aws-frame{fill:#fffdf9;stroke:#f0a13a;stroke-width:3;rx:26}.azure-frame{fill:#fafdff;stroke:#2389ce;stroke-width:3;rx:26}.nested{fill:#fff;stroke:#c6d2de;stroke-width:2;stroke-dasharray:9 7;rx:17}.flow{fill:none;stroke:#34445b;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;marker-end:url(#arr)}.dns{fill:none;stroke:#1769aa;stroke-width:3.2;stroke-linecap:round;stroke-linejoin:round;marker-end:url(#arr-blue)}.fail{fill:none;stroke:#16845c;stroke-width:3;stroke-dasharray:11 8;stroke-linecap:round;stroke-linejoin:round;marker-end:url(#arr-green)}.build{fill:none;stroke:#805ac4;stroke-width:2.6;stroke-dasharray:8 7;stroke-linecap:round;marker-end:url(#arr)}.probe{fill:none;stroke:#ec7b40;stroke-width:2.5;stroke-dasharray:3 8;stroke-linecap:round;marker-end:url(#arr)}
  </style>
</defs>
<rect class="bg" width="2400" height="1770"/>
<rect x="45" y="38" width="2310" height="100" rx="18" fill="#fff" stroke="#dce5ed" stroke-width="2"/>
''')
text(75, 91, "MULTI-CLOUD FAILOVER", "title")
text(75, 122, "AWS EC2 primary  ×  Azure VM standby   |   DCS29 · Kiến trúc mục tiêu cho demo", "subtitle")
rect(1994, 68, 327, 44, "card", fill="#fff7df", stroke="#efd08b")
text(2018, 96, "AZURE: VM, KHÔNG CONTAINER APPS", "small")

# Build and deploy row.
text(70, 184, "01  BUILD & TRIỂN KHAI MỘT IMAGE", "section")
service(70, 207, 420, "Source code", "FastAPI + dữ liệu JSON", "fastapi", "#eef9f5")
service(610, 207, 420, "GitHub Actions", "test → build linux/amd64", "githubactions", "#eef3ff")
service(1150, 207, 420, "GHCR", "private image @ sha256 digest", "github", "#f1f3f6")
rect(1680, 207, 645, 88, "service", fill="#f6f0ff")
icon("docker", 1697, 222, 54)
text(1775, 247, "Cùng một image cho cả 2 VM", "service-title")
text(1775, 274, "Chỉ APP_ENV và APP_REGION khác nhau", "small")
path("M490 251 H596")
path("M1030 251 H1136")
path("M1570 251 H1665", "build")

# User + DNS routing; Route 53 is not in the HTTP data path.
text(70, 354, "02  TRA CỨU DNS → KẾT NỐI HTTPS TRỰC TIẾP", "section")
rect(770, 378, 305, 102, "service", fill="#f7f9fc")
add('<circle cx="810" cy="414" r="14" fill="none" stroke="#33465c" stroke-width="3"/><path d="M783 459c0-20 13-31 27-31s27 11 27 31" fill="none" stroke="#33465c" stroke-width="3"/>')
text(857, 420, "Người dùng", "service-title")
text(857, 450, "app.example.com", "small")
rect(1120, 378, 425, 102, "service", fill="#f0e9ff")
icon("aws-route53", 1139, 397, 62)
text(1220, 419, "Amazon Route 53", "service-title")
text(1220, 450, "DNS failover + health checks", "small")
path("M1075 428 H1106")
text(1083, 396, "DNS query", "tiny")
path("M1230 480 V536 H592 V578", "dns")
path("M1435 480 V536 H1795 V578", "fail")
text(620, 526, "PRIMARY · trả AWS khi healthy", "small")
text(1763, 526, "SECONDARY · khi AWS unhealthy", "small")
text(980, 565, "Route 53 chỉ trả DNS; HTTPS đi thẳng tới Public IP của origin", "tiny")

# AWS boundary.
rect(60, 590, 1090, 624, "aws-frame")
icon("aws-vpc", 88, 615, 61)
text(166, 650, "Amazon VPC · ap-southeast-1", "frame-label")
text(164, 680, "AWS PRIMARY · EC2 Ubuntu", "small")
rect(91, 705, 1028, 473, "nested")
text(114, 737, "VPC / public subnet", "service-title")
text(850, 737, "SG: 443 public · 22 hạn chế", "small")
service(112, 757, 290, "Internet Gateway", "đường ra Internet", "aws-internet-gateway", "#fff7e9")
service(422, 757, 295, "Elastic IP", "địa chỉ origin ổn định", "aws-elastic-ip", "#fff7e9")
rect(738, 757, 349, 88, "service", fill="#fff7e9")
add('<path d="M770 778l25 10v18c0 17-9 29-25 36-16-7-25-19-25-36v-18z" fill="#f4a83e" stroke="#ad6a08" stroke-width="2"/>')
text(817, 797, "Security Group", "service-title")
text(817, 826, "443 → NGINX; 8080 đóng", "small")
path("M402 801 H410")
path("M717 801 H726")
rect(111, 872, 976, 263, "card", fill="#ffffff")
icon("aws-ec2", 135, 888, 60)
text(211, 915, "Amazon EC2", "service-title")
text(211, 945, "Ubuntu · Docker Compose", "small")
icon("ubuntu", 977, 885, 48)
rect(139, 973, 260, 96, "service", fill="#eef8f1")
icon("docker", 157, 992, 50)
text(220, 1011, "Docker Compose", "service-title")
text(220, 1042, "restart: unless-stopped", "small")
rect(425, 973, 245, 96, "service", fill="#eef8f1")
icon("nginx", 444, 992, 49)
text(508, 1011, "NGINX", "service-title")
text(508, 1042, "HTTPS :443", "small")
rect(695, 973, 256, 96, "service", fill="#eef8f1")
icon("fastapi", 714, 992, 49)
text(777, 1011, "FastAPI", "service-title")
text(777, 1042, "app :8080 (nội bộ)", "small")
path("M670 1021 H682")
icon("aws-ebs-gp3", 145, 1081, 43)
text(201, 1111, "EBS gp3 · OS / Compose / logs", "small")
text(742, 1111, "APP_ENV=aws-primary", "small")

# Azure boundary.
rect(1250, 590, 1090, 624, "azure-frame")
icon("azure-resource-group", 1278, 615, 61)
text(1356, 650, "Resource Group · dcs29-rg", "frame-label")
text(1356, 680, "MICROSOFT AZURE · East Asia · STANDBY", "small")
rect(1281, 705, 1028, 473, "nested")
icon("azure-vnet", 1300, 713, 37)
text(1351, 737, "Virtual Network / subnet", "service-title")
text(2042, 737, "NSG: 443 public · 22 hạn chế", "small")
service(1302, 757, 329, "Static Public IP", "địa chỉ origin ổn định", "azure-public-ip", "#edf7ff")
service(1652, 757, 294, "Azure NSG", "443 → NGINX", "azure-nsg", "#edf7ff")
rect(1967, 757, 310, 88, "service", fill="#edf7ff")
icon("azure-nic", 1984, 774, 49)
text(2046, 797, "Network Interface", "service-title")
text(2046, 826, "NIC trong VNet / subnet", "small")
path("M1631 801 H1639")
path("M1946 801 H1954")
rect(1301, 872, 976, 263, "card", fill="#ffffff")
icon("azure-vm", 1325, 888, 60)
text(1401, 915, "Azure Virtual Machine", "service-title")
text(1401, 945, "Ubuntu · Docker Compose", "small")
icon("ubuntu", 2167, 885, 48)
rect(1329, 973, 260, 96, "service", fill="#eef8ff")
icon("docker", 1347, 992, 50)
text(1410, 1011, "Docker Compose", "service-title")
text(1410, 1042, "restart: unless-stopped", "small")
rect(1615, 973, 245, 96, "service", fill="#eef8ff")
icon("nginx", 1634, 992, 49)
text(1698, 1011, "NGINX", "service-title")
text(1698, 1042, "HTTPS :443", "small")
rect(1885, 973, 256, 96, "service", fill="#eef8ff")
icon("fastapi", 1904, 992, 49)
text(1967, 1011, "FastAPI", "service-title")
text(1967, 1042, "app :8080 (nội bộ)", "small")
path("M1860 1021 H1872")
icon("azure-disks", 1335, 1081, 43)
text(1391, 1111, "Managed OS Disk · Compose / logs", "small")
text(1932, 1111, "APP_ENV=azure-standby", "small")

# External observability boundary.
text(70, 1260, "03  QUAN SÁT & THỬ NGHIỆM · NGOÀI HAI CLOUD", "section")
rect(60, 1280, 2280, 240, "card", fill="#f7faf8")
text(90, 1310, "Laptop nhóm hoặc máy giám sát thứ ba · vẫn chạy khi EC2 dừng", "small")
rect(92, 1340, 403, 127, "service", fill="#fff3ec")
add('<circle cx="138" cy="1384" r="23" fill="none" stroke="#e27134" stroke-width="4"/><circle cx="138" cy="1384" r="7" fill="#e27134"/><path d="M138 1351v10M171 1384h-10M138 1417v-10M105 1384h10" stroke="#e27134" stroke-width="4"/>')
text(185, 1380, "Blackbox Exporter", "service-title")
text(185, 1409, "probe 3 URL: AWS, Azure, shared", "small")
text(185, 1434, "HTTPS /health/ready", "small")
rect(529, 1340, 394, 127, "service", fill="#fff7ed")
icon("prometheus", 555, 1365, 62)
text(636, 1380, "Prometheus", "service-title")
text(636, 1409, "scrape /probe và /metrics", "small")
text(636, 1434, "availability, latency, error", "small")
rect(957, 1340, 325, 127, "service", fill="#fff7ed")
icon("grafana", 984, 1365, 62)
text(1067, 1380, "Grafana", "service-title")
text(1067, 1409, "dashboard & timeline", "small")
rect(1316, 1340, 310, 127, "service", fill="#f3edff")
icon("k6", 1343, 1365, 62)
text(1425, 1380, "Grafana k6", "service-title")
text(1425, 1409, "request shared domain", "small")
text(1425, 1434, "đo lỗi / downtime", "small")
rect(1660, 1340, 645, 127, "service", fill="#fff6e9")
icon("aws-cloudwatch", 1687, 1365, 62)
text(1769, 1380, "Amazon CloudWatch", "service-title")
text(1769, 1409, "Route 53 health-check status", "small")
text(1769, 1434, "đối chiếu mốc chuyển DNS", "small")
path("M495 1403 H516")
path("M923 1403 H944")
text(100, 1493, "Blackbox probe 2 origin và shared URL; Prometheus lưu chuỗi thời gian; Grafana hiển thị; k6 sinh tải và ghi kết quả.", "small")

# Workflow strip.
text(70, 1577, "WORKFLOW DEMO · 5 BƯỚC THEO THỨ TỰ", "section")
step(70, "1", "Build", "Actions push digest lên GHCR", "#f0f3ff")
step(530, "2", "Chạy 2 origin", "AWS + Azure pull cùng digest", "#edf7ff")
step(990, "3", "Kiểm tra", "Route 53 probe /health/ready", "#f1edff")
step(1450, "4", "Gây lỗi AWS", "Stop EC2; DNS trả Azure", "#eaf8f1")
step(1910, "5", "Đo & phục hồi", "k6 + Grafana; start EC2", "#fff5eb")
for x in (490, 950, 1410, 1870):
    path(f"M{x} 1662 H{x + 30}")
text(70, 1750, "Đường liền: kết nối / luồng dữ liệu   ·   Nét xanh đứt: nhánh failover khi AWS unhealthy   ·   Mỗi VM pull cùng GHCR digest lúc triển khai", "tiny")
add("</svg>")
OUT.write_text("\n".join(parts), encoding="utf-8")
print(OUT)
