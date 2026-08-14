# Phương pháp dữ liệu tài chính

## Nguyên tắc bắt buộc

1. Không dùng mô hình AI để tạo số liệu tài chính còn thiếu.
2. Mỗi cơ cấu sản phẩm phải có kỳ báo cáo và nguồn công bố của doanh nghiệp.
3. Không áp tỷ trọng của một năm vào doanh thu TTM hoặc kỳ khác.
4. Số âm được giữ nguyên. Donut chỉ biểu diễn các thành phần dương và phải liệt kê riêng thành phần âm.
5. Thiếu dữ liệu được biểu diễn là `N/A`, không thay bằng 0.

## Vai trò từng nguồn

- DNSE: giá gần nhất, giao dịch và dữ liệu thị trường realtime.
- Vietcap public REST: báo cáo tài chính quý/năm chuẩn hóa.
- vnstock: nguồn dự phòng khi nguồn BCTC chính không hoạt động.
- Website IR của doanh nghiệp: cơ cấu sản phẩm, kênh bán, địa lý và các thuyết minh đặc thù.
- DeepSeek: chỉ diễn giải sau thao tác chủ động của người dùng; không tham gia tạo số liệu mặc định.

## Hợp đồng biểu đồ diễn biến

- Chỉ tiêu `flow`: giá trị phát sinh trong kỳ, gồm doanh thu, lợi nhuận và CFO.
- Chỉ tiêu `stock`: số dư cuối kỳ, gồm tồn kho, phải thu, dư nợ và vốn chủ sở hữu.
- Theo quý: YoY so với đúng quý cùng kỳ năm trước.
- Theo năm: lấy trực tiếp báo cáo năm và so với năm trước.
- CFO chỉ được đọc từ báo cáo lưu chuyển tiền tệ.

## Hợp đồng cơ cấu doanh thu

`issuer_business_disclosure` là cơ cấu sản phẩm/kênh do doanh nghiệp công bố.
Mỗi hồ sơ cần có:

- Tổng doanh thu và kỳ tương ứng.
- Các phân khúc loại trừ lẫn nhau hoặc ghi rõ chiều phân loại.
- URL, nhà xuất bản, ngày công bố và căn cứ trích xuất.
- Kiểm tra tổng tỷ trọng bằng 100% trong sai số cho phép.
- Giới hạn công bố, ví dụ không có tỷ trọng kim cương hoặc độ tinh khiết vàng.

`accounting_income_breakdown` chỉ áp dụng cho ngân hàng và chứng khoán, nơi các dòng thu nhập trên BCTC có ý nghĩa phân tích hoạt động. Nó không được gọi là cơ cấu sản phẩm.

`not_disclosed` là trạng thái chuẩn cho doanh nghiệp chưa có hồ sơ công bố được kiểm chứng. Giao diện phải ẩn biểu đồ thay vì suy đoán.

## Mở rộng độ phủ

Hồ sơ công bố được bổ sung có kiểm duyệt trong `COMPANY_DISCLOSURES` của `revenue_structure_engine.py`. Trước khi thêm mã mới, đối chiếu tổng phân khúc với tổng doanh thu cùng kỳ và kiểm tra các chiều phân loại không bị chồng lấn.

## Hợp đồng dữ liệu LP-RRG

- Lịch sử ngày đi theo chuỗi Vietcap → KBS → bản PostgreSQL đã kiểm định; không dùng tìm kiếm MSN không kèm sàn.
- DNSE chỉ bổ sung giá realtime, không được tạo chuỗi OHLC lịch sử từ một điểm giá.
- Mã đủ chuẩn phải có ít nhất 252 phiên thật khớp benchmark. Mã mới được hiển thị tiến độ nhưng không có tọa độ giả.
- Bản PostgreSQL tốt gần nhất chỉ được phục vụ tối đa ba phiên benchmark và phải gắn trạng thái `stale_valid`.
- Dataset chỉ được trả khi coverage của mã đủ chuẩn bằng 100%; nếu không, API trả `503 data_incomplete`.
- Khởi tạo/backfill bằng `python rrg_sync.py backfill`; kiểm tra độ phủ bằng `python rrg_sync.py audit` sau khi cấu hình `DATABASE_URL`.

## Hợp đồng dữ liệu Corporate Calendar

- Calendar chỉ hiển thị dữ kiện quan sát được từ nguồn. Trường thiếu phải là `null`/`N/A`; không thay bằng 0, không dùng AI hoặc heuristic để tạo ngày, giờ, tỷ lệ hay địa điểm.
- Ngày công bố, ngày GDKHQ, ngày đăng ký cuối cùng, ngày thanh toán, ngày họp, ngày phát hành và ngày giao dịch đầu tiên là các occurrence độc lập. Không đổi tên một loại ngày thành loại khác để lấp dữ liệu.
- `displayDate` của nhà cung cấp chỉ được gắn vai trò `provider_display`/“Ngày theo nguồn” khi không có trường ngày mang ngữ nghĩa rõ ràng. Nó không mặc nhiên là GDKHQ hoặc ngày họp.
- Chỉ gắn giờ khi nguồn trả timestamp có giờ. Sự kiện chỉ có ngày phải xuất ICS dạng all-day bằng `VALUE=DATE`.
- Công bố BCTC/KQKD dùng whitelist xác định và phải gắn được mã chứng khoán từ trường ticker hoặc tiền tố tiêu đề. Tin không khớp phải bị loại và được tính trong `rejected_items`, không được mặc định thành BCTC.
- Mỗi occurrence phải có evidence gồm nguồn, cấp nguồn, raw ID, URL chứng từ nếu có, thời điểm công bố và thời điểm quan sát. Không có URL chứng từ thì không được gắn `source_verified=true`.
- Nguồn chính thức của VSDC/HOSE/HNX/SSC hoặc doanh nghiệp được ưu tiên hơn nguồn tổng hợp. Bất đồng cùng cấp phải gắn `conflict`, liệt kê trường bất đồng và không chọn giá trị âm thầm.
- Coverage là số mã/trang/nguồn đã quét thành công, không phải tuyên bố dữ liệu đầy đủ tuyệt đối. Cache cũ hoặc partial phải hiển thị `stale`/`partial` cùng thời điểm last-known-good.
- Refresh không được xóa last-known-good khi nguồn lỗi hoặc trả thiếu trang. Dataset mới chỉ được promote sau khi vượt qua kiểm tra schema, pagination và data-quality gate.

## Hợp đồng dữ liệu Macro v2

- Phạm vi sự kiện hiện tại chỉ là kinh tế Mỹ. Các lựa chọn quốc gia chưa có pipeline kiểm chứng không được hiển thị như chức năng khả dụng.
- Thứ tự nguồn là cơ quan công bố (BLS, BEA, Federal Reserve) → FRED → FairEconomy/ForexFactory cho lịch dự phòng. Nguồn tổng hợp không được cung cấp `actual` hoặc ghi đè nguồn chính thức.
- `forecast` luôn là `null` và không hiển thị trong giao diện. Thiếu `actual`, kỳ trước, giờ hoặc kỳ tham chiếu phải là `null`/`N/A`; không dùng AI, hash, random hoặc heuristic để tạo giá trị.
- Mỗi observation phải khớp `indicator_key`, series, phép biến đổi và `reference_period`. Không lấy observation mới nhất gắn cho một sự kiện lịch sử chưa xác định được kỳ.
- CPI/PCE dùng phần trăm thay đổi từ index liên tiếp; payroll dùng chênh lệch mức việc làm; GDP và lãi suất dùng đúng series đã công bố. Mọi phép biến đổi được khai báo trong indicator registry và có unit test.
- `change_vs_previous` chỉ mô tả `up`, `down` hoặc `flat`; không mang nghĩa tốt/xấu. Phần diễn giải tác động phải trình bày theo điều kiện và bối cảnh nhiều chỉ báo.
- Event bắt buộc có source, URL nếu có, observed/as-of time, verification và stale state.
- Dataset chỉ được promote nếu không rỗng và coverage không giảm quá quality gate. Nguồn lỗi giữ dữ liệu nguồn đó từ last-known-good và gắn trạng thái partial/stale.
- Production dùng PostgreSQL qua `DATABASE_URL`; SQLite chỉ dành cho local/test. Refresh chạy single-flight và không giữ HTTP request chờ nguồn ngoài.
- Backfill/refresh bằng `python macro_sync.py backfill`; kiểm tra bằng `python macro_sync.py audit`. Audit thất bại nếu có actual không kèm evidence chính thức.

## Hợp đồng Market Ribbon VN30

- Ribbon chỉ gồm `VNINDEX`, `VN30` và đúng 30 thành phần VN30 đã được KBS và VCI đối chiếu; không hard-code danh sách.
- Giá cổ phiếu dùng cùng snapshot bảng giá Vietcap với Heatmap/Bubbles. Biến động phiên là `(giá khớp / giá tham chiếu - 1) × 100`.
- Giá hai chỉ số lấy trực tiếp từ OHLC intraday Vietcap và so với close phiên trước; không tự tổng hợp chỉ số từ giá cổ phiếu.
- Trong phiên, client tải snapshot mỗi 10 giây và server dùng cache/single-flight. Ngoài phiên giữ close snapshot gần nhất, không gọi upstream theo chu kỳ 10 giây.
- Giá thiếu, bằng 0, membership thiếu hoặc response rỗng không được thay thế dataset tốt; dùng last-known-good kèm `stale`, hoặc `null` nếu chưa từng có giá hợp lệ.
