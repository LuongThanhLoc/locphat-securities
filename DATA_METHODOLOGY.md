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
