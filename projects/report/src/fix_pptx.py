# pptxgenjs chèn <a:pPr> thứ hai giữa đoạn khi một đoạn có nhiều run (bullet có nhãn in đậm): bỏ nó đi
import re, sys, zipfile, shutil
src = sys.argv[1]; tmp = src + ".tmp"
bad = '</a:r><a:pPr indent="0" marL="0"><a:buNone/></a:pPr>'
n = 0
with zipfile.ZipFile(src) as zi, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
    for it in zi.infolist():
        if it.filename.endswith("/"):
            continue
        d = zi.read(it.filename)
        if it.filename.startswith("ppt/slides/slide") and it.filename.endswith(".xml"):
            x = d.decode(); n += x.count(bad); d = x.replace(bad, "</a:r>").encode()
        zo.writestr(it, d)
shutil.move(tmp, src); print("removed", n)
