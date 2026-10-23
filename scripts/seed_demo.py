"""Load a few sample issues and comments through the HTTP API so a demo starts with content.

    python scripts/seed_demo.py --url http://127.0.0.1:8090

Request ids are derived from fixed names, so running it again does not create duplicates.
"""
import argparse
from uuid import NAMESPACE_URL, uuid5

import httpx

ISSUES = [
    ("Không đăng nhập được trên điện thoại", "Form đăng nhập báo lỗi 500 sau khi nhập đúng mật khẩu.", "Minh",
     ["Mình tái hiện được trên Android 14.", "Log cho thấy lỗi ở bước tạo session."]),
    ("Trang danh sách tải chậm", "Mở danh sách mất hơn 5 giây khi có nhiều issue.", "Lan", ["Có thể thiếu index theo thời gian cập nhật."]),
    ("Thêm bộ lọc theo trạng thái", "Cần lọc nhanh Open/Closed.", "Hùng", []),
    ("Sai múi giờ ở lịch sử hoạt động", "Giờ hiển thị lệch 7 tiếng so với giờ máy.", "Vy", ["Server lưu UTC, giao diện chưa đổi sang giờ địa phương."]),
    ("Gợi ý nội dung khi tạo issue", "Hiển thị mẫu mô tả để người dùng viết đủ thông tin.", "An", []),
]


def request_id(name):
    return str(uuid5(NAMESPACE_URL, "dcs29-seed/" + name))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8090")
    base = parser.parse_args().url.rstrip("/")
    with httpx.Client(timeout=10, trust_env=False) as client:
        for title, description, author, comments in ISSUES:
            body = {"request_id": request_id(title), "title": title, "description": description,
                    "author": author, "status": "open", "version": 0}
            response = client.post(base + "/api/issues", json=body)
            response.raise_for_status()
            issue_id = response.json()["id"]
            for index, text in enumerate(comments):
                comment = {"request_id": request_id(f"{title}/{index}"), "author": "Demo", "body": text}
                client.post(f"{base}/api/issues/{issue_id}/comments", json=comment).raise_for_status()
            print(f"OK  {title}")


if __name__ == "__main__":
    main()
