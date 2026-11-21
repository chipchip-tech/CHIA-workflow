#!/usr/bin/env python3
"""run_demo.py - chạy một bước demo qua CHIA và tự mở trang trạng thái (status.html, giao diện tối) trên trình duyệt.

Chạy trên host, trong env chia_env (cần lệnh `chia`), từ bất kỳ thư mục nào:
  python projects/demo-alu/run_demo.py NoCG baseline                 # pha 1
  python projects/demo-alu/run_demo.py CG agent --rounds 2           # pha 2; tham số sau tên bước chuyển cho script
  python projects/demo-alu/run_demo.py show <run_dir>                # xem lại một lần chạy đã xong

Script tự chọn run_id, phục vụ thư mục <WORK_DIR>/demo-alu qua http://127.0.0.1:<port> (chỉ máy này truy cập được), mở
trình duyệt tới trang của lần chạy, rồi `chia job submit` và hiện log như bình thường. Trước khi job ghi trang đầu tiên,
trình duyệt hiện trang chờ (tự làm mới). Job xong thì server vẫn chạy để xem tiếp; Ctrl+C để tắt.
Vì sao không mở file:// trực tiếp: job chạy dưới Ray job server, không gắn với phiên desktop, nên không mở được trình
duyệt; Firefox bản Flatpak cũng có thể không đọc được file ngoài thư mục được phép. Qua http thì không vướng cả hai.
Trình duyệt: --browser "<lệnh>" (URL nối vào cuối); mặc định thử /usr/bin/xdg-open, gio open, Firefox Flatpak
(trên Bazzite, `xdg-open` trong PATH có thể là bản của distrobox, chỉ chạy trong container).
"""
import argparse
import functools
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMOS = ("NoCG", "CG")

WAIT_PAGE = """<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta http-equiv="refresh" content="2">
<title>Đang chờ job CHIA</title><style>:root{{color-scheme:dark}}body{{margin:0;background:#0f1419;color:#e6e9ed;
font:18px/1.5 system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}}div{{text-align:center}}
code{{color:#8b95a1;font-size:14px}}</style></head><body><div><p>Đang chờ job CHIA bắt đầu và ghi trang trạng thái…</p>
<code>{path}</code></div></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")             # trang làm mới phải luôn lấy bản mới
        super().end_headers()

    def send_error(self, code, message=None, explain=None):
        if code == 404 and self.path.split("?")[0].endswith("/status.html"):
            body = WAIT_PAGE.format(path=self.path).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().send_error(code, message, explain)

    def log_message(self, *args):                                  # không chen vào log của job
        pass


def serve(root: Path, port: int) -> tuple[ThreadingHTTPServer, int]:
    handler = functools.partial(Handler, directory=str(root))
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), handler)
        except OSError:
            continue
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, p
    raise SystemExit(f"LỖI: không mở được cổng nào trong {port}..{port + 19}")


def open_browser(url: str, browser: str | None) -> None:
    cands = [shlex.split(browser)] if browser else []
    if Path("/usr/bin/xdg-open").is_file():
        cands.append(["/usr/bin/xdg-open"])
    if shutil.which("gio"):
        cands.append(["gio", "open"])
    if shutil.which("flatpak"):
        cands.append(["flatpak", "run", "org.mozilla.firefox"])
    for c in cands:
        try:
            p = subprocess.Popen(c + [url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            time.sleep(1.5)
            if p.poll() in (None, 0):                              # còn chạy (trình duyệt) hoặc đã giao cho trình duyệt
                print(f"[run_demo] đã mở trình duyệt bằng: {' '.join(c)}", flush=True)
                return
        except OSError:
            continue
    print(f"[run_demo] không tự mở được trình duyệt; mở tay: {url}", flush=True)


def wait_forever(url: str) -> None:
    print(f"[run_demo] trang vẫn xem được tại {url} ; Ctrl+C để tắt server.", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demo", help="NoCG, CG, hoặc show")
    ap.add_argument("what", help="baseline hoặc agent (với show: thư mục lần chạy)")
    ap.add_argument("--work-dir", default=os.environ.get("WORK_DIR", os.path.expanduser("~/chia_demo")))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--browser", help='lệnh mở trình duyệt, ví dụ "firefox" (mặc định tự dò)')
    ap.add_argument("--no-open", action="store_true", help="không mở trình duyệt, chỉ in URL")
    args, extra = ap.parse_known_args()

    root = Path(os.path.realpath(args.work_dir)) / "demo-alu"
    if args.demo == "show":
        run_dir = Path(args.what).expanduser().resolve()
        try:
            rel = run_dir.relative_to(root)
        except ValueError:
            raise SystemExit(f"LỖI: {run_dir} không nằm trong {root}")
        sp = HERE / rel.parts[0] / "status_page.py"
        subprocess.run([sys.executable, str(sp), str(run_dir)], check=True)   # dựng lại trang từ results/*.json
        srv, port = serve(root, args.port)
        url = f"http://127.0.0.1:{port}/{rel.as_posix()}/status.html"
        if not args.no_open:
            open_browser(url, args.browser)
        wait_forever(url)
        return

    if args.demo not in DEMOS or args.what not in ("baseline", "agent"):
        raise SystemExit("dùng: run_demo.py {NoCG|CG} {baseline|agent} [tham số của script] | run_demo.py show <run_dir>")
    if not shutil.which("chia"):
        raise SystemExit("LỖI: không có lệnh `chia`: `conda activate chia_env` trước")
    if "--run-id" in extra:
        rid = extra[extra.index("--run-id") + 1]
    else:
        rid = time.strftime(f"{args.what}-%Y%m%d-%H%M%S")
        extra = ["--run-id", rid] + extra
    if args.work_dir and "--work-dir" not in extra:
        extra = ["--work-dir", os.path.realpath(args.work_dir)] + extra
    root.mkdir(parents=True, exist_ok=True)
    srv, port = serve(root, args.port)
    url = f"http://127.0.0.1:{port}/{args.demo}/{rid}/status.html"
    print(f"[run_demo] trang trạng thái: {url}", flush=True)
    if not args.no_open:
        open_browser(url, args.browser)

    cmd = ["chia", "job", "submit", "--working-dir", ".", "--", "python", f"{args.what}.py", *extra]
    print(f"[run_demo] {' '.join(cmd)}  (trong {HERE / args.demo})", flush=True)
    try:
        rc = subprocess.run(cmd, cwd=HERE / args.demo).returncode
    except KeyboardInterrupt:
        print("\n[run_demo] Ctrl+C: job vẫn chạy trên cluster (dừng bằng `chia job stop <id>`); tắt server.",
              flush=True)
        return
    print(f"[run_demo] job kết thúc (mã {rc}).", flush=True)
    wait_forever(url)


if __name__ == "__main__":
    main()
