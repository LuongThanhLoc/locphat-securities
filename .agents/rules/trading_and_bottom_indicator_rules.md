# Quy Tắc Định Lượng & Triết Lý Giao Dịch — Chỉ Báo Đáy & Dòng Tiền Lộc Phát Securities

Các quy tắc dưới đây được đúc kết trực tiếp từ yêu cầu của User và là kim chỉ nam bắt buộc phải tuân thủ trong toàn bộ hệ thống code và giao diện:

---

## 1. Triết Lý Tạo Lập vs Đám Đông ("Cáo" vs "Gà")
- **Nguyên lý bất biến:** Bigboys (Dòng tiền tạo lập) là "Cáo" kiến tạo cuộc chơi; Đám đông (Nhỏ lẻ) là "Gà". **Tuyệt đối KHÔNG BAO GIỜ** kết luận dòng tiền lớn và đám đông "cùng suy nghĩ" hay "ở trạng thái cân bằng thăm dò".
- **Tên gọi chuẩn mực:**
  - `Smart Money Start` (Pulse / Flow / Core): Đo lường hành vi gom / xả thực tế của **Bigboys (Cáo)**.
  - `Market Emotion Index` (Nhiệt kế thị trường): Đo lường tâm lý hoảng loạn / tham lam của **Đám đông (Gà)**.
  - `Disparity Score` (Độ lệch pha): $\text{Smart Money} - \text{Market Emotion}$. Độ lệch pha dương lớn báo hiệu Bigboys đang âm thầm gom hàng khi đám đông hoảng loạn.

---

## 2. Tuân Thủ Pháp Lý Chứng Khoán (Compliance & Legal Safety)
- **Tuyệt đối KHÔNG hiển thị khuyến nghị mua/bán, giá mục tiêu cụ thể, giá cắt lỗ hay tỷ trọng giải ngân trực tiếp trên giao diện Frontend.**
- Việc đưa các box khuyến nghị giao dịch cụ thể lên UI dễ bị cơ quan quản lý (UBCKNN) xem là hành vi tư vấn đầu tư chứng khoán không giấy phép.
- Giao diện người dùng tập trung vào vai trò **Nghiên cứu Định lượng, Trực quan hóa Dòng tiền và Nhiệt kế Thị trường khách quan** để nhà đầu tư tự ra quyết định.

---

## 3. Bộ Lọc Chống Bán Tại Vùng Đáy (Anti-Bottom-Sell Filter)
- **Nguyên tắc:** Không bao giờ để hệ thống phát tín hiệu Bán (**BS**) tại vùng đáy chiết khấu / hoảng loạn rũ bỏ (tránh tình trạng bán ngay đáy cho tạo lập gom).
- **Quy tắc cấm phát BS khi:**
  - $Aperture \le 38$ hoặc $RSI_{14} \le 42$ (vùng hoảng loạn / quá bán).
  - Độ giãn giá $(Close - EMA20)/ATR_{14} \le -1.2$ (giá đã rơi quá xa đường trung bình).
  - Giá nằm gần đáy lớn 50 phiên ($\le 6\%$).
  - Xuất hiện nến rút chân gom hàng $CLV \ge 0.35$ (Stopping Volume / Spring).
  - Độ lệch pha $Disparity \ge +10$.

---

## 4. Bộ Lọc Chống Bắt Dao Rơi (Anti-Falling-Knife Filter) & Đáy Xác Thực
- **Nguyên tắc:** **Đáy là tín hiệu lớn, hiếm hoi và có tính bước ngoặt của một cổ phiếu.** Tuyệt đối không được phát tín hiệu Đáy (`BOTTOM_WATCH`) hay Mua (`BB`) tràn lan khi cổ phiếu đang rơi thác đổ.
- **Nhận diện thác đổ (`is_falling_knife_regime`):**
  - $EMA20$ dốc xuống mạnh ($EMA20 < EMA20_{t-5} \times 0.988$).
  - Giá nằm sâu dưới $EMA20$ và $EMA50$.
  - $Pulse < Flow$ và chưa có phân kỳ tăng.
  $\rightarrow$ **CẤM phát tín hiệu Đáy hoặc BB trong giai đoạn này.**
- **Tiêu chuẩn Đáy Xác Thực:**
  1. Có Lực Đỡ Hấp Thụ: Phân kỳ tăng kép (RSI + Smart Money) HOẶC Wyckoff Spring cạn cung HOẶC Stopping Volume với $Pulse > Flow$.
  2. Nền giá giữ vững hỗ trợ ít nhất 5 phiên ($Close \ge Low_{min, 5} \times 1.005$).
  3. **Chỉ vẽ Marker Đáy đúng 1 LẦN** tại phiên chuyển pha xác nhận (`is_event`), không spam nhiều phiên liên tiếp.

---

## 5. Tích Hợp RSI(14) & Hệ Thống Phân Kỳ Kép (Dual Divergence)
- **Hiển thị trực quan:** Cung cấp thẻ số liệu RSI(14) động trên Header Metric Grid, đồng bộ realtime khi rê chuột qua từng phiên nến.
- **Phân kỳ kép (Dual Bullish / Bearish Divergence):**
  - Khi **CẢ RSI VÀ Smart Money Pulse** cùng tạo phân kỳ với đường giá $\rightarrow$ Đánh dấu là siêu tín hiệu đảo chiều với độ tin cậy cao nhất.
  - Gắn nhãn trực quan `● Phân kỳ tăng kép (RSI + Smart Money)` màu xanh lục sáng trên UI.
