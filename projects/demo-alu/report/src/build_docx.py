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

def h3(t): return f'<w:p><w:pPr><w:pStyle w:val="904" /><w:keepNext w:val="true" /><w:rPr>{COL}</w:rPr></w:pPr>{runs(t)}</w:p>'

B = []
B += [h1('1. Thiết lập CHIA cho demo')]
B += [para('CHIA đã được giới thiệu ở báo cáo 16/11, nên phần này chỉ nói demo dùng CHIA như thế nào. Demo chạy trên một máy. '
           'Cluster được chia thành năm khối, mỗi khối là một container có sẵn một bộ công cụ. Mỗi bước của flow là một hàm '
           'Python gắn `@ChiaFunction(resources=...)`: hàm khai báo cần tài nguyên gì, và Ray gửi nó tới khối có tài nguyên đó.')]
B += [para('Mọi khối mount chung thư mục `~/chia_demo` ở cùng một đường dẫn, nên bước sau đọc thẳng file kết quả của bước trước. '
           'CHIA ghi thời điểm bắt đầu và kết thúc của từng task thành một timeline (Hình 1). Trang `status.html` tự làm mới '
           'và cho biết flow đang chạy tới bước nào, cùng kết quả của các bước đã xong.')]
B += [table([
    ['Task', 'Chạy ở khối', 'Làm gì'],
    ['`sim_build`', 'chipyard', 'Build testbench bằng Verilator 5.022 có sẵn trong Chipyard'],
    ['`sim_run`', 'verilator', 'Chạy file mô phỏng đã build, ghi dạng sóng (VCD)'],
    ['`fpga_sim`, `fpga_build`', 'vivado', 'Mô phỏng vỏ FPGA bằng xsim, rồi tổng hợp, đặt dây và tạo bitstream bằng Vivado 2025.1'],
    ['`asic_stage` (6 lần)', 'asic', 'Sáu chặng của ORFS: synth, floorplan, place, cts, route, finish. Kết quả cuối là file GDS và ảnh layout'],
    ['`agent_session`', 'claude', 'Claude Code đọc báo cáo và sửa file trong thư mục của vòng. Khối này không có công cụ build'],
], [2300, 1300, 5426])]

B += [h1('2. Demo flow Sim → FPGA → ASIC với vòng lặp agent')]
B += [h2('2.1. Mục tiêu')]
B += [para('Demo kiểm tra xem CHIA có điều phối được trọn một flow thiết kế vi mạch số bằng công cụ mã nguồn mở hay không: '
           'mô phỏng, rồi lên FPGA và ASIC, trong khi một agent Claude đọc kết quả và tối ưu từng bước.')]
B += [para('Thiết kế là một ALU 8 bit. Thiết kế được chọn nhỏ để mỗi bước chỉ mất vài phút và nhánh ASIC chạy được tới GDS, '
           'tức file layout cuối cùng để gửi đi sản xuất. Hai biến thể của ALU chạy cùng một flow:')]
B += [bullet('NoCG: thanh ghi giữ giá trị bằng tín hiệu enable thông thường. Khi không ghi, một mux đưa giá trị cũ quay lại thanh ghi.'),
      bullet('CG: clock của thanh ghi vào và ra đi qua một cell clock gating (ICG). Khi không cần ghi, clock bị chặn nên thanh ghi không đổi.')]
B += [para('Demo gồm ba pha. Pha 1 chạy flow không có agent để lấy kết quả gốc (baseline). Pha 2 cho agent tối ưu, mỗi bước '
           'tối đa 2 vòng. Pha 3 đặt kết quả của agent cạnh baseline để so sánh.')]

B += [h2('2.2. Thiết lập thực nghiệm')]
B += [table([
    ['Hạng mục', 'Lựa chọn'],
    ['Thiết kế', 'ALU 8 bit, 8 phép (ADD, SUB, AND, OR, XOR, SHL, SHR, SLT), pipeline 2 tầng, cờ `zero`, `carry`, `valid`'],
    ['Biến thể CG', 'Module `icg`. Khi mô phỏng là latch nối cổng AND; khi làm ASIC là cell `sky130_fd_sc_hd__dlclkp`'],
    ['Sim', 'Verilator 5.022. Testbench tự so kết quả với một mô hình tham chiếu'],
    ['FPGA', 'Vivado 2025.1, chip `xc7a100tcsg324-1` (board Arty A7-100T), 100 MHz. Vỏ `fpga_top` tự sinh dữ liệu vào bằng LFSR và tự kiểm kết quả bằng MISR'],
    ['ASIC', 'Yosys 0.68 và OpenROAD qua ORFS, thư viện sky130hd, chu kỳ clock 10 ns, đi từ RTL tới GDS'],
    ['Agent', 'Claude Code (Opus) chạy ở khối `claude`, chỉ sửa bản chép trong thư mục của vòng'],
], [2000, 7026])]
B += [para('Baseline dùng thiết lập gần mặc định. ORFS lấy `CORE_UTILIZATION` 40 và `PLACE_DENSITY` 0,65 giống ví dụ `gcd` '
           'đi kèm ORFS; Vivado dùng directive mặc định. Chạy baseline hai lần cho ra đúng từng con số ở cả ba bước, nghĩa là '
           'các công cụ cho kết quả tất định. Vì vậy ngưỡng so sánh ở pha 2 chỉ cần đủ để bỏ qua sai khác do làm tròn.')]
B += [para('Các chỉ số dùng trong báo cáo:')]
B += [table([
    ['Chỉ số', 'Ý nghĩa'],
    ['WNS / WHS', 'Khoảng thời gian còn dư trên đường chậm nhất (setup) và nhanh nhất (hold). Số dương là đạt timing; càng lớn càng dư'],
    ['LUT, FF', 'Số ô logic và số flip-flop thiết kế dùng trên FPGA'],
    ['Công suất động', 'Phần công suất do tín hiệu đảo trạng thái, khác với công suất tĩnh luôn tiêu thụ'],
    ['Diện tích cell / die', 'Cell là tổng diện tích các cell chuẩn. Die là diện tích cả chip, gồm cell, chỗ trống và lề. Util là tỉ lệ diện tích cell trên vùng đặt cell'],
    ['DRC', 'Số lỗi luật thiết kế của layout. Phải bằng 0'],
    ['Gate', 'Điều kiện tối thiểu một bước phải đạt (ví dụ mô phỏng PASS) thì kết quả mới được xét tiếp'],
], [2200, 6826])]

B += [h2('2.3. Vòng lặp agent')]
B += [para('Agent làm lần lượt Sim, FPGA rồi ASIC. Mỗi bước có tối đa 2 vòng, và mỗi vòng diễn ra như sau:')]
B += [bullet('CHIA chép bản tốt nhất hiện có sang một thư mục riêng của vòng.'),
      bullet('Agent đọc báo cáo của bản tốt nhất cùng lịch sử các vòng trước, rồi sửa những file được phép. Khối `claude` không có '
             'Verilator, Vivado hay ORFS, nên agent không tự build hay đo được.'),
      bullet('CHIA hoàn lại mọi sửa đổi ngoài phạm vi cho phép và kiểm luật: không được đổi chip FPGA, clock, thiết kế, file nguồn hay file SDC.'),
      bullet('CHIA chạy lại bước đó bằng công cụ thật. Kết quả phải qua gate, rồi được so với bản tốt nhất theo luật nhận ở bảng dưới.')]
B += [para('Vòng được nhận trở thành bản tốt nhất cho vòng sau và cho bước sau. Vòng bị loại không thay đổi gì, nhưng lý do '
           'bị loại được đưa vào lời nhắc của agent ở vòng kế tiếp.')]
B += [table([
    ['Bước', 'Agent được sửa', 'Gate', 'Luật nhận'],
    ['Sim', '`sim/tb_<DUT>.v`', 'Mô phỏng in `RESULT: PASS`', 'Số lần kiểm tra (`checked`) tăng'],
    ['FPGA', '`build.tcl`, `sources.f`, file `.v` mới trong `fpga/`', 'Mô phỏng `tb_fpga_top` PASS, có bitstream, WNS ≥ 0',
     'Không kém hơn ở cả WNS, công suất động và LUT+FF; tốt hơn ở ít nhất một (Pareto chặt)'],
    ['ASIC', '`asic/config.mk`', 'Có GDS, slack setup và hold ≥ 0, DRC = 0',
     'Diện tích cell, diện tích die, công suất không kém quá 1 %; ít nhất một chỉ số tốt hơn quá 1 %'],
], [900, 2400, 2526, 3200])]
B += [para('Agent không được sửa thư mục `rtl/` và các file ràng buộc `.xdc`, `.sdc`. Muốn tối ưu riêng cho FPGA, agent phải '
           'viết file mới trong `fpga/`. Nhờ vậy thay đổi dành cho FPGA không lọt sang nhánh ASIC.')]

B += [h2('2.4. Kết quả baseline')]
B += [table([
    ['Bước', 'Chỉ số', 'NoCG', 'CG'],
    ['Sim', 'Kết quả, số lần kiểm tra', 'PASS, 6 272', 'PASS, 6 272'],
    ['FPGA', 'LUT / FF', '138 / 139', '140 / 141'],
    ['', 'WNS / WHS (ns)', '4,579 / 0,024', '1,502 / 0,052'],
    ['', 'Công suất tổng / động (W)', '0,099 / 0,002', '0,102 / 0,005'],
    ['ASIC', 'Diện tích cell (µm²), util', '3 078, 44,8 %', '2 975, 46,9 %'],
    ['', 'Diện tích die (µm²)', '7 534', '6 926'],
    ['', 'Số cell (flop, ICG, buffer clock)', '356 (31, 0, 9)', '306 (31, 2, 18)'],
    ['', 'Setup / hold slack (ns)', '6,815 / 0,493', '6,957 / 0,406'],
    ['', 'Công suất (mW), DRC', '0,368, 0', '0,324, 0'],
], [900, 3326, 2400, 2400])]
B += [para('Cùng một RTL, clock gating cho kết quả ngược nhau trên hai nền tảng.')]
B += [para('Trên FPGA, CG kém hơn NoCG. Ở CG, clock phải đi qua latch và một LUT trước khi tới thanh ghi, nên đường găng chỉ còn '
           'nửa chu kỳ: WNS giảm từ 4,579 xuống 1,502 ns. Công suất động tăng từ 2 lên 5 mW, và Vivado cảnh báo gated clock.')]
B += [para('Trên ASIC, CG tốt hơn. Cell ICG thay cho mux hồi tiếp của NoCG, nên thiết kế ít hơn 50 cell, diện tích cell giảm 3 % '
           'và công suất giảm 12 %. Đổi lại, số buffer clock tăng gấp đôi, từ 9 lên 18. Bước CTS của OpenROAD xử lý đúng cell ICG.')]
B += [para('Vì kết quả ngược nhau như vậy, tối ưu cho FPGA không được mang sang ASIC (luật ở mục 2.3). Mỗi demo chạy baseline '
           'mất khoảng 64 s cho FPGA và 2,5 phút cho ASIC.')]
B += [figure('[Chèn hình: timeline CHIA của một lần chạy baseline, profile/ChiaProfileCollector_profile.html]')]
B += [caption('Hình 1. Timeline CHIA: các task Sim, FPGA, ASIC chạy trên từng khối của cluster')]

B += [h2('2.5. Kết quả agent so với baseline')]
B += [para('Cả hai demo chạy `agent.py --rounds 2` (NoCG `agent-20261121-141430`, CG `agent-20261121-143619`) và kết thúc với '
           '`KẾT QUẢ: OK`. Ở cả hai demo, các vòng được nhận là `sim-r1`, `sim-r2`, `asic-r1`, `asic-r2`; mọi vòng FPGA bị loại. '
           'Mỗi demo chạy khoảng 22 phút.')]
B += [table([
    ['Chỉ số', 'NoCG: baseline → agent', 'CG: baseline → agent'],
    ['Sim: số lần kiểm tra (vẫn PASS)', '6 272 → 542 046 (×86)', '6 272 → 1 509 090 (×241)'],
    ['FPGA', '2 vòng đều bị loại, giữ baseline', '2 vòng đều bị loại, giữ baseline'],
    ['ASIC: diện tích cell (µm²)', '3 077,95 → 3 021,65 (−1,8 %)', '2 975,35 → 2 835,22 (−4,7 %)'],
    ['ASIC: diện tích die (µm²)', '7 534,24 → 4 641,70 (**−38,4 %**)', '6 925,57 → 5 504,90 (**−20,5 %**)'],
    ['ASIC: công suất (mW)', '0,368 → 0,361 (−1,8 %)', '0,324 → 0,302 (**−6,8 %**)'],
    ['ASIC: setup slack (ns), DRC', '6,815 → 7,034, 0', '6,957 → 7,042, 0'],
], [3026, 3000, 3000])]

B += [h3('Sim')]
B += [para('Ở vòng 1, agent viết thêm các trường hợp testbench gốc chưa kiểm: `valid` phải lên đúng 2 chu kỳ sau khi có dữ liệu '
           'vào, các giá trị biên (7F/80, FF/00), phép dịch khi `b[7:3] ≠ 0`, các kiểu bật tắt `en`, và reset giữa chừng. Ở vòng 2, '
           'agent kiểm hết mọi cặp toán hạng 256 × 256 cho ADD, SUB và SLT. Riêng ở CG, agent thêm phép đếm cạnh clock sau ICG '
           'ở mỗi chu kỳ để chắc rằng latch không sinh xung clock giả. Mô phỏng vẫn PASS, còn số lần kiểm tra tăng 86 lần (NoCG) '
           'và 241 lần (CG).')]

B += [h3('FPGA')]
B += [para('Agent đưa ra bốn đề xuất, hai vòng cho mỗi demo. Đề xuất nào cũng cải thiện được một chỉ số nhưng làm kém đi một chỉ '
           'số khác, nên theo luật Pareto chặt cả bốn đều bị loại và baseline được giữ nguyên.')]
B += [table([
    ['Vòng', 'Agent làm gì', 'Kết quả đo thật'],
    ['NoCG r1', 'Retiming, ánh xạ tay LUT6_2/CARRY4, directive `Explore`', 'WNS 4,579 → 4,94 ns; công suất động 2 → 5 mW; LUT+FF 277 → 302'],
    ['NoCG r2', 'Giữ thanh ghi vào, ADD/SUB/SLT dùng chung một bộ cộng', 'LUT+FF 277 → 262; WNS 4,579 → 4,212 ns'],
    ['CG r1', 'Biến ICG thành cổng AND và bật `-gated_clock_conversion auto`, để Vivado đổi clock gating thành tín hiệu enable (CE) của FF', 'WNS 1,502 → 4,499 ns; công suất động 5 → 2 mW; LUT+FF 281 → 284'],
    ['CG r2', 'Viết ALU riêng cho FPGA dùng CE, không có latch trên clock, thêm retiming', 'WNS 1,502 → 4,436 ns; LUT+FF 281 → 312'],
], [1100, 4000, 3926])]
B += [para('Trước khi nộp, agent tự kiểm logic bằng mô hình Python: vét cạn 524 288 tổ hợp phép × a × b, không có lỗi. Logic đúng, '
           'nhưng dự đoán của agent về số tài nguyên vẫn sai; chỉ khi Vivado đo thật mới biết. Agent cũng học từ vòng trước: '
           'ở NoCG r2, agent đọc lý do r1 bị loại (công suất động tăng) và quay lại giữ thanh ghi vào.')]
B += [para('Đáng chú ý nhất là CG r1. Agent nhận ra đúng vấn đề của clock gating trên FPGA và sửa nó: WNS tăng gấp 3 lần '
           '(1,502 lên 4,499 ns), công suất động giảm 60 % (5 xuống 2 mW). Cái giá là thêm 3 ô LUT+FF (281 lên 284), nên vòng này '
           'vẫn bị loại. Luật nhận đã chốt trước khi chạy và được giữ nguyên, không nới ra sau khi thấy kết quả.')]

B += [h3('ASIC')]
B += [para('Ở bước ASIC, agent chỉ được sửa `config.mk` của ORFS:')]
B += [table([
    ['Vòng', 'NoCG', 'CG'],
    ['r1', '`ABC_AREA=1`, `CORE_UTILIZATION` 40 → 55, `PLACE_DENSITY` 0,65 → 0,75: die −27,5 %',
     'Chỉ `ABC_AREA=1`, vì lần route trước của CG phải lặp 6 vòng mới hết lỗi DRC: công suất −7,1 %'],
    ['r2', '`CORE_UTILIZATION` 55 → 65, `PLACE_DENSITY` 0,75 → 0,85: die giảm thêm 15 %',
     '`CORE_UTILIZATION` 40 → 50, `DONT_BUFFER_PORTS=1`: diện tích cell −4 %, die −19,5 %'],
], [900, 4063, 4063])]
B += [para('`ABC_AREA=1` cho Yosys tổng hợp theo diện tích thay vì theo tốc độ, vì timing còn dư nhiều. `CORE_UTILIZATION` và '
           '`PLACE_DENSITY` cao hơn thì cell được xếp sát nhau hơn, nên die nhỏ lại. Cả bốn vòng đều qua gate, setup slack còn tăng '
           'nhẹ. Số cell tăng 26 đến 31 % vì tổng hợp theo diện tích chọn nhiều cell nhỏ hơn. Agent cũng chỉ ra giới hạn của bước '
           'này: ở NoCG, flip-flop và cây clock chiếm khoảng 70 % công suất, và phần này do RTL quyết định, `config.mk` không đổi được.')]
B += [figure('[Chèn hình: layout GDS NoCG và CG, baseline (trái) và agent (phải), asic/layout.png của mỗi lần chạy]')]
B += [caption('Hình 2. Layout ASIC trước và sau agent')]

B += [h2('2.6. Kết luận')]
B += [bullet('Flow chạy trọn và đúng: CHIA điều phối Sim (Verilator), FPGA (Vivado, ra bitstream) và ASIC (Yosys và OpenROAD, ra GDS) '
             'trên năm khối, bước nào cũng có gate.'),
      bullet('Agent đóng góp được ở cả ba bước: số lần kiểm tra Sim tăng 86 đến 241 lần, die ASIC giảm 20 đến 38 %, công suất CG giảm '
             '6,8 %. Ở FPGA không vòng nào được nhận, nhưng agent tìm đúng vấn đề clock gating.'),
      bullet('Phải đo bằng công cụ thật: dự đoán của agent có lúc sai, và vòng "đề xuất, đo, nhận hoặc loại" bảo đảm kết quả cuối '
             'không bao giờ kém baseline.'),
      bullet('FPGA và ASIC cần tách riêng: clock gating có lợi trên ASIC nhưng có hại trên FPGA. Vì `rtl/` cố định, sửa riêng cho FPGA '
             'không ảnh hưởng ASIC.')]
B += [h2('2.7. Hạn chế')]
B += [bullet('Thiết kế rất nhỏ (khoảng 300 cell) và RTL cố định, nên ít chỗ để tối ưu.'),
      bullet('Thước đo của Sim chỉ đếm số phép so sánh (`checked`), không đo độ phủ. Chưa có mô phỏng mức cổng hay kiểm LVS sau khi agent đổi cấu hình ASIC.'),
      bullet('Công suất chỉ là ước lượng: OpenSTA dùng hoạt động mặc định, Vivado ước lượng không có vector kích thích.'),
      bullet('Mỗi vòng chỉ lấy một lần trả lời của agent. Chưa nạp lên board; nhánh FPGA dừng ở bitstream.')]

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
