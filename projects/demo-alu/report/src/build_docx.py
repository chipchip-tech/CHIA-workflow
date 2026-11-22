# Dựng báo cáo docx từ file mẫu: giữ styles, numbering, footer, chỉ thay phần thân document.xml.
import re, sys, shutil, zipfile, datetime
from xml.sax.saxutils import escape

TPL = sys.argv[1]; OUT = sys.argv[2]
COL = '<w:color w:val="000000" w:themeColor="text1" />'

def runs(text, code_sz=21):
    """**đậm**, `code`, *nghiêng* -> các w:r"""
    out = []
    for tok in re.split(r'(\*\*.+?\*\*|`.+?`|\*.+?\*)', text):
        if not tok:
            continue
        if tok.startswith('**'):
            rpr, t = '<w:b/><w:bCs/>' + COL, tok[2:-2]
        elif tok.startswith('`'):
            rpr, t = (f'<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="Consolas" w:cs="Consolas" />{COL}'
                      f'<w:sz w:val="{code_sz}" /><w:szCs w:val="{code_sz}" />'), tok[1:-1]
        elif tok.startswith('*') and len(tok) > 2:
            rpr, t = '<w:i/><w:iCs/>' + COL, tok[1:-1]
        else:
            rpr, t = COL, tok
        out.append(f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{escape(t)}</w:t></w:r>')
    return ''.join(out)

def h1(t): return f'<w:p><w:pPr><w:pStyle w:val="902" /><w:keepNext w:val="true" /><w:rPr>{COL}</w:rPr></w:pPr>{runs(t)}</w:p>'
def h2(t): return f'<w:p><w:pPr><w:pStyle w:val="903" /><w:keepNext w:val="true" /><w:rPr>{COL}</w:rPr></w:pPr>{runs(t)}</w:p>'
def para(t): return (f'<w:p><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto" /><w:jc w:val="both" />'
                     f'<w:rPr>{COL}</w:rPr></w:pPr>{runs(t)}</w:p>')
def bullet(t): return (f'<w:p><w:pPr><w:pStyle w:val="894" /><w:numPr><w:ilvl w:val="0" /><w:numId w:val="2" /></w:numPr>'
                       f'<w:spacing w:after="60" w:line="290" w:lineRule="auto" /><w:rPr>{COL}</w:rPr></w:pPr>{runs(t)}</w:p>')
def spacer(): return f'<w:p><w:pPr><w:spacing w:after="60" /><w:rPr>{COL}</w:rPr></w:pPr></w:p>'

BRD = ''.join(f'<w:{s} w:val="single" w:color="999999" w:sz="4" w:space="0" />' for s in ('top', 'left', 'bottom', 'right'))
MAR = ('<w:tcMar><w:left w:w="100" w:type="dxa"/><w:top w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/>'
       '<w:bottom w:w="60" w:type="dxa"/></w:tcMar>')

def table(rows, widths, bold_first_col=True):
    assert sum(widths) == 9026, sum(widths)
    tb = ''.join(f'<w:{s} w:val="single" w:color="auto" w:sz="4" w:space="0" />'
                 for s in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))
    x = [f'<w:tbl><w:tblPr><w:tblW w:w="9026" w:type="dxa"/><w:tblBorders>{tb}</w:tblBorders>'
         '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>'
         '</w:tblPr><w:tblGrid>' + ''.join(f'<w:gridCol w:w="{w}"/>' for w in widths) + '</w:tblGrid>']
    for i, r in enumerate(rows):
        x.append('<w:tr><w:trPr><w:tblHeader/></w:trPr>' if i == 0 else '<w:tr><w:trPr><w:cantSplit /></w:trPr>')
        for j, (c, w) in enumerate(zip(r, widths)):
            shd = '<w:shd w:val="clear" w:color="auto" w:fill="d9e2f3" />' if i == 0 else ''
            t = f'**{c}**' if c and (i == 0 or (bold_first_col and j == 0)) and not c.startswith('**') and '`' not in c else c
            x.append(f'<w:tc><w:tcPr>{shd}<w:tcBorders>{BRD}</w:tcBorders>{MAR}<w:tcW w:w="{w}" w:type="dxa"/></w:tcPr>'
                     f'<w:p><w:pPr><w:spacing w:after="0" w:line="260" w:lineRule="auto" /><w:rPr>{COL}</w:rPr></w:pPr>'
                     f'{runs(t, 19)}</w:p></w:tc>')
        x.append('</w:tr>')
    x.append('</w:tbl>')
    return ''.join(x) + spacer()

def figure(text):
    """khung chờ hình: bảng 1 ô viền nét đứt, nền xám"""
    d = ''.join(f'<w:{s} w:val="dashed" w:color="999999" w:sz="6" w:space="0" />' for s in ('top', 'left', 'bottom', 'right'))
    return (f'<w:tbl><w:tblPr><w:tblW w:w="9026" w:type="dxa"/><w:jc w:val="center" /><w:tblBorders>{d}</w:tblBorders></w:tblPr>'
            f'<w:tblGrid><w:gridCol w:w="9026"/></w:tblGrid><w:tr><w:trPr><w:trHeight w:val="2600" /></w:trPr><w:tc><w:tcPr>'
            f'<w:shd w:val="clear" w:color="auto" w:fill="f2f2f2" />{MAR}<w:tcW w:w="9026" w:type="dxa"/><w:vAlign w:val="center" /></w:tcPr>'
            f'<w:p><w:pPr><w:spacing w:after="0" /><w:jc w:val="center" /><w:rPr>{COL}</w:rPr></w:pPr>{runs("*" + text + "*")}</w:p>'
            f'</w:tc></w:tr></w:tbl>' + spacer())

def caption(t): return (f'<w:p><w:pPr><w:spacing w:after="160" /><w:jc w:val="center" /><w:rPr>{COL}</w:rPr></w:pPr>'
                        f'{runs("*" + t + "*")}</w:p>')

B = []
B += [h1('1. Thiết lập CHIA cho demo')]
B += [para('Framework CHIA đã giới thiệu ở báo cáo 16/11. Demo dùng lại cluster một máy chia theo khối; mỗi bước của flow là '
           'một hàm `@ChiaFunction(resources=...)` được Ray đưa tới khối có công cụ tương ứng. Thư mục `WORK_DIR` '
           '(`~/chia_demo`) mount cùng đường dẫn vào mọi khối nên các bước đọc kết quả của nhau trực tiếp. Profiler của CHIA '
           'ghi timeline mọi task; trang `status.html` (tự làm mới) cho thấy đang ở bước nào và kết quả các bước trước.')]
B += [table([
    ['Task', 'Khối', 'Công cụ'],
    ['`sim_build`', 'chipyard', 'Verilator 5.022 (của Chipyard) build testbench'],
    ['`sim_run`', 'verilator', 'chạy binary mô phỏng, ghi VCD'],
    ['`fpga_sim`, `fpga_build`', 'vivado', 'xsim + UNISIM; Vivado 2025.1 synth → P&R → bitstream'],
    ['`asic_stage` × 6', 'asic', 'ORFS: synth, floorplan, place, cts, route, finish (GDS + ảnh layout)'],
    ['`agent_session`', 'claude', 'Claude Code đọc báo cáo, sửa file trong thư mục vòng (không build được)'],
], [2300, 1300, 5426])]

B += [h1('2. Demo flow Sim → FPGA → ASIC với vòng lặp agent')]
B += [h2('2.1. Mục tiêu')]
B += [bullet('**Câu hỏi:** CHIA có chạy được trọn một flow thiết kế vi mạch số mã nguồn mở cho cả FPGA và ASIC, với agent '
             'Claude đánh giá và tối ưu từng bước, không?'),
      bullet('**Thiết kế nhỏ có chủ đích:** ALU 8 bit để mỗi bước chạy trong vài phút và nhánh ASIC đi tới GDS.'),
      bullet('**Hai demo độc lập:** NoCG (thanh ghi dùng clock enable thường) và CG (clock của thanh ghi vào/ra qua cell '
             'clock gating), cùng một flow.'),
      bullet('**Ba pha:** (1) baseline, không agent; (2) agent, tối đa 2 vòng mỗi bước; (3) bảng so sánh baseline / agent.')]
B += [h2('2.2. Thiết lập thực nghiệm')]
B += [table([
    ['Hạng mục', 'Lựa chọn'],
    ['Thiết kế', 'ALU 8 bit, 8 phép (ADD, SUB, AND, OR, XOR, SHL, SHR, SLT), pipeline 2 tầng, cờ `zero`/`carry`/`valid`'],
    ['Biến thể CG', 'module `icg`: mô phỏng bằng latch + AND, ASIC bằng `sky130_fd_sc_hd__dlclkp`'],
    ['Sim', 'Verilator 5.022, testbench tự kiểm so với mô hình tham chiếu'],
    ['FPGA', 'Vivado 2025.1, `xc7a100tcsg324-1` (Arty A7-100T), 100 MHz; vỏ `fpga_top` tự sinh kích thích (LFSR) và tự kiểm (MISR)'],
    ['ASIC', 'Yosys 0.68 + OpenROAD (ORFS), sky130hd, clock 10 ns, RTL → GDS'],
    ['Agent', 'Claude Code (Opus) trên khối `claude`; chỉ sửa bản chép trong thư mục vòng'],
], [2000, 7026])]
B += [para('Baseline dùng thiết lập gần mặc định (`CORE_UTILIZATION` 40, `PLACE_DENSITY` 0,65 như ví dụ `gcd` của ORFS; '
           'directive mặc định của Vivado). Chạy baseline hai lần ra **đúng từng số** ở cả ba bước: công cụ tất định, '
           'nên ngưỡng so sánh ở pha 2 chỉ để bỏ sai khác làm tròn.')]
B += [h2('2.3. Vòng lặp agent')]
B += [para('Với mỗi bước Sim → FPGA → ASIC, tối đa 2 vòng. Mỗi vòng gồm:')]
B += [bullet('**Chép:** bản tốt nhất hiện tại sang thư mục riêng của vòng.'),
      bullet('**Agent:** đọc báo cáo của bản tốt nhất và lịch sử các vòng trước, sửa các file được phép. Khối `claude` không có '
             'Verilator, Vivado hay ORFS nên agent không tự build.'),
      bullet('**Kiểm phạm vi:** CHIA hoàn lại mọi sửa ngoài phạm vi và kiểm luật (không đổi part, clock, design, file nguồn, SDC).'),
      bullet('**Đo lại** bước đó trên công cụ thật, qua gate, rồi so với bản tốt nhất. Vòng được nhận thành nền cho vòng sau '
             'và bước sau; vòng bị loại thì lý do được đưa vào lời nhắc của vòng kế.')]
B += [table([
    ['Bước', 'Agent được sửa', 'Gate', 'Luật nhận'],
    ['Sim', '`sim/tb_<DUT>.v`', '`RESULT: PASS`', 'số lần kiểm tra (`checked`) tăng'],
    ['FPGA', '`build.tcl`, `sources.f`, file `.v` mới trong `fpga/`', '`tb_fpga_top` PASS (xsim), có `.bit`, WNS ≥ 0',
     'Pareto chặt: không kém ở WNS, công suất động, LUT+FF; tốt hơn ở ít nhất một'],
    ['ASIC', '`asic/config.mk`', 'có GDS, setup/hold slack ≥ 0, DRC = 0',
     'không kém quá 1 % ở diện tích cell, diện tích die, công suất; tốt hơn quá 1 % ở ít nhất một'],
], [900, 2400, 2526, 3200])]
B += [para('`rtl/`, `.xdc` và `.sdc` cố định với agent. Tối ưu riêng cho FPGA phải nằm trong file mới ở `fpga/`, nên không '
           'lan sang nhánh ASIC.')]

B += [h2('2.4. Kết quả baseline')]
B += [table([
    ['Bước', 'Chỉ số', 'NoCG', 'CG'],
    ['Sim', 'kết quả, số lần kiểm tra', 'PASS, 6 272', 'PASS, 6 272'],
    ['FPGA', 'LUT / FF', '138 / 139', '140 / 141'],
    ['', 'WNS / WHS (ns)', '4,579 / 0,024', '1,502 / 0,052'],
    ['', 'công suất tổng / động (W)', '0,099 / 0,002', '0,102 / 0,005'],
    ['ASIC', 'diện tích cell (µm²), util', '3 078, 44,8 %', '2 975, 46,9 %'],
    ['', 'diện tích die (µm²)', '7 534', '6 926'],
    ['', 'số cell (flop, ICG, buffer clock)', '356 (31, 0, 9)', '306 (31, 2, 18)'],
    ['', 'setup / hold WS (ns)', '6,815 / 0,493', '6,957 / 0,406'],
    ['', 'công suất (mW), DRC', '0,368, 0', '0,324, 0'],
], [900, 3326, 2400, 2400])]
B += [para('Clock gating cho kết quả **ngược nhau** trên hai nền tảng. Trên **FPGA**, CG kém hơn: clock đi qua latch + LUT nên '
           'đường găng chỉ còn nửa chu kỳ (WNS giảm 3 ns), công suất động tăng, Vivado báo cảnh báo gated clock. Trên **ASIC**, '
           'CG tốt hơn: bỏ được mux hồi tiếp nên ít hơn 50 cell, diện tích cell −3 %, công suất −12 %, đổi lại số buffer clock '
           'gấp đôi; CTS của OpenROAD xử lý đúng cell ICG. Đây là lý do tối ưu cho FPGA không được mang sang ASIC. Thời gian: '
           'FPGA khoảng 64 s, ASIC khoảng 2,5 phút mỗi demo.')]
B += [figure('[Chèn hình: timeline CHIA của một lần chạy baseline, profile/ChiaProfileCollector_profile.html]')]
B += [caption('Hình 1. Timeline CHIA: các task Sim, FPGA, ASIC trên từng khối của cluster')]

B += [h2('2.5. Kết quả agent so với baseline')]
B += [para('Cả hai demo chạy `agent.py --rounds 2` (NoCG `agent-20261121-141430`, CG `agent-20261121-143619`) và kết thúc '
           '`KẾT QUẢ: OK`; các vòng được nhận là `sim-r1`, `sim-r2`, `asic-r1`, `asic-r2`. Mỗi demo khoảng 22 phút.')]
B += [table([
    ['Chỉ số', 'NoCG: baseline → agent', 'CG: baseline → agent'],
    ['Sim: số lần kiểm tra (vẫn PASS)', '6 272 → 542 046 (×86)', '6 272 → 1 509 090 (×241)'],
    ['FPGA', '2 vòng đều loại, giữ baseline', '2 vòng đều loại, giữ baseline'],
    ['ASIC: diện tích cell (µm²)', '3 077,95 → 3 021,65 (−1,8 %)', '2 975,35 → 2 835,22 (−4,7 %)'],
    ['ASIC: diện tích die (µm²)', '7 534,24 → 4 641,70 (**−38,4 %**)', '6 925,57 → 5 504,90 (**−20,5 %**)'],
    ['ASIC: công suất (mW)', '0,368 → 0,361 (−1,8 %)', '0,324 → 0,302 (**−6,8 %**)'],
    ['ASIC: setup WS (ns), DRC', '6,815 → 7,034, 0', '6,957 → 7,042, 0'],
], [3026, 3000, 3000])]
B += [para('**Sim.** Vòng 1 agent bổ sung các trường hợp testbench gốc chưa kiểm: độ trễ `valid` đúng 2 chu kỳ, giá trị biên '
           '(7F/80, FF/00), phép dịch với `b[7:3] ≠ 0`, các mẫu `en`, reset giữa chừng. Vòng 2 vét cạn ADD/SUB/SLT trên mọi '
           'cặp 256 × 256. Riêng CG, agent tự thêm phép đếm cạnh của clock sau ICG mỗi chu kỳ để kiểm latch không tạo xung giả.')]
B += [para('**FPGA.** Bốn đề xuất, không đề xuất nào tốt hơn ở mọi chỉ số:')]
B += [table([
    ['Vòng', 'Agent làm gì', 'Đo thật'],
    ['NoCG r1', 'Retiming, ánh xạ tay LUT6_2/CARRY4, directive `Explore`', 'WNS 4,579 → 4,94 ns; công suất động 2 → 5 mW; LUT+FF 277 → 302'],
    ['NoCG r2', 'Giữ thanh ghi vào, ADD/SUB/SLT dùng chung một bộ cộng', 'LUT+FF 277 → 262; WNS 4,579 → 4,212 ns'],
    ['CG r1', 'ICG thành cổng AND + `-gated_clock_conversion auto` (gating → CE của FF)', 'WNS 1,502 → 4,499 ns; công suất động 5 → 2 mW; LUT+FF 281 → 284'],
    ['CG r2', 'ALU riêng cho FPGA dùng CE, không latch trên clock, + retiming', 'WNS 1,502 → 4,436 ns; LUT+FF 281 → 312'],
], [1100, 4000, 3926])]
B += [para('Agent tự kiểm trước khi nộp bằng mô hình Python (vét cạn 524 288 tổ hợp, 0 lỗi) nhưng các dự đoán về tài nguyên '
           'vẫn sai: chỉ đo thật mới biết. CG r1 là đánh đổi đáng chú ý nhất: agent xác định và sửa đúng vấn đề của clock '
           'gating trên FPGA (WNS gấp 3, công suất động −60 %) với giá +3 ô LUT+FF. Theo luật Pareto chặt chốt từ trước, vòng '
           'này bị loại; luật được **giữ nguyên**, không nới ra sau khi thấy kết quả.')]
B += [para('**ASIC.** Agent chỉnh `config.mk`: NoCG r1 `ABC_AREA=1`, `CORE_UTILIZATION` 40 → 55, `PLACE_DENSITY` 0,65 → 0,75 '
           '(die −27,5 %), r2 tăng tiếp lên 65 / 0,85; CG r1 chỉ `ABC_AREA=1` vì route của CG từng phải lặp 6 vòng mới sạch DRC '
           '(công suất −7,1 %), r2 `CORE_UTILIZATION` 50 và `DONT_BUFFER_PORTS=1`. Mọi vòng qua gate; số cell tăng 26–31 % do '
           'ABC theo diện tích dùng nhiều cell nhỏ hơn. Agent cũng chỉ ra giới hạn của bước: ở NoCG, flip-flop và cây clock '
           'chiếm khoảng 70 % công suất, phần này do RTL quyết định.')]
B += [figure('[Chèn hình: layout GDS NoCG và CG, baseline (trái) và agent (phải), asic/layout.png của mỗi lần chạy]')]
B += [caption('Hình 2. Layout ASIC trước và sau agent')]

B += [h2('2.6. Kết luận')]
B += [bullet('**Flow đúng và đủ:** CHIA điều phối Sim (Verilator) → FPGA (Vivado, ra bitstream) → ASIC (Yosys + OpenROAD, ra '
             'GDS) trên năm khối; mỗi bước có gate.'),
      bullet('**Agent tham gia được ở cả ba bước:** số lần kiểm tra Sim tăng 86–241 lần, die ASIC giảm 20–38 %, công suất CG '
             'giảm 6,8 %; ở FPGA agent tìm đúng vấn đề clock gating.'),
      bullet('**Đo trên công cụ thật là bắt buộc:** vòng "đề xuất → đo → nhận/loại" giữ kết quả cuối không bao giờ kém baseline.'),
      bullet('**FPGA và ASIC tách biệt:** clock gating có lợi trên ASIC, có hại trên FPGA; `rtl/` cố định nên sửa riêng cho FPGA '
             'không ảnh hưởng ASIC.')]
B += [h2('2.7. Hạn chế')]
B += [bullet('**Thiết kế rất nhỏ:** khoảng 300 cell, RTL cố định, nên ít chỗ để tối ưu.'),
      bullet('**Thước đo Sim chỉ đếm số phép so sánh** (`checked`), không đo độ phủ; chưa có mô phỏng mức cổng hay LVS sau khi agent đổi cấu hình ASIC.'),
      bullet('**Công suất là ước lượng:** OpenSTA với hoạt động mặc định; Vivado vector-less.'),
      bullet('**Mỗi vòng chỉ một mẫu agent;** chưa nạp board (FPGA dừng ở bitstream).')]

body = ''.join(B)
z = zipfile.ZipFile(TPL)
doc = z.read('word/document.xml').decode()
i = doc.index('<w:body>') + len('<w:body>'); j = doc.index('<w:sectPr>')
doc = doc[:i] + body + doc[j:]
now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as zo:
    for it in z.infolist():
        if it.filename.endswith('/'):
            continue
        d = z.read(it.filename)
        if it.filename == 'word/document.xml':
            d = doc.encode()
        elif it.filename == 'docProps/core.xml':
            x = d.decode().replace('<dc:title>Draft</dc:title>', '<dc:title>Báo cáo demo ALU</dc:title>')
            d = re.sub(r'(<dcterms:modified[^>]*>)[^<]*', r'\g<1>' + now, x).encode()
        zo.writestr(it, d)
print('wrote', OUT)
